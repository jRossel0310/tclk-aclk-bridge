# Redis Convention Alignment Design

Date: 2026-07-07
Status: approved (brainstorm complete)
Scope: board-side Python + a Redis config file (`deploy/`). Evolves the still-unmerged
`redis-publish` work. No RTL, no bitstream change.

## 1. Problem and goal

Our KR260 event publisher (`deploy/redis_publish.py` + `deploy/redis_sink.py`, branch
`redis-publish`) writes events into two plain Redis Streams (`events:tclk`,
`events:aclk`). The Fermilab reference system (`redis-clock-server`, the
`tclk-redis-adapter`) uses a different set of conventions: an app-name namespace on
every key, a by-event-code lookup key, event-time-based access, a hardened
`redis.conf`, and a status/watchdog liveness key.

Goal: align our publisher to that lab convention so it reads and behaves like their
systems ("lab consistency"), while keeping our richer data model where it is genuinely
cleaner. We are NOT doing byte-identical drop-in interop; we adopt their patterns, not
their exact struct choices.

## 2. Decisions made during brainstorm

| Question | Decision |
|---|---|
| Overall goal | Lab consistency (same patterns), not byte-identical interop |
| Data model | Keep our per-source full-field Streams; do NOT switch to their per-code-only layout |
| Timestamps | Keep sec/ns/utc fields; rely on Redis stream `-seq` for collisions (no manual +1 ns) |
| Stream entry ID | Set from event time (`ms = sec*1000 + ns//1e6`), with a monotonic guard |
| By-code lookup | ADD per-event-code index keys (a latest-event hash) alongside the streams |
| Namespace | `KR260` prefix on every key |
| redis.conf (persistence off + stream tuning) | Adopt |
| Health status + watchdog key | Adopt (minimal; in the writer thread) |
| Unix domain socket | Skip (YAGNI: TCP localhost is enough; no local-consumer need yet) |
| ACLs / producer password | Skip (YAGNI: no security boundary on a localhost single board) |

## 3. Key scheme (namespace `KR260`)

Every key carries the `KR260:` prefix.

- **Event streams** (durable feed, unchanged fields): `KR260:tclk`, `KR260:aclk`. Each
  `XADD` carries `{sec, ns, utc, event, data, is_tclk, has_data, src}` (all strings,
  as today).
- **Stream entry ID = event time**: the `XADD` ID is `<ms>-*` where
  `ms = sec*1000 + ns//1_000_000`, so Redis assigns the `-seq` and consumers can
  `XRANGE` / `XREVRANGE` by when the event happened.
- **By-code index** (familiar direct lookup): `KR260:event:<src>:0x<HEX>` (e.g.
  `KR260:event:tclk:0x1D`), a hash holding that code's LATEST event plus a running
  count: `{sec, ns, utc, data, count}`. Updated per event with `HSET` (latest
  sec/ns/utc/data) + `HINCRBY count 1`. `<HEX>` is the event code as two-digit
  uppercase hex; `<src>` is `tclk` or `aclk`.
- **Health**: `KR260:status` (set to 1 on connect) and `KR260:watchdog` (refreshed with
  a TTL; see section 6).

## 4. Monotonic stream-ID guard (required correctness detail)

Redis requires stream entry IDs to be strictly increasing; an `XADD` with an ID equal
to or smaller than the stream's current top errors. Event time is normally monotonic
per source (the hardware FIFO preserves order and WR timestamps increase), but a WR
re-arm can jump the clock backward by up to ~1 s (observed this session). A naive
event-time ID would then make `XADD` error and crash the writer.

The sink therefore keeps a per-stream `last_ms` and uses `guarded_ms-*` where
`guarded_ms = max(event_ms, last_ms[stream])`, then sets `last_ms[stream] = guarded_ms`.
In steady state the ID is exactly the event ms; across a backward re-arm jump, a handful
of events briefly cluster at the last ms (disambiguated by `-seq`) instead of erroring.
This is invisible in normal running and never drops or crashes.

## 5. Components and interface changes

### 5.1 `redis_sink.py`

The sink stays schema-agnostic (it executes described Redis ops; it does not know event
semantics). The `submit()` contract changes from a flat field dict to a **record**:

```python
record = {
    "stream":       "KR260:tclk",           # stream key
    "id_ms":        <int>,                   # event-time ms (sink applies the monotonic guard)
    "fields":       {...8 stream fields...}, # XADD field map (all strings)
    "index_key":    "KR260:event:tclk:0x1D", # per-code hash key
    "index_fields": {...4 index fields...},  # HSET map: sec, ns, utc, data (strings)
}
```

Per record, the writer pipelines three ops:
- `XADD <stream> <guarded_ms>-* <fields> MAXLEN ~ <maxlen>`
- `HSET <index_key> <index_fields>`
- `HINCRBY <index_key> count 1`

Constructor gains the health keys and TTL:
`RedisSink(host, port, maxlen, queue_size, batch, status_key, watchdog_key,
watchdog_ttl=30, watchdog_period=10, connect=None)`. The per-record `stream` and
`index_key` come from the record (the sink is no longer bound to a single stream at
construction). `submit(record)`, `start()`, `stop(timeout)`, `stats()` keep their shape;
`stats()` gains no required new keys. The bounded-queue / drop-oldest / reconnect /
best-effort-stop behavior is unchanged.

### 5.2 `redis_publish.py`

- New `--namespace` arg (default `KR260`).
- Derives `stream = f"{ns}:{src}"`, `index_key = f"{ns}:event:{src}:0x{event:02X}"`,
  `id_ms = sec*1000 + ns//1_000_000` (sec/ns from `wr_split`).
- `event_fields(...)` is unchanged and still builds the 8-field stream map; a new small
  helper builds the record (stream, id_ms, fields, index_key, index_fields), where
  `index_fields = {sec, ns, utc, data}` (a subset of the stream fields).
- `on_event` drops UNSYNC (unchanged), builds the record, and `sink.submit(record)`.
- Constructs `RedisSink` with `status_key = f"{ns}:status"`,
  `watchdog_key = f"{ns}:watchdog"`.
- The 1 Hz stats line is unchanged.

## 6. Health status + watchdog

Liveness lives in the sink's WRITER thread, not the drain loop, because under load the
drain never idles (so the publisher's idle_cb stats line cannot be relied on for
liveness). On first successful connect the writer sets `KR260:status = 1`. Every
`watchdog_period` (~10 s) it refreshes `KR260:watchdog` with a value (e.g. a monotonic
tick or unix seconds) and an expiry of `watchdog_ttl` (~30 s). If the publisher dies,
`KR260:watchdog` expires within the TTL and `KR260:status` goes stale, so an external
monitor can detect a dead feed. No monitor is built here (out of scope); this only
publishes the liveness signal in the lab-standard shape.

## 7. Server config

`deploy/redis-kr260.conf`: the directives to disable persistence and tune streams,
matching the reference:

```
save ""
appendonly no
stream-node-max-bytes 4096
stream-node-max-entries 100
```

Applied to the board's apt `redis-server` (systemd). The runbook documents adding these
directives to `/etc/redis/redis.conf` (or an `include`d drop-in) and
`sudo systemctl restart redis-server`. Port stays the default 6379 (no reason to move to
their 6380 on our single board). No `bind *`, no unix socket, no ACLs (sections 2, 5).

## 8. Testing (PC, stub Redis, existing manual-runner style)

- `test_redis_sink.py` (updated for the record contract):
  - a record submit pipelines exactly `XADD` (with the event-time ID) + `HSET` +
    `HINCRBY count 1`, with `MAXLEN ~ <maxlen>` on the XADD.
  - monotonic guard: submitting records with a decreasing `id_ms` yields emitted stream
    IDs that never decrease (the second uses the guarded, not the raw, ms).
  - watchdog/status: after `start()`, `status_key` is set and `watchdog_key` is written
    (stub records `set`/`setex`), refreshed on the writer's period.
  - drop-oldest, reconnect-after-error, and stop-flushes still hold on the new contract.
- `test_redis_publish.py` (updated):
  - record building is correct: `stream = KR260:tclk`, `index_key =
    KR260:event:tclk:0x1D`, `id_ms` computed from sec/ns, the 8-field stream map, the
    4-field index map.
  - `should_publish` still drops UNSYNC (ts==0).
  - the lazy-redis-import guard still holds (importing the modules needs no `redis`).
- Board integration (documented in `redis.md`, manual): with a locked WR timebase and
  the conf applied, `XREVRANGE KR260:tclk + - COUNT 3` shows event-time-ordered entries,
  `HGETALL KR260:event:tclk:0x1D` shows the latest event + count, `GET KR260:status`
  returns 1, and `KR260:watchdog` exists with a TTL (`TTL KR260:watchdog`).

## 9. Deployment

- `deploy/redis-kr260.conf` added; `hw.ps1` `aclk_pipeline` deploy map gains it.
- `deploy/redis.md` updated: apply the conf + restart, the new `KR260:` key scheme in
  the run/verify sections, and the `HGETALL` / `GET KR260:status` / `TTL
  KR260:watchdog` checks.
- Branch: `redis-convention`, cut from `redis-publish`.

## 10. Out of scope (YAGNI)

- Unix domain socket, ACLs / producer password, `bind *`, port 6380.
- Their per-event-code-only layout, single-ns-value timestamps, manual +1 ns collision
  nudge (our stream `-seq` handles collisions).
- A consumer / monitor that reads the streams, index, or watchdog.
- Checking out their `RedisAdapter` / `clk-monitor` submodules for byte-identical key
  names (we match the pattern, not the exact struct).
