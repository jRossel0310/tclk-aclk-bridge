# RedisAdapter Protocol v1.0 alignment (KR260 publisher)

Date: 2026-07-19
Status: approved, ready for implementation plan

## Goal

Make the board-side Redis event convention conform to the Fermilab **RedisAdapter
Protocol Specification v1.0** (`../redis-adapter/docs/redis-adapter-implementation-spec.md`,
repo `fermi-ad/redis-adapter`) so that a generic RedisAdapter consumer can read our
TCLK/ACLK event streams with no producer-specific code. This is the convention the lab
uses; we want to match it.

Today's KR260 convention (see `deploy/redis.md`) diverges from RA v1.0 on all three
core-compliance points: unbraced keys, `<ms>-*` server-sequenced stream IDs, and
human-readable text fields with no `_` binary payload.

## Scope

Clean cutover (no dual-publish period). Streams are ephemeral (Redis persistence off,
in-memory, empty on restart) and every consumer is ours, so nothing external breaks.

In scope:
- `deploy/redis_publish.py` - key builders, record construction, `_` packing.
- `deploy/redis_sink.py` - stream-ID encoding + ns-resolution monotonic guard.
- `deploy/stream_archive.py` - braced key construction (keeps reading text fields).
- New `deploy/ra_consumer.py` - reusable RA-compliant decode helper (reference code).
- New `deploy/test_ra_roundtrip.py` - hermetic self-hosting round-trip self-test.
- Existing tests: `test_redis_sink.py`, `test_redis_publish.py`, `test_stream_archive.py`.
- Docs: `deploy/redis.md`, `deploy/capture.md`, `docs/OPERATIONS.md`, and the generated
  hardware guide reference to the key layout.

Out of scope: changing the hardware readout, the WR timebase, the FIFO drain path, the
per-code index-hash aggregation strategy, or the stats/archive CSV formats.

## Design

### 1. Key schema (`{baseKey}:subKey`)

RA v1.0 requires literal curly braces on the base key for Redis Cluster hash tagging.
Braces go into the key-builder **helpers**, not into the `--namespace` value, so
`--namespace KR260` stays natural and every key on the same base lands in one hash slot.

| Purpose | Old key | New key |
| --- | --- | --- |
| Event stream | `KR260:tclk` | `{KR260}:tclk` |
| Per-code index hash | `KR260:event:tclk:0x1D` | `{KR260}:event:tclk:0x1D` |
| Liveness (sticky) | `KR260:status` | `{KR260}:status` |
| Liveness (TTL) | `KR260:watchdog` | `{KR260}:watchdog` |

The index hash, status, and watchdog are non-core (diagnostics/extension) under RA, but
bracing them keeps them co-located with the stream and is harmless.

Helper changes in `redis_publish.py`:
- `_stream_key(ns, src)` -> `"{%s}:%s" % (ns, src)`
- `_index_key(ns, src, event)` -> `"{%s}:event:%s:0x%02X" % (ns, src, event)`
- status/watchdog keys -> `"{%s}:status" % ns`, `"{%s}:watchdog" % ns`
- `stream_archive.py` stream builder -> `"{%s}:%s" % (namespace, src)`

### 2. Stream ID = full `RA_Time`

`RA_Time` is nanoseconds since the Unix epoch. From the WR split `(sec, ns)`:

```
RA_Time = sec * 1_000_000_000 + ns
id      = "%d-%d" % (RA_Time // 1_000_000, RA_Time % 1_000_000)   # <ms>-<ns_within_ms>
```

This replaces `id="%d-*" % ms` (server-picked sequence). Because the ID is now explicit
and complete, the Redis >= 7.0 requirement goes away (`-*` was the only 7.0 feature we
used); the encoding works on Redis 6 too.

**Monotonic guard at ns resolution.** Redis requires each ID strictly greater than the
stream's current top. The sink tracks the last `RA_Time` per stream (replacing today's
`_last_ms`):

```
if ra_time <= last: ra_time = last + 1      # documented adjust-policy, RA-permitted
last = ra_time
ms, seq = divmod(ra_time, 1_000_000)        # recompute after any bump; seq stays < 1e6
```

A `+1 ns` bump naturally rolls into the next millisecond when `seq` would hit 1e6, so
recomputing ms/seq from the (possibly bumped) `RA_Time` is always valid. A backward WR
re-arm or same-ns burst clusters a few entries within <1 us of the boundary; the exact
`sec`/`ns` still live in `_` and the text fields.

### 3. `_` binary payload

Add a mandatory `_` field: one little-endian packed struct, format `<IIIHB` (15 bytes):

| offset | field | type | notes |
| --- | --- | --- | --- |
| 0 | sec | uint32 LE | WR seconds since Unix epoch (valid to 2106) |
| 4 | ns | uint32 LE | 0..999,999,999 within-second |
| 8 | data | uint32 LE | event data word |
| 12 | event | uint16 LE | event code |
| 14 | flags | uint8 | bit0 `has_data`, bit1 `is_tclk` |

Packed once per event in `event_fields()` via `struct.pack("<IIIHB", ...)`. This struct
is the producer/consumer "device contract" the RA spec keeps out-of-band; it is
documented in `redis.md` and the hardware guide.

The existing readable string fields (`sec, ns, event, data, is_tclk, has_data, src`) stay
as **additional** fields, so `stream_archive.py`, `plot_stats.py`, and `redis-cli`
inspection keep working unchanged. RA consumers read `_`; we keep the text extras. RA
compliance requires consumers to ignore unknown fields, so this is legal.

`src` stays text-only (it equals the stream name, no value in the struct).

### 4. Self-test (`deploy/test_ra_roundtrip.py`)

Hermetic integration test proving the publisher emits RA v1.0-compliant entries and that
the same producer can target any Redis (the "self-test now, lab Redis later" requirement).

- **Own Redis.** Spawn a private `redis-server` on an ephemeral port with a temp dir and
  minimal config; tear it down in teardown. If no `redis-server` binary is on PATH, skip
  with a clear message (Windows dev box has none; the board and Linux CI do).
- **Producer = real code path.** Feed synthetic decoded events through `build_record()` +
  `RedisSink` against the private Redis, bypassing only the `/dev/uio` hardware drain
  (which cannot run off-board). This exercises the actual key builders, ns-guard, and `_`
  packing we ship.
- **Consumer = RA-compliant reader** (`ra_consumer.py`). Uses ONLY the three core
  protocol pieces from the spec's compliance checklist: the `{KR260}:tclk` key schema,
  the stream ID (`ms-seq` -> `RA_Time = ms*1e6 + seq`), and the `_` field (unpack
  `<IIIHB`). It deliberately ignores the text fields, proving a generic RA consumer
  recovers the primary value.
- **Assertions.**
  - `RA_Time` recovered from each stream ID equals the input `sec*1e9+ns`, except entries
    the guard bumped, which must stay within the expected small window.
  - Unpacked `_` matches the input `event`/`data`/`flags`/`sec`/`ns`.
  - Braced stream key, index hash (`{KR260}:event:tclk:0x1D`), `{KR260}:status`, and
    `{KR260}:watchdog` all exist.

`ra_consumer.py` is a small standalone helper (parse ID -> RA_Time; unpack `_`) that the
test imports and that doubles as reference code for a lab-side RA consumer.

### 5. Streaming to a different Redis server

Already supported: `redis_publish.py` takes `--redis-host` (default `127.0.0.1`) and
`--redis-port` (default `6379`). Pointing at a lab Redis is
`sudo python3 redis_publish.py /dev/uio4 --src tclk --redis-host <lab-host> --redis-port <port>`.
Add a one-line example to `redis.md`; no code change.

## Testing

- `test_ra_roundtrip.py` - the end-to-end self-test above (real redis-server).
- `test_redis_sink.py` - update for full-`RA_Time` IDs + ns-guard; add a same-ns and a
  backward-jump case asserting the +1 ns bump.
- `test_redis_publish.py` - update expected braced keys and the `_` field presence/layout.
- `test_stream_archive.py` - update expected braced keys.

## Compliance check (against spec section 10)

Producer: (1) Streams via XADD - yes. (2) `{baseKey}:subKey` schema - yes (section 1).
(3) Explicit `RA_Time` IDs - yes (section 2). (4) Primary payload in `_` - yes
(section 3). (5) Binary integrity - little-endian struct, documented. (6) Intentional
retention - maxlen cap documented. (7) No tight retry / no fabricated timestamps -
unchanged (existing backoff + UNSYNC drop).

## Risks / notes

- `sec` as uint32 in `_` is valid until 2106; acceptable and 4 bytes cheaper than u64.
- The ns-guard changes IDs only under collision/backward-jump, same conditions as today's
  ms-guard, just finer; documented policy per RA section 7.3.
- Cutover means old `KR260:` (unbraced) and new `{KR260}:` keys never coexist; since Redis
  is wiped on restart, deploy = restart redis-server + relaunch publishers.
