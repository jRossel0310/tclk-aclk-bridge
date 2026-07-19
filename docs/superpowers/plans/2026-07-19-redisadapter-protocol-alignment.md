# RedisAdapter Protocol v1.0 alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the KR260 board-side Redis event convention conform to the Fermilab RedisAdapter Protocol Specification v1.0 so a generic RedisAdapter consumer can read our TCLK/ACLK streams with no producer-specific code.

**Architecture:** Clean cutover of the publisher (`redis_publish.py` + `redis_sink.py`) and archiver (`stream_archive.py`). Three protocol changes: braced `{KR260}:subKey` keys, full `RA_Time` stream IDs (`<ms>-<ns_within_ms>`) with an ns-resolution monotonic guard, and a mandatory `_` binary payload (little-endian `<IIIHB` struct) published alongside the existing readable text fields. A new reusable RA-decode helper (`ra_consumer.py`) plus a hermetic self-hosting round-trip test prove compliance.

**Tech Stack:** Python 3, redis-py, real `redis-server` (spawned by the self-test), pytest (tests also run under bare `python deploy/<test>.py`).

## Global Constraints

- **Base key:** `{KR260}` (braces literal). Braces live in the key-builder helpers, never in the `--namespace` value (default stays `KR260`).
- **Stream ID:** `RA_Time = sec*1_000_000_000 + ns`; `id = "%d-%d" % divmod(RA_Time, 1_000_000)`. No `-*`.
- **`_` payload struct:** `struct.pack("<IIIHB", sec, ns, data, event, flags)` = 15 bytes: sec u32, ns u32, data u32, event u16, flags u8 (bit0 `has_data`, bit1 `is_tclk`).
- **Readable text fields stay** (`sec, ns, event, data, is_tclk, has_data, src`) as additional fields; `_` is added, not a replacement.
- **WR timestamp packing:** `ts = (sec << 32) | ns`; `wr_split(ts) -> (sec, ns)` (already exists in `readout_common`).
- **Tests run both ways:** each test file has an `if __name__ == "__main__":` runner AND works under `pytest deploy -q`. Keep that pattern.
- **No em dashes anywhere** (project style).

---

### Task 1: Braced key builders in the publisher

**Files:**
- Modify: `deploy/redis_publish.py:49-56` (`_stream_key`, `_index_key`) and `deploy/redis_publish.py:120` (status/watchdog keys in `main`)
- Test: `deploy/test_redis_publish.py`

**Interfaces:**
- Produces: `_stream_key(ns, src) -> "{KR260}:tclk"`, `_index_key(ns, src, event) -> "{KR260}:event:tclk:0x1D"`. Consumed by `build_record` (Task 3) and the self-test (Task 6).

- [ ] **Step 1: Update the build_record key assertions to expect braces (failing test)**

In `deploy/test_redis_publish.py`, replace the four key assertions in `test_build_record`:

```python
    assert r["stream"] == "{KR260}:tclk"
    assert r["index_key"] == "{KR260}:event:tclk:0x1D"
```

and further down:

```python
    assert r2["stream"] == "{KR260}:aclk"
    assert r2["index_key"] == "{KR260}:event:aclk:0xABCD"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest deploy/test_redis_publish.py::test_build_record -q`
Expected: FAIL (`assert 'KR260:tclk' == '{KR260}:tclk'`).

- [ ] **Step 3: Brace the key builders**

In `deploy/redis_publish.py`, change the two helpers:

```python
@lru_cache(maxsize=None)
def _stream_key(ns, src):
    return "{%s}:%s" % (ns, src)


@lru_cache(maxsize=4096)
def _index_key(ns, src, event):
    return "{%s}:event:%s:0x%02X" % (ns, src, event)
```

And in `main`, the status/watchdog keys (currently `status_key="%s:status" % ns, watchdog_key="%s:watchdog" % ns`):

```python
                     status_key="{%s}:status" % ns, watchdog_key="{%s}:watchdog" % ns)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest deploy/test_redis_publish.py -q`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add deploy/redis_publish.py deploy/test_redis_publish.py
git commit -m "feat(redis): brace base key ({KR260}:subKey) for RedisAdapter v1.0 hash tagging"
```

---

### Task 2: `_` binary payload in event_fields

**Files:**
- Modify: `deploy/redis_publish.py` (top imports + `event_fields`, lines 22-23 and 59-71)
- Test: `deploy/test_redis_publish.py`

**Interfaces:**
- Produces: `event_fields(...)` now returns a dict that additionally contains `"_"` -> `bytes` (15-byte `<IIIHB` struct). All other fields unchanged and still `str`.

- [ ] **Step 1: Write the failing test for the `_` field**

In `deploy/test_redis_publish.py`, update `test_event_fields_schema`. Replace the key-set and isinstance assertions, and add payload decoding:

```python
import struct


def test_event_fields_schema():
    SEC = 1_751_800_000
    f = event_fields(0x07, 0x03, 0xABCD, (SEC << 32) | 1500, "tclk")
    assert f["sec"] == str(SEC) and f["ns"] == "1500"
    assert f["event"] == "7" and f["data"] == str(0xABCD)
    assert f["is_tclk"] == "1" and f["has_data"] == "1"
    assert f["src"] == "tclk"
    # readable extras + the mandatory RedisAdapter `_` binary payload
    assert set(f.keys()) == {"sec", "ns", "event", "data",
                             "is_tclk", "has_data", "src", "_"}
    assert all(isinstance(v, str) for k, v in f.items() if k != "_")
    # `_` is the little-endian <IIIHB> primary struct
    assert isinstance(f["_"], (bytes, bytearray)) and len(f["_"]) == 15
    sec, ns, data, event, flags = struct.unpack("<IIIHB", f["_"])
    assert (sec, ns, data, event, flags) == (SEC, 1500, 0xABCD, 0x07, 0x03)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest deploy/test_redis_publish.py::test_event_fields_schema -q`
Expected: FAIL (`KeyError: '_'` / key-set mismatch).

- [ ] **Step 3: Add the struct pack to event_fields**

In `deploy/redis_publish.py`, add `import struct` near the top imports (line ~22, next to `import time`):

```python
import struct
import time
```

Then extend `event_fields` to pack `_`:

```python
def event_fields(event, flags, data, ts, src):
    """Map a decoded event to the Redis Stream field dict. Carries the mandatory
    RedisAdapter `_` primary payload (little-endian <IIIHB>: sec u32, ns u32, data
    u32, event u16, flags u8) plus the human-readable extras. A generic RedisAdapter
    consumer reads only `_`; our archiver/redis-cli read the text fields. No per-entry
    `utc`: it duplicated sec/ns on every entry and was pure hot-path cost; consumers
    derive UTC from sec/ns and the per-code index hash keeps a human-readable utc."""
    sec, ns = wr_split(ts)
    return {
        "_": struct.pack("<IIIHB", sec, ns, data, event, flags),
        "sec": str(sec), "ns": str(ns),
        "event": str(event), "data": str(data),
        "is_tclk": str((flags >> 1) & 1), "has_data": str(flags & 1),
        "src": src,
    }
```

- [ ] **Step 4: Run the full publisher test file**

Run: `python -m pytest deploy/test_redis_publish.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add deploy/redis_publish.py deploy/test_redis_publish.py
git commit -m "feat(redis): add RedisAdapter `_` binary payload (<IIIHB>) to stream entries"
```

---

### Task 3: Full RA_Time stream IDs + ns-resolution guard

**Files:**
- Modify: `deploy/redis_publish.py:79-94` (`build_record`: `id_ms` -> `ra_time`)
- Modify: `deploy/redis_sink.py:50` (`_last_ms` -> `_last_ratime`) and `deploy/redis_sink.py:105-124` (`_write_batch`)
- Modify docstrings referencing `id_ms` in both files
- Test: `deploy/test_redis_publish.py`, `deploy/test_redis_sink.py`

**Interfaces:**
- Produces: the sink record dict now uses `"ra_time"` (int, ns since epoch) in place of `"id_ms"`. The sink emits `id = "%d-%d" % divmod(guarded_ra_time, 1_000_000)`. Consumed by the self-test (Task 6).

- [ ] **Step 1: Update build_record test to expect ra_time (failing test)**

In `deploy/test_redis_publish.py::test_build_record`, replace the `id_ms` assertion:

```python
    assert r["ra_time"] == SEC * 1_000_000_000 + NS
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest deploy/test_redis_publish.py::test_build_record -q`
Expected: FAIL (`KeyError: 'ra_time'`).

- [ ] **Step 3: Emit ra_time from build_record**

In `deploy/redis_publish.py::build_record`, replace the `id_ms` line and update the docstring line about the entry ID:

```python
def build_record(ns, src, event, flags, data, ts):
    """Build the sink record for one event: the per-source stream write plus the
    per-event-code index write, all under the `ns` namespace. The stream entry ID is
    the event's RA_Time (ns since epoch); the sink encodes it as <ms>-<ns_in_ms> and
    applies the monotonic guard. Keys are lru_cached and the index shares the fields
    dict's strings: this runs per event on the drain thread's hot path."""
    sec, nsec = wr_split(ts)
    fields = event_fields(event, flags, data, ts, src)
    return {
        "stream":       _stream_key(ns, src),
        "ra_time":      sec * 1_000_000_000 + nsec,
        "fields":       fields,
        "index_key":    _index_key(ns, src, event),
        "index_fields": {"sec": fields["sec"], "ns": fields["ns"],
                         "utc": wr_utc(ts), "data": fields["data"]},
    }
```

- [ ] **Step 4: Run the publisher tests**

Run: `python -m pytest deploy/test_redis_publish.py -q`
Expected: PASS.

- [ ] **Step 5: Update the sink test record + ID assertions (failing test)**

In `deploy/test_redis_sink.py`, update the `_record` helper to carry `ra_time` and braced keys:

```python
def _record(ra_time, event="7"):
    return {
        "stream": "{KR260}:tclk",
        "ra_time": ra_time,
        "fields": {"sec": "1", "ns": "0", "event": event, "src": "tclk"},
        "index_key": "{KR260}:event:tclk:0x%02X" % int(event),
        "index_fields": {"sec": "1", "ns": "0", "data": "0"},
    }
```

Update `test_record_pipelines_xadd_hset_hincrby` key/id assertions:

```python
    assert stream == "{KR260}:tclk"
    assert fields == {"sec": "1", "ns": "0", "event": "29", "src": "tclk"}
    assert sid == "0-0" and maxlen == 555 and approx is True
    assert fake.ops[1] == ("hset", "{KR260}:event:tclk:0x1D", {"sec": "1", "ns": "0", "data": "0"})
    assert fake.ops[2] == ("hincrby", "{KR260}:event:tclk:0x1D", "count", 1)
```

(The call `sink.submit(_record(1000, event="29"))` now passes `ra_time=1000` ns -> `divmod(1000, 1_000_000) = (0, 1000)`... note the ID is `"0-1000"`. Set the submit to a clean value instead: change that submit to `sink.submit(_record(1_000_000, event="29"))` so `sid == "1-0"`.) Use:

```python
    sink.submit(_record(1_000_000, event="29"))       # 29 == 0x1D; 1e6 ns -> id "1-0"
```

and

```python
    assert sid == "1-0" and maxlen == 555 and approx is True
```

Update `test_index_writes_aggregate_per_batch` braced index keys:

```python
    assert incs == {"{KR260}:event:tclk:0x07": 5, "{KR260}:event:tclk:0x0C": 1}, incs
```

Rewrite `test_monotonic_id_guard` for ns-resolution `<=` bumping:

```python
def test_monotonic_id_guard():
    fake = FakeRedis()
    sink = RedisSink(connect=lambda: fake)
    sink.submit(_record(1_000_000))          # id 1-0
    sink.submit(_record(500_000))            # backward: bumped to 1_000_001 -> 1-1
    sink.submit(_record(1_000_000))          # equal to last-seen input: bumped to 1-2
    sink.submit(_record(2_000_000))          # forward: 2-0
    sink.start()
    assert _wait(lambda: len(_xadds(fake)) >= 4), sink.stats()
    sink.stop()
    ids = [o[3] for o in _xadds(fake)]
    assert ids == ["1-0", "1-1", "1-2", "2-0"], ids
```

Update the braced status/watchdog keys in `test_status_and_watchdog`, `test_watchdog_only_no_status`, `test_watchdog_error_backs_off`, `test_stop_flushes_when_redis_fully_down` (mechanical: `"KR260:status"` -> `"{KR260}:status"`, `"KR260:watchdog"` -> `"{KR260}:watchdog"`).

- [ ] **Step 6: Run to verify the sink tests fail**

Run: `python -m pytest deploy/test_redis_sink.py -q`
Expected: FAIL (ID format `1000-*` vs `1-0`; `KeyError` on record shape).

- [ ] **Step 7: Encode RA_Time + ns guard in the sink**

In `deploy/redis_sink.py`, rename the guard state in `__init__` (line ~50):

```python
        self._last_ratime = {}                   # per-stream monotonic RA_Time (ns) guard
```

Update the module docstring lines about stream IDs (lines ~7 and ~17-18) to describe `<ms>-<ns_in_ms>` from RA_Time. Then rewrite the loop body in `_write_batch` (lines 109-117):

```python
        for rec in batch:
            stream = rec["stream"]
            ra = rec["ra_time"]
            last = self._last_ratime.get(stream, 0)
            if ra <= last:                       # strictly increasing IDs: bump 1 ns
                ra = last + 1
            self._last_ratime[stream] = ra
            ms, seq = divmod(ra, 1_000_000)      # RA_Time -> <ms>-<ns_within_ms>
            pipe.xadd(stream, rec["fields"], id="%d-%d" % (ms, seq),
                      maxlen=self.maxlen, approximate=True)
            k = rec["index_key"]
            idx_last[k] = rec["index_fields"]    # batch order = event order: last wins
            idx_cnt[k] = idx_cnt.get(k, 0) + 1
```

- [ ] **Step 8: Run the sink tests**

Run: `python -m pytest deploy/test_redis_sink.py -q`
Expected: PASS (all tests).

- [ ] **Step 9: Run the whole deploy suite**

Run: `python -m pytest deploy -q`
Expected: PASS (stream_archive tests still green; they use their own literal keys).

- [ ] **Step 10: Commit**

```bash
git add deploy/redis_publish.py deploy/redis_sink.py deploy/test_redis_publish.py deploy/test_redis_sink.py
git commit -m "feat(redis): full RA_Time stream IDs (<ms>-<ns>) with ns-resolution monotonic guard"
```

---

### Task 4: Braced keys in the archiver (+ binary-safe decode)

**Files:**
- Modify: `deploy/stream_archive.py:118-121` (`_default_connect`), `:146` and `:167` (stream key), add a `_stream_key` helper
- Test: `deploy/test_stream_archive.py`

**Interfaces:**
- Produces: archiver reads `{KR260}:tclk`. `_default_connect` tolerates the binary `_` field via `encoding_errors="replace"` (the archiver never reads `_`).

- [ ] **Step 1: Add a failing unit test for the archive stream-key helper**

In `deploy/test_stream_archive.py`, add to the imports:

```python
from stream_archive import (
    HEADER, row_from_entry, DailyCsv, drain_source, load_state, save_state,
    _stream_key,
)
```

and add a test:

```python
def test_archive_stream_key_is_braced():
    assert _stream_key("KR260", "tclk") == "{KR260}:tclk"
    assert _stream_key("KR260", "aclk") == "{KR260}:aclk"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest deploy/test_stream_archive.py::test_archive_stream_key_is_braced -q`
Expected: FAIL (`ImportError: cannot import name '_stream_key'`).

- [ ] **Step 3: Add the helper, brace the keys, make decode binary-safe**

In `deploy/stream_archive.py`, add the helper above `main` (near `_default_connect`):

```python
def _stream_key(namespace, src):
    return "{%s}:%s" % (namespace, src)
```

Update `_default_connect` so the binary `_` field cannot crash the UTF-8 decoder (the archiver only reads the text fields; `_` decodes to a lossy placeholder it ignores):

```python
def _default_connect(host, port):
    import redis   # lazy: module imports without redis-py (PC unit tests)
    return redis.Redis(host=host, port=port, decode_responses=True,
                       encoding_errors="replace",
                       socket_connect_timeout=2.0, socket_timeout=5.0)
```

Replace both stream-key constructions (line ~146 and ~167):

```python
        stream = _stream_key(args.namespace, args.src[0])
```

and

```python
                stream = _stream_key(args.namespace, s)
```

- [ ] **Step 4: Run the archiver tests**

Run: `python -m pytest deploy/test_stream_archive.py -q`
Expected: PASS. (The `FakeStreamRedis`-based tests pass the stream name straight through, so they stay green; the new helper test covers the bracing.)

- [ ] **Step 5: Commit**

```bash
git add deploy/stream_archive.py deploy/test_stream_archive.py
git commit -m "feat(redis): brace archiver keys + tolerate binary `_` field (encoding_errors=replace)"
```

---

### Task 5: RA-compliant decode helper (`ra_consumer.py`)

**Files:**
- Create: `deploy/ra_consumer.py`
- Test: `deploy/test_ra_consumer.py`

**Interfaces:**
- Produces: `stream_key(namespace, src)`, `ra_time_from_id(entry_id)`, `decode_payload(buf)`, `decode_entry(entry_id, field_map)`, `PAYLOAD_LEN`. Consumed by the self-test (Task 6). This module is the reference RA consumer: it reads ONLY the key schema, stream ID, and `_` field.

- [ ] **Step 1: Write the failing tests**

Create `deploy/test_ra_consumer.py`:

```python
"""Unit tests for ra_consumer (no Redis, no board).
Run: python deploy/test_ra_consumer.py   or   pytest deploy -q"""
import struct

from ra_consumer import (
    stream_key, ra_time_from_id, decode_payload, decode_entry, PAYLOAD_LEN,
)


def test_stream_key_is_braced():
    assert stream_key("KR260", "tclk") == "{KR260}:tclk"


def test_payload_len_is_15():
    assert PAYLOAD_LEN == 15


def test_ra_time_from_id_str_and_bytes():
    sec, ns = 1_751_800_000, 123_456_789
    ra = sec * 1_000_000_000 + ns
    ms, seq = divmod(ra, 1_000_000)
    assert ra_time_from_id("%d-%d" % (ms, seq)) == ra
    assert ra_time_from_id(("%d-%d" % (ms, seq)).encode()) == ra


def test_decode_payload():
    buf = struct.pack("<IIIHB", 1_751_800_000, 1500, 0xABCD, 0x07, 0x03)
    d = decode_payload(buf)
    assert d == {"sec": 1_751_800_000, "ns": 1500, "data": 0xABCD,
                 "event": 0x07, "is_tclk": 1, "has_data": 1}


def test_decode_entry_reads_only_underscore():
    buf = struct.pack("<IIIHB", 5, 6, 9, 0x1D, 0x02)
    e = decode_entry(b"1000-500", {b"_": buf, b"sec": b"5"})
    assert e["event"] == 0x1D and e["is_tclk"] == 1 and e["has_data"] == 0
    assert e["ra_time"] == 1000 * 1_000_000 + 500


def test_decode_entry_missing_underscore_raises():
    try:
        decode_entry("1-0", {"sec": "5"})
        raised = False
    except ValueError:
        raised = True
    assert raised


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all ra_consumer tests passed")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest deploy/test_ra_consumer.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'ra_consumer'`).

- [ ] **Step 3: Write ra_consumer.py**

Create `deploy/ra_consumer.py`:

```python
"""RedisAdapter Protocol v1.0 consumer helper (reference code).

Decodes KR260 event-stream entries using ONLY the three core RA protocol pieces:
the {baseKey}:subKey key schema, the RA_Time stream ID (<ms>-<ns_within_ms>), and
the `_` binary primary payload. It deliberately ignores the human-readable extra
fields, so it demonstrates exactly what a generic RedisAdapter consumer needs to
read our primary data. See docs/superpowers/specs/2026-07-19-redisadapter-protocol-alignment-design.md."""
import struct

_STRUCT = "<IIIHB"                          # sec u32, ns u32, data u32, event u16, flags u8
PAYLOAD_LEN = struct.calcsize(_STRUCT)      # 15


def stream_key(namespace, src):
    """The braced RA key for one event source, e.g. ("KR260","tclk") -> {KR260}:tclk."""
    return "{%s}:%s" % (namespace, src)


def ra_time_from_id(entry_id):
    """Redis Stream ID 'ms-seq' -> RA_Time (ns since Unix epoch)."""
    if isinstance(entry_id, (bytes, bytearray)):
        entry_id = entry_id.decode("ascii")
    ms, seq = entry_id.split("-")
    return int(ms) * 1_000_000 + int(seq)


def decode_payload(buf):
    """Unpack the `_` field bytes -> {sec, ns, data, event, is_tclk, has_data}."""
    sec, ns, data, event, flags = struct.unpack(_STRUCT, bytes(buf))
    return {"sec": sec, "ns": ns, "data": data, "event": event,
            "is_tclk": (flags >> 1) & 1, "has_data": flags & 1}


def decode_entry(entry_id, field_map):
    """One RA stream entry (id, field map) -> the primary value dict, with `ra_time`
    added from the stream ID. `field_map` keys may be bytes (decode_responses=False,
    the correct setting for reading a binary `_`) or str. Reads only `_`."""
    payload = field_map.get(b"_")
    if payload is None:
        payload = field_map.get("_")
    if payload is None:
        raise ValueError("entry %r has no `_` primary field" % (entry_id,))
    out = decode_payload(payload)
    out["ra_time"] = ra_time_from_id(entry_id)
    return out
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest deploy/test_ra_consumer.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/ra_consumer.py deploy/test_ra_consumer.py
git commit -m "feat(redis): add ra_consumer RedisAdapter v1.0 decode helper + tests"
```

---

### Task 6: Hermetic self-hosting round-trip self-test

**Files:**
- Create: `deploy/test_ra_roundtrip.py`

**Interfaces:**
- Consumes: `build_record` (Task 3), `RedisSink` (Task 3), `ra_consumer` (Task 5).
- Produces: nothing imported elsewhere; this is the end-to-end compliance proof.

- [ ] **Step 1: Write the self-test**

Create `deploy/test_ra_roundtrip.py`:

```python
"""End-to-end RedisAdapter Protocol v1.0 compliance self-test.

Stands up a PRIVATE redis-server on an ephemeral port, publishes synthetic events
through the REAL producer path (build_record + RedisSink), then reads them back with
the RA-compliant ra_consumer using ONLY the key schema + stream ID + `_` field. This
proves a generic RedisAdapter consumer recovers our primary data. The same producer
targets any Redis by host/port, so pointing at a lab Redis is just different args.

Skips cleanly if `redis-server` is not on PATH (e.g. Windows dev box) or redis-py is
missing. Run: python deploy/test_ra_roundtrip.py   or   pytest deploy -q"""
import os
import shutil
import socket
import subprocess
import tempfile
import time

from redis_publish import build_record
from redis_sink import RedisSink
import ra_consumer

NS = "KR260"


def _skip(reason):
    try:
        import pytest
        pytest.skip(reason, allow_module_level=False)
    except ImportError:
        print("SKIP:", reason)
        raise SystemExit(0)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_redis(tmpdir):
    """Launch a private redis-server (persistence off) and wait for PING."""
    import redis
    port = _free_port()
    conf = os.path.join(tmpdir, "redis.conf")
    with open(conf, "w") as f:
        f.write("port %d\n" % port)
        f.write("bind 127.0.0.1\n")
        f.write("save \"\"\n")
        f.write("appendonly no\n")
        f.write("dir %s\n" % tmpdir)
    proc = subprocess.Popen(["redis-server", conf],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = redis.Redis(host="127.0.0.1", port=port, socket_connect_timeout=0.2)
    for _ in range(100):
        try:
            if client.ping():
                return proc, port
        except Exception:
            time.sleep(0.05)
    proc.terminate()
    raise RuntimeError("redis-server did not become ready")


def _publish(port, events):
    sink = RedisSink(host="127.0.0.1", port=port,
                     status_key="{%s}:status" % NS,
                     watchdog_key="{%s}:watchdog" % NS, watchdog_period=0)
    sink.start()
    for ev, fl, da, ts in events:
        sink.submit(build_record(NS, "tclk", ev, fl, da, ts))
    sink.stop(timeout=5.0)
    return sink.stats()


def test_ra_roundtrip():
    if shutil.which("redis-server") is None:
        _skip("redis-server not on PATH")
    try:
        import redis
    except ImportError:
        _skip("redis-py not installed")

    tmp = tempfile.mkdtemp()
    proc = None
    try:
        proc, port = _start_redis(tmp)
        events = [
            # (event, flags, data, ts=(sec<<32)|ns)  -- distinct RA_Times, no collision
            (0x1D, 0x02, 0,      (1_751_800_000 << 32) | 100),
            (0x07, 0x03, 0xABCD, (1_751_800_000 << 32) | 200),
            (0x07, 0x03, 0x1234, (1_751_800_001 << 32) | 200),
        ]
        stats = _publish(port, events)
        assert stats["published"] == len(events), stats

        client = redis.Redis(host="127.0.0.1", port=port)   # decode_responses=False
        entries = client.xrange(ra_consumer.stream_key(NS, "tclk"))
        assert len(entries) == len(events), entries
        decoded = [ra_consumer.decode_entry(eid, fmap) for eid, fmap in entries]

        # `_` recovers event/data/flags/sec/ns exactly, in publish order
        for (ev, fl, da, ts), d in zip(events, decoded):
            sec, ns = ts >> 32, ts & 0xFFFFFFFF
            assert d["event"] == ev, (d, ev)
            assert d["data"] == da, (d, da)
            assert d["is_tclk"] == (fl >> 1) & 1
            assert d["has_data"] == fl & 1
            assert d["sec"] == sec and d["ns"] == ns
            # RA_Time from the stream ID equals sec*1e9+ns (no collisions here)
            assert d["ra_time"] == sec * 1_000_000_000 + ns

        # braced keys, per-code index hash, and liveness all present
        assert client.exists("{%s}:event:tclk:0x1D" % NS) == 1
        assert client.exists("{%s}:event:tclk:0x07" % NS) == 1
        assert client.hget("{%s}:event:tclk:0x07" % NS, "count") == b"2"
        assert client.get("{%s}:status" % NS) == b"1"
        assert client.exists("{%s}:watchdog" % NS) == 1
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=5)
        shutil.rmtree(tmp, ignore_errors=True)


def test_ra_roundtrip_ns_collision_guard():
    """Two events in the SAME nanosecond must both publish, with strictly increasing
    stream IDs (the second bumped +1 ns), and the `_` payload keeps the true sec/ns."""
    if shutil.which("redis-server") is None:
        _skip("redis-server not on PATH")
    try:
        import redis
    except ImportError:
        _skip("redis-py not installed")

    tmp = tempfile.mkdtemp()
    proc = None
    try:
        proc, port = _start_redis(tmp)
        ts = (1_751_800_000 << 32) | 500          # identical timestamp for both
        events = [(0x07, 0x03, 1, ts), (0x07, 0x03, 2, ts)]
        stats = _publish(port, events)
        assert stats["published"] == 2, stats

        client = redis.Redis(host="127.0.0.1", port=port)
        entries = client.xrange(ra_consumer.stream_key(NS, "tclk"))
        assert len(entries) == 2, entries
        ra0 = ra_consumer.ra_time_from_id(entries[0][0])
        ra1 = ra_consumer.ra_time_from_id(entries[1][0])
        assert ra1 == ra0 + 1                      # guard bumped the collision by 1 ns
        # both `_` payloads still carry the true (identical) sec/ns and their own data
        d0 = ra_consumer.decode_entry(*entries[0])
        d1 = ra_consumer.decode_entry(*entries[1])
        assert d0["ns"] == 500 and d1["ns"] == 500
        assert d0["data"] == 1 and d1["data"] == 2
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=5)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all ra_roundtrip tests passed")
```

- [ ] **Step 2: Run the self-test**

Run: `python -m pytest deploy/test_ra_roundtrip.py -q`
Expected on a machine WITH `redis-server` + redis-py: PASS (2 tests). On the Windows dev box WITHOUT redis-server: SKIPPED (clear reason). Either outcome is acceptable; a FAIL is not.

If `redis-server` and redis-py are available in this environment, a PASS is required. If skipped, note in the commit that it must be run on the board or a Linux host with redis-server before merge.

- [ ] **Step 3: Run the full deploy suite**

Run: `python -m pytest deploy -q`
Expected: PASS (or the two roundtrip tests SKIPPED if no redis-server), no FAIL.

- [ ] **Step 4: Commit**

```bash
git add deploy/test_ra_roundtrip.py
git commit -m "test(redis): self-hosting RedisAdapter v1.0 round-trip compliance test"
```

---

### Task 7: Documentation

**Files:**
- Modify: `deploy/redis.md` (authoritative doc)
- Modify: `deploy/capture.md`, `docs/OPERATIONS.md`, `docs/generated/tclk-aclk-pipeline-hardware-interface-guide.md`, `constraints/README.md` (mechanical key rename + note)

**Interfaces:** none (docs).

- [ ] **Step 1: Rewrite the "The publisher writes" section of `deploy/redis.md`**

Replace the intro + bullet block (the paragraph starting "Publishes WR-timestamped" through the `KR260:watchdog` liveness bullet) with:

```markdown
Publishes WR-timestamped TCLK/ACLK readout events into local Redis following the
**Fermilab RedisAdapter Protocol Specification v1.0** (repo `fermi-ad/redis-adapter`,
`docs/redis-adapter-implementation-spec.md`), so a generic RedisAdapter consumer reads
our primary data with no producer-specific code. Publish side only. UNSYNC events (ts==0,
WR timebase not locked) are dropped, so arm the WR timebase first (see wr.md).

Key schema is RedisAdapter `{baseKey}:subKey` with the base key braced for Redis Cluster
hash tagging (default base `KR260`). The publisher writes:
- per event: `XADD {KR260}:<src>` where the entry ID is the event's **RA_Time**
  (nanoseconds since the Unix epoch) encoded as `<ms>-<ns_within_ms>` =
  `<floor(RA_Time/1e6)>-<RA_Time mod 1e6>`. Fields:
  - `_` : the mandatory RedisAdapter primary payload, a little-endian packed struct
    `<IIIHB>` = sec (u32), ns (u32), data (u32), event (u16), flags (u8; bit0 has_data,
    bit1 is_tclk). This is the producer/consumer device contract; a generic RA consumer
    reads only `_`.
  - readable extras (ignored by generic RA consumers, used by our archiver/redis-cli):
    `sec, ns, event, data, is_tclk, has_data, src`. There is no per-entry `utc` (derive
    it from sec/ns).
- per event code, per writer batch (<= ~1 s): `HSET {KR260}:event:<src>:0x<CODE>` = that
  code's latest event (`sec, ns, utc, data`) and `HINCRBY ... count <n-in-batch>` (a
  per-code lookup index; counts stay exact, the hash updates per batch not per event).
It also maintains `{KR260}:status` (=1 while alive) and `{KR260}:watchdog` (a TTL key,
refreshed every ~10 s, expiring in 30 s) for liveness.

Stream IDs are explicit and complete (no server `-*` sequence), so this no longer
requires Redis >= 7.0; it works on Redis 6 too. A duplicate or backward RA_Time (same-ns
burst or a backward WR re-arm) is bumped to the previous ID + 1 ns so XADD's
strictly-increasing rule holds; the exact sec/ns always remain in `_` and the fields.
```

- [ ] **Step 2: Update the Verify + Run examples in `deploy/redis.md`**

In the `## Verify` block replace the unbraced keys (note `{...}` must be quoted for the shell so the brace is literal):

```bash
    redis-cli XLEN '{KR260}:tclk'                     # climbs while publishing
    redis-cli XREVRANGE '{KR260}:tclk' + - COUNT 3    # newest 3 (event-time ordered)
    redis-cli HGETALL '{KR260}:event:tclk:0x1D'       # latest event for code 0x1D + count
    redis-cli GET '{KR260}:status'                    # 1 while a publisher is alive
    redis-cli TTL '{KR260}:watchdog'                  # counts down from ~30 while alive
```

Add, under `## Run`, a remote-Redis example:

```markdown
To stream to a different Redis (e.g. a lab RedisAdapter server) instead of the board's
local one, point the publisher at it (no other change needed):

    sudo python3 redis_publish.py /dev/uio4 --src tclk \
        --redis-host redis.example.fnal.gov --redis-port 6379
```

In the `## Gotchas` block, soften the two Redis >= 7.0 lines to note it is no longer
required (explicit `<ms>-<ns>` IDs work on Redis 6), but keep the rest.

- [ ] **Step 3: Add a self-test line to `deploy/redis.md`**

Add a short `## Self-test` section:

```markdown
## Self-test (no board, needs a redis-server binary)

Prove the publisher emits RedisAdapter v1.0-compliant entries end to end. It spawns a
private redis-server, runs the real producer path, and reads back with an RA-compliant
consumer (`ra_consumer.py`):

    python3 test_ra_roundtrip.py        # or: pytest deploy -q

It SKIPS if `redis-server` is not installed. `ra_consumer.py` doubles as reference code
for a lab-side RedisAdapter consumer.
```

- [ ] **Step 4: Mechanical key rename in the other docs**

Run to find every remaining unbraced occurrence:

```bash
grep -rn "KR260:" deploy/capture.md docs/OPERATIONS.md docs/generated/tclk-aclk-pipeline-hardware-interface-guide.md constraints/README.md
```

For each hit, change `KR260:<x>` to `{KR260}:<x>` in prose and quote it in any shell
example (`'{KR260}:tclk'`). Where a doc states "requires Redis >= 7.0" for the stream ID
syntax, update it to note explicit `<ms>-<ns>` IDs no longer require Redis 7.

- [ ] **Step 5: Commit**

```bash
git add deploy/redis.md deploy/capture.md docs/OPERATIONS.md docs/generated/tclk-aclk-pipeline-hardware-interface-guide.md constraints/README.md
git commit -m "docs(redis): document RedisAdapter v1.0 keys, RA_Time IDs, `_` payload, self-test"
```

---

## Final verification

- [ ] Run the whole suite once more: `python -m pytest deploy -q`. Expected: all PASS (roundtrip tests SKIPPED only if no redis-server).
- [ ] If a Linux host or the board with `redis-server` is reachable, run `python3 deploy/test_ra_roundtrip.py` there and confirm PASS (the compliance proof).
- [ ] Confirm no `KR260:` (unbraced) strings remain in code: `grep -rn '"KR260:' deploy/ ; grep -rn "'KR260:" deploy/` returns nothing.

## Spec coverage check

- Key schema `{baseKey}:subKey` -> Tasks 1, 4 (and index/status/watchdog).
- Full `RA_Time` stream IDs + ns guard -> Task 3.
- `_` binary payload `<IIIHB>` + kept text fields -> Task 2.
- RA-compliant consumer + self-hosting round-trip -> Tasks 5, 6.
- Stream to a different Redis server -> already via `--redis-host/--redis-port`, documented in Task 7.
- Docs (redis.md + others) -> Task 7.
