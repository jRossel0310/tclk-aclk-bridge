# Redis Convention Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the KR260 Redis publisher to the Fermilab lab conventions: a `KR260:` namespace, per-source Streams keyed by event time (with a monotonic guard), per-event-code index hashes for by-code lookup, a status/watchdog liveness key, and a persistence-off `redis.conf`.

**Architecture:** `redis_sink.py`'s writer changes from XADD-only-into-one-stream to executing a per-event **record** (`{stream, id_ms, fields, index_key, index_fields}`): one `XADD <stream> <guarded_ms>-*` + `HSET <index_key>` + `HINCRBY <index_key> count 1`, plus a periodic status/watchdog refresh in the writer thread. `redis_publish.py` builds those records under the `KR260` namespace. Board-side Python + one Redis config file; no RTL change. Spec: `docs/superpowers/specs/2026-07-07-redis-convention-alignment-design.md`.

**Tech Stack:** Python 3 (board `sudo python3`; PC unit tests via `.venv\Scripts\python.exe`), `redis-py` (board only, imported lazily so PC tests use a stub), stdlib `queue`/`threading`.

## Global Constraints

- All work on branch `redis-convention`, cut from `redis-publish`.
- Never use em dashes anywhere (code, comments, docs, commit messages). Use commas, colons, or parentheses.
- `redis-py` MUST stay imported lazily (only inside `_default_connect`), never at module top level, so the modules import on the PC without `redis` and the unit tests run with a stub.
- Namespace is `KR260` (default, overridable via `--namespace`). Derived keys: stream `KR260:<src>`, index `KR260:event:<src>:0x<HEX>` (event code as `0x%02X` of the full event value, min 2 digits), status `KR260:status`, watchdog `KR260:watchdog`.
- Stream entry ID is `<ms>-*` where `ms = sec*1000 + ns//1_000_000`; the sink applies a per-stream monotonic guard (`guarded_ms = max(event_ms, last_ms[stream])`) so a backward WR re-arm jump cannot make XADD error.
- Every XADD passes `maxlen=<maxlen>, approximate=True`. The index hash is updated with `HSET index_key mapping=index_fields` then `HINCRBY index_key count 1`.
- UNSYNC events (ts==0) are still dropped by the publisher.
- `submit()` still never blocks: on a full queue it drops the OLDEST record and counts it.
- Do NOT add a unix socket, ACLs, `bind *`, or port 6380 (explicitly out of scope).
- PC unit tests run from the repo root as `& .venv\Scripts\python.exe deploy\<test>.py` and end with an all-passed line. The board is unavailable here; board integration is manual (documented in `redis.md`). Do NOT connect to a real Redis or board during implementation.
- Commit after every task with the message given in the task.

---

### Task 1: Sink record contract, event-time IDs + monotonic guard, per-code index

**Files:**
- Modify: `deploy/redis_sink.py`
- Modify: `deploy/test_redis_sink.py` (full rewrite for the record contract)

**Interfaces:**
- Consumes: nothing new (stdlib; `redis` lazy).
- Produces (used by Tasks 2 and 3):
  - `RedisSink(host="127.0.0.1", port=6379, maxlen=1_000_000, queue_size=100_000, batch=1000, connect=None)` (NO `stream` arg anymore; per-record now).
  - `.submit(record)` where `record = {"stream": str, "id_ms": int, "fields": dict, "index_key": str, "index_fields": dict}`; never blocks, drop-oldest on full.
  - `.start()`, `.stop(timeout=2.0)`, `.stats()` (unchanged keys: `published, queue_dropped, redis_dropped, reconnects, queued`).
  - The client-like object supports `.pipeline(transaction=False)` returning an object with `.xadd(stream, fields, id=, maxlen=, approximate=)`, `.hset(key, mapping=)`, `.hincrby(key, field, amount)`, `.execute()`.

- [ ] **Step 1: Rewrite the test for the record contract (failing)**

Replace the entire contents of `deploy/test_redis_sink.py` with:

```python
"""Unit tests for redis_sink.RedisSink (no hardware, no Redis server).
A stub Redis records the pipelined ops (xadd/hset/hincrby) and can be told to fail
to exercise reconnect. Run: python deploy/test_redis_sink.py   or   pytest deploy -q"""
import time

from redis_sink import RedisSink


class FakePipe:
    def __init__(self, ops_log, fail):
        self.ops_log = ops_log      # committed ops land here on execute()
        self.fail = fail
        self.pending = []

    def xadd(self, stream, fields, id=None, maxlen=None, approximate=None):
        self.pending.append(("xadd", stream, dict(fields), id, maxlen, approximate))

    def hset(self, key, mapping=None):
        self.pending.append(("hset", key, dict(mapping)))

    def hincrby(self, key, field, amount):
        self.pending.append(("hincrby", key, field, amount))

    def execute(self):
        if self.fail:
            raise RuntimeError("redis down")
        self.ops_log.extend(self.pending)
        self.pending = []


class FakeRedis:
    """Records committed pipeline ops in `ops`. First `fail_times` pipelines raise on
    execute(), to exercise the sink's reconnect/drop path."""
    def __init__(self, fail_times=0):
        self.ops = []
        self.fail_times = fail_times

    def pipeline(self, transaction=False):
        fail = self.fail_times > 0
        if fail:
            self.fail_times -= 1
        return FakePipe(self.ops, fail)


def _wait(pred, timeout=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _record(ms, event="7"):
    return {
        "stream": "KR260:tclk",
        "id_ms": ms,
        "fields": {"sec": "1", "ns": "0", "event": event, "src": "tclk"},
        "index_key": "KR260:event:tclk:0x%02X" % int(event),
        "index_fields": {"sec": "1", "ns": "0", "data": "0"},
    }


def _xadds(fake):
    return [o for o in fake.ops if o[0] == "xadd"]


def test_record_pipelines_xadd_hset_hincrby():
    fake = FakeRedis()
    sink = RedisSink(maxlen=555, connect=lambda: fake)
    sink.start()
    sink.submit(_record(1000, event="29"))       # 29 == 0x1D
    assert _wait(lambda: len(fake.ops) >= 3), sink.stats()
    sink.stop()
    kinds = [o[0] for o in fake.ops]
    assert kinds == ["xadd", "hset", "hincrby"], fake.ops
    _, stream, fields, sid, maxlen, approx = fake.ops[0]
    assert stream == "KR260:tclk"
    assert fields == {"sec": "1", "ns": "0", "event": "29", "src": "tclk"}
    assert sid == "1000-*" and maxlen == 555 and approx is True
    assert fake.ops[1] == ("hset", "KR260:event:tclk:0x1D", {"sec": "1", "ns": "0", "data": "0"})
    assert fake.ops[2] == ("hincrby", "KR260:event:tclk:0x1D", "count", 1)
    assert sink.stats()["published"] == 1


def test_monotonic_id_guard():
    fake = FakeRedis()
    sink = RedisSink(connect=lambda: fake)
    sink.submit(_record(1000))
    sink.submit(_record(500))      # backward jump: must be clamped up to 1000
    sink.submit(_record(2000))     # forward: passes through
    sink.start()
    assert _wait(lambda: len(_xadds(fake)) >= 3), sink.stats()
    sink.stop()
    ids = [o[3] for o in _xadds(fake)]
    assert ids == ["1000-*", "1000-*", "2000-*"], ids


def test_queue_full_drops_oldest():
    sink = RedisSink(queue_size=2, connect=lambda: FakeRedis())
    a, b, c = _record(1), _record(2), _record(3)
    sink.submit(a)
    sink.submit(b)
    sink.submit(c)                 # full -> drop oldest (a) -> [b, c]
    assert sink.stats()["queue_dropped"] == 1
    drained = [sink._q.get_nowait(), sink._q.get_nowait()]
    assert drained == [b, c]


def test_reconnect_after_error():
    fake = FakeRedis(fail_times=1)
    sink = RedisSink(connect=lambda: fake)
    sink.start()
    sink.submit(_record(1000))
    assert _wait(lambda: sink.stats()["reconnects"] >= 1), sink.stats()
    sink.submit(_record(1001))
    assert _wait(lambda: sink.stats()["published"] >= 1), sink.stats()
    sink.stop()
    assert sink.stats()["redis_dropped"] >= 1


def test_stop_flushes_queue():
    fake = FakeRedis()
    sink = RedisSink(connect=lambda: fake)
    for i in range(50):
        sink.submit(_record(1000 + i))
    sink.start()
    sink.stop(timeout=3.0)
    assert len(_xadds(fake)) == 50
    assert sink.stats()["published"] == 50 and sink.stats()["queued"] == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all redis_sink tests passed")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `& .venv\Scripts\python.exe deploy\test_redis_sink.py`
Expected: FAIL (the current `RedisSink` requires a positional `stream` and `submit` treats its arg as a flat XADD field dict, so `test_record_pipelines_xadd_hset_hincrby` raises, e.g. a `TypeError`/assertion mismatch).

- [ ] **Step 3: Rewrite `redis_sink.py` for the record contract**

Replace the entire contents of `deploy/redis_sink.py` with:

```python
"""Background Redis writer for the readout publisher.

A bounded in-process queue decouples the caller (the UIO drain thread) from Redis
latency: submit() never blocks; if the queue is full it drops the OLDEST record
(counted) so the hardware FIFO drain can never stall on a Redis hiccup. A writer
thread pops records in batches and, per record, pipelines:
  XADD <stream> <guarded_ms>-* <fields> MAXLEN ~ <maxlen>
  HSET <index_key> <index_fields>
  HINCRBY <index_key> count 1
On any Redis error it counts the dropped batch, reconnects with backoff, continues.

Stream IDs come from event time (ms), with a per-stream monotonic guard so a backward
WR re-arm jump cannot make XADD error (Redis requires increasing IDs).

Redis is reached through an injected `connect` factory (default: a real redis-py
client). redis-py is imported lazily inside that factory so this module imports cleanly
on a machine without redis-py and the unit tests run with a stub."""
import queue
import threading
import time


def _default_connect(host, port):
    import redis   # lazy: module imports without redis-py present (PC unit tests)
    return redis.Redis(host=host, port=port,
                       socket_connect_timeout=1.0, socket_timeout=1.0)


class RedisSink:
    def __init__(self, host="127.0.0.1", port=6379, maxlen=1_000_000,
                 queue_size=100_000, batch=1000, connect=None):
        self.maxlen = maxlen
        self.batch = batch
        self._connect = connect or (lambda: _default_connect(host, port))
        self._q = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._last_ms = {}                       # per-stream monotonic-ID guard
        self.published = 0
        self.queue_dropped = 0
        self.redis_dropped = 0
        self.reconnects = 0

    # ---- producer side (drain thread) ----
    def submit(self, record):
        """Enqueue one event record. Never blocks: on a full queue drop the OLDEST
        record (counted), then enqueue this one. A record is:
        {stream, id_ms, fields, index_key, index_fields}."""
        try:
            self._q.put_nowait(record)
            return
        except queue.Full:
            pass
        try:
            self._q.get_nowait()                 # drop oldest
            with self._lock:
                self.queue_dropped += 1
        except queue.Empty:
            pass
        try:
            self._q.put_nowait(record)
        except queue.Full:                       # racing producers; drop this one
            with self._lock:
                self.queue_dropped += 1

    # ---- consumer side (writer thread) ----
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout=2.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def stats(self):
        with self._lock:
            return {"published": self.published, "queue_dropped": self.queue_dropped,
                    "redis_dropped": self.redis_dropped, "reconnects": self.reconnects,
                    "queued": self._q.qsize()}

    def _drain_batch(self):
        batch = []
        for _ in range(self.batch):
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
        return batch

    def _write_batch(self, client, batch):
        pipe = client.pipeline(transaction=False)
        for rec in batch:
            stream = rec["stream"]
            ms = rec["id_ms"]
            last = self._last_ms.get(stream, 0)
            if ms < last:                        # monotonic guard: never go backward
                ms = last
            self._last_ms[stream] = ms
            pipe.xadd(stream, rec["fields"], id="%d-*" % ms,
                      maxlen=self.maxlen, approximate=True)
            pipe.hset(rec["index_key"], mapping=rec["index_fields"])
            pipe.hincrby(rec["index_key"], "count", 1)
        pipe.execute()

    def _run(self):
        client = None
        while True:
            if self._stop.is_set() and self._q.empty():
                break
            if client is None:
                try:
                    client = self._connect()
                except Exception:
                    with self._lock:
                        self.reconnects += 1
                    if self._stop.is_set():
                        break                    # stopping AND cannot connect: give up rest
                    time.sleep(0.5)
                    continue
            batch = self._drain_batch()
            if not batch:
                if self._stop.is_set():
                    break
                time.sleep(0.005)
                continue
            try:
                self._write_batch(client, batch)
                with self._lock:
                    self.published += len(batch)
            except Exception:
                with self._lock:
                    self.redis_dropped += len(batch)
                    self.reconnects += 1
                client = None                    # force reconnect next iteration
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `& .venv\Scripts\python.exe deploy\test_redis_sink.py`
Expected: PASS, ending with `all redis_sink tests passed` (5 tests).

- [ ] **Step 5: Commit**

```bash
git add deploy/redis_sink.py deploy/test_redis_sink.py
git commit -m "feat(deploy): RedisSink record contract (event-time IDs + monotonic guard, per-code index HSET/HINCRBY)"
```

---

### Task 2: Status + watchdog liveness in the writer thread

**Files:**
- Modify: `deploy/redis_sink.py`
- Modify: `deploy/test_redis_sink.py` (add the stub `set` + one test)

**Interfaces:**
- Consumes: Task 1's `RedisSink`.
- Produces (used by Task 3): `RedisSink(..., status_key=None, watchdog_key=None, watchdog_ttl=30, watchdog_period=10, ...)`. When `status_key`/`watchdog_key` are set, the writer thread sets `status_key=1` once per (re)connect and refreshes `watchdog_key` with `ex=watchdog_ttl` every `watchdog_period` seconds. The client-like object must also support `.set(key, value, ex=None)`.

- [ ] **Step 1: Add the watchdog test (failing)**

In `deploy/test_redis_sink.py`, add a `set` method to `FakeRedis` (so it records status/watchdog writes) and one new test. Change the `FakeRedis` class to:

```python
class FakeRedis:
    """Records committed pipeline ops in `ops`, and set() calls in `kv`. First
    `fail_times` pipelines raise on execute(), to exercise reconnect/drop."""
    def __init__(self, fail_times=0):
        self.ops = []
        self.kv = {}
        self.fail_times = fail_times

    def pipeline(self, transaction=False):
        fail = self.fail_times > 0
        if fail:
            self.fail_times -= 1
        return FakePipe(self.ops, fail)

    def set(self, key, value, ex=None):
        self.kv[key] = (value, ex)
```

And add this test (before the `if __name__` block):

```python
def test_status_and_watchdog():
    fake = FakeRedis()
    sink = RedisSink(status_key="KR260:status", watchdog_key="KR260:watchdog",
                     watchdog_ttl=30, watchdog_period=0, connect=lambda: fake)
    sink.start()
    assert _wait(lambda: "KR260:status" in fake.kv and "KR260:watchdog" in fake.kv), fake.kv
    sink.stop()
    assert fake.kv["KR260:status"][0] == 1
    _, ex = fake.kv["KR260:watchdog"]
    assert ex == 30
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `& .venv\Scripts\python.exe deploy\test_redis_sink.py`
Expected: FAIL on `test_status_and_watchdog` (current `RedisSink.__init__` has no `status_key`/`watchdog_key` kwargs, so it raises `TypeError: __init__() got an unexpected keyword argument 'status_key'`).

- [ ] **Step 3: Add the watchdog to `redis_sink.py`**

In `deploy/redis_sink.py`, extend `__init__` (add the four kwargs and the two state fields) and add the refresh logic. Change the `__init__` signature and body to:

```python
    def __init__(self, host="127.0.0.1", port=6379, maxlen=1_000_000,
                 queue_size=100_000, batch=1000, status_key=None,
                 watchdog_key=None, watchdog_ttl=30, watchdog_period=10,
                 connect=None):
        self.maxlen = maxlen
        self.batch = batch
        self.status_key = status_key
        self.watchdog_key = watchdog_key
        self.watchdog_ttl = watchdog_ttl
        self.watchdog_period = watchdog_period
        self._connect = connect or (lambda: _default_connect(host, port))
        self._q = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._last_ms = {}                       # per-stream monotonic-ID guard
        self._status_set = False                 # re-announced after each (re)connect
        self._last_wd = 0.0                       # monotonic time of last watchdog refresh
        self.published = 0
        self.queue_dropped = 0
        self.redis_dropped = 0
        self.reconnects = 0
```

Add this method (next to `_write_batch`):

```python
    def _maybe_watchdog(self, client):
        """Set status once per (re)connect and refresh the watchdog TTL key every
        watchdog_period seconds. Raises on a Redis error so the caller reconnects."""
        if self.status_key is None and self.watchdog_key is None:
            return
        now = time.monotonic()
        if self._status_set and (now - self._last_wd) < self.watchdog_period:
            return
        if self.status_key is not None and not self._status_set:
            client.set(self.status_key, 1)
            self._status_set = True
        if self.watchdog_key is not None:
            client.set(self.watchdog_key, int(time.time()), ex=self.watchdog_ttl)
        self._last_wd = now
```

Change `_run` so it re-announces status after a (re)connect and calls the watchdog each loop (a watchdog write failure forces a reconnect). Replace the `_run` method with:

```python
    def _run(self):
        client = None
        while True:
            if self._stop.is_set() and self._q.empty():
                break
            if client is None:
                try:
                    client = self._connect()
                    self._status_set = False     # re-announce status after (re)connect
                except Exception:
                    with self._lock:
                        self.reconnects += 1
                    if self._stop.is_set():
                        break                    # stopping AND cannot connect: give up rest
                    time.sleep(0.5)
                    continue
            try:
                self._maybe_watchdog(client)
            except Exception:
                with self._lock:
                    self.reconnects += 1
                client = None
                continue
            batch = self._drain_batch()
            if not batch:
                if self._stop.is_set():
                    break
                time.sleep(0.005)
                continue
            try:
                self._write_batch(client, batch)
                with self._lock:
                    self.published += len(batch)
            except Exception:
                with self._lock:
                    self.redis_dropped += len(batch)
                    self.reconnects += 1
                client = None                    # force reconnect next iteration
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `& .venv\Scripts\python.exe deploy\test_redis_sink.py`
Expected: PASS, ending with `all redis_sink tests passed` (6 tests). The status/watchdog test passes, and the Task 1 tests still pass (they construct `RedisSink` with no status/watchdog keys, so `_maybe_watchdog` returns immediately).

- [ ] **Step 5: Commit**

```bash
git add deploy/redis_sink.py deploy/test_redis_sink.py
git commit -m "feat(deploy): RedisSink status + watchdog liveness keys (writer-thread refresh, TTL)"
```

---

### Task 3: `redis_publish.py` builds KR260 records

**Files:**
- Modify: `deploy/redis_publish.py`
- Modify: `deploy/test_redis_publish.py`

**Interfaces:**
- Consumes: `readout_common` (`line_buffer_stdout`, `parse_args`, `open_dev`, `drain_events`, `wr_split`, `wr_utc`, `say`); `redis_sink.RedisSink(host, port, maxlen, queue_size, status_key, watchdog_key)` and `.submit(record)`.
- Produces (tested here): `event_fields(...)` (unchanged), `should_publish(ts)` (unchanged), and `build_record(ns, src, event, flags, data, ts) -> {stream, id_ms, fields, index_key, index_fields}`.

- [ ] **Step 1: Add the record-building test (failing)**

In `deploy/test_redis_publish.py`, add a test for `build_record` (before the `if __name__` block). Insert:

```python
def test_build_record():
    from redis_publish import build_record
    SEC = 1_751_800_000
    NS = 123_456_789
    r = build_record("KR260", "tclk", 0x1D, 0x02, 0, (SEC << 32) | NS)
    assert r["stream"] == "KR260:tclk"
    assert r["index_key"] == "KR260:event:tclk:0x1D"
    assert r["id_ms"] == SEC * 1000 + NS // 1_000_000       # ...*1000 + 123
    assert r["fields"]["event"] == str(0x1D) and r["fields"]["src"] == "tclk"
    assert r["index_fields"] == {"sec": str(SEC), "ns": str(NS),
                                 "utc": r["fields"]["utc"], "data": "0"}
    # a wide (16-bit) ACLK event still formats sensibly
    r2 = build_record("KR260", "aclk", 0xABCD, 0x01, 5, (SEC << 32) | NS)
    assert r2["stream"] == "KR260:aclk"
    assert r2["index_key"] == "KR260:event:aclk:0xABCD"
    assert r2["index_fields"]["data"] == "5"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `& .venv\Scripts\python.exe deploy\test_redis_publish.py`
Expected: FAIL with `ImportError: cannot import name 'build_record' from 'redis_publish'`.

- [ ] **Step 3: Rewrite `redis_publish.py`**

Replace the entire contents of `deploy/redis_publish.py` with:

```python
#!/usr/bin/env python3
"""Publish WR-timestamped readout events to local Redis (KR260 namespace).

Drains one UIO readout (TCLK or ACLK), drops UNSYNC events (ts==0), and submits a
record per event to a background RedisSink. Per event the sink writes:
  XADD KR260:<src> <event-time-ms>-* {sec, ns, utc, event, data, is_tclk, has_data, src}
  HSET KR260:event:<src>:0x<CODE> {sec, ns, utc, data}   (latest event for that code)
  HINCRBY KR260:event:<src>:0x<CODE> count 1
So consumers read the time-ordered stream OR look an event code up directly. The sink
also maintains KR260:status / KR260:watchdog liveness keys. Two threads: this (main)
thread drains the FIFO and enqueues; the sink's writer thread talks to Redis, so a Redis
stall never stalls the hardware FIFO drain.

    sudo python3 redis_publish.py /dev/uio4 --src tclk
    sudo python3 redis_publish.py /dev/uio5 --src aclk

Ctrl-C to stop (flushes the queue, prints final stats). Needs redis-py on the board
(pip install -r requirements-board.txt) and a running redis-server."""
import sys

import readout_common as rc
from readout_common import say, wr_split, wr_utc
from redis_sink import RedisSink


def event_fields(event, flags, data, ts, src):
    """Map a decoded event to the Redis Stream field dict (all string values)."""
    sec, ns = wr_split(ts)
    return {
        "sec": str(sec), "ns": str(ns), "utc": wr_utc(ts),
        "event": str(event), "data": str(data),
        "is_tclk": str((flags >> 1) & 1), "has_data": str(flags & 1),
        "src": src,
    }


def should_publish(ts):
    """UNSYNC events (ts==0, WR timebase not locked when stamped) are not published."""
    return ts != 0


def build_record(ns, src, event, flags, data, ts):
    """Build the sink record for one event: the per-source stream write plus the
    per-event-code index write, all under the `ns` namespace. The stream entry ID is
    the event time in ms (the sink applies the monotonic guard)."""
    sec, nsec = wr_split(ts)
    return {
        "stream":       "%s:%s" % (ns, src),
        "id_ms":        sec * 1000 + nsec // 1_000_000,
        "fields":       event_fields(event, flags, data, ts, src),
        "index_key":    "%s:event:%s:0x%02X" % (ns, src, event),
        "index_fields": {"sec": str(sec), "ns": str(nsec),
                         "utc": wr_utc(ts), "data": str(data)},
    }


def main(argv):
    rc.line_buffer_stdout()
    pos, flags = rc.parse_args(
        argv, value_flags=("--src", "--namespace", "--redis-host", "--redis-port",
                           "--maxlen", "--queue-size"))
    dev    = pos[0] if pos else "/dev/uio4"
    src    = flags.get("--src", "tclk")
    ns     = flags.get("--namespace", "KR260")
    host   = flags.get("--redis-host", "127.0.0.1")
    port   = int(flags.get("--redis-port", "6379"))
    maxlen = int(flags.get("--maxlen", "1000000"))
    qsize  = int(flags.get("--queue-size", "100000"))

    io = rc.open_dev(dev)
    sink = RedisSink(host=host, port=port, maxlen=maxlen, queue_size=qsize,
                     status_key="%s:status" % ns, watchdog_key="%s:watchdog" % ns)
    sink.start()
    stream = "%s:%s" % (ns, src)
    say("# publishing %s events from %s to Redis stream '%s' (%s:%d). Ctrl-C to stop."
        % (src, dev, stream, host, port))

    drained = [0]

    def on_event(e):
        if not should_publish(e["ts"]):        # UNSYNC: dropped by design
            return
        drained[0] += 1
        sink.submit(build_record(ns, src, e["event"], e["flags"], e["data"], e["ts"]))

    def stats_line():
        s = sink.stats()
        say("[stats] drained=%d published=%d queued=%d queue_dropped=%d "
            "redis_dropped=%d reconnects=%d" % (
                drained[0], s["published"], s["queued"], s["queue_dropped"],
                s["redis_dropped"], s["reconnects"]))

    try:
        rc.drain_events(io, on_event, idle_cb=stats_line)
    finally:
        say("\n# stopping; flushing queue ...")
        sink.stop(timeout=3.0)
        stats_line()


if __name__ == "__main__":
    main(sys.argv[1:])
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `& .venv\Scripts\python.exe deploy\test_redis_publish.py`
Expected: PASS, ending with `all redis_publish tests passed` (the existing `event_fields`/`should_publish` tests plus `test_build_record`).

- [ ] **Step 5: Confirm the module still imports without redis-py**

Run: `& .venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'deploy'); import redis_publish; print('import ok')"`
Expected: `import ok` (neither `redis_publish` nor `redis_sink` imports `redis` at load time).

- [ ] **Step 6: Commit**

```bash
git add deploy/redis_publish.py deploy/test_redis_publish.py
git commit -m "feat(deploy): redis_publish builds KR260 records (namespaced stream + per-code index, event-time ID)"
```

---

### Task 4: Redis config, deploy map, runbook, docs

**Files:**
- Create: `deploy/redis-kr260.conf`
- Modify: `hw.ps1` (the `aclk_pipeline` `$pyMap` entry, around line 125)
- Modify: `deploy/redis.md`
- Modify: `docs/FUNCTIONALITY.md` (the two redis bullets in the Deploy section)

**Interfaces:**
- Consumes: the three modules from Tasks 1-3.
- Produces: a deployable config + accurate runbook. No code interface.

- [ ] **Step 1: Create the Redis config**

Create `deploy/redis-kr260.conf`:

```
# KR260 event-publisher Redis settings (append to /etc/redis/redis.conf, then
# sudo systemctl restart redis-server). Matches the Fermilab redis-clock-server
# convention: streams are ephemeral (no persistence), with stream-node tuning.
# No unix socket, no ACLs, no bind * (localhost single-board setup).
save ""
appendonly no
stream-node-max-bytes 4096
stream-node-max-entries 100
```

- [ ] **Step 2: Add the config to the deploy map**

In `hw.ps1`, the `aclk_pipeline` line of `$pyMap` currently reads (from the redis-publish branch):

```powershell
            "aclk_pipeline"   = @("tclk_read.py", "aclkgt_read.py", "wr_time.py", "tclk_filter.py", "readout_common.py", "redis_sink.py", "redis_publish.py", "requirements-board.txt", "aclk_pipeline.dts")
```

Replace it with (adds the config file):

```powershell
            "aclk_pipeline"   = @("tclk_read.py", "aclkgt_read.py", "wr_time.py", "tclk_filter.py", "readout_common.py", "redis_sink.py", "redis_publish.py", "requirements-board.txt", "redis-kr260.conf", "aclk_pipeline.dts")
```

- [ ] **Step 3: Verify the deploy map references only existing files**

Run: `& .venv\Scripts\python.exe -c "import pathlib; base=pathlib.Path('deploy'); names=['tclk_read.py','aclkgt_read.py','wr_time.py','tclk_filter.py','readout_common.py','redis_sink.py','redis_publish.py','requirements-board.txt','redis-kr260.conf','aclk_pipeline.dts']; missing=[f for f in names if not (base/f).exists()]; print('missing:', missing); assert not missing"`
Expected: `missing: []`.

- [ ] **Step 4: Update the runbook**

Replace the entire contents of `deploy/redis.md` with:

```markdown
# Redis event publishing (board-side, KR260 convention)

Publishes WR-timestamped TCLK/ACLK readout events into local Redis under the `KR260:`
namespace, matching the Fermilab redis-clock-server conventions. Publish side only.
UNSYNC events (ts==0, WR timebase not locked) are dropped, so arm the WR timebase first
(see wr.md).

Per event the publisher writes:
- `XADD KR260:<src>` (the time-ordered event feed; entry ID is the event time in ms),
  fields `sec, ns, utc, event, data, is_tclk, has_data, src`.
- `HSET KR260:event:<src>:0x<CODE>` = that code's latest event (`sec, ns, utc, data`) and
  `HINCRBY ... count 1` (a per-code lookup index).
It also maintains `KR260:status` (=1 while alive) and `KR260:watchdog` (a TTL key,
refreshed every ~10 s, expiring in 30 s) for liveness.

## One-time setup on the board

    sudo apt update && sudo apt install -y redis-server python3-redis
    # apply the KR260 Redis settings (ephemeral streams, stream tuning), then restart:
    cat redis-kr260.conf | sudo tee -a /etc/redis/redis.conf
    sudo systemctl enable --now redis-server
    sudo systemctl restart redis-server
    redis-cli ping                       # -> PONG
    sudo python3 -c "import redis; print(redis.__version__)"   # redis-py visible to root

## Run (one publisher per source; after the WR timebase is armed + locked)

    sudo python3 redis_publish.py /dev/uio4 --src tclk
    sudo python3 redis_publish.py /dev/uio5 --src aclk

Match the /dev/uioN indices to the readout names with:
    grep . /sys/class/uio/uio*/name

Ctrl-C stops a publisher (it flushes the queue and prints final stats). The 1 Hz stats
line reports drained / published / queued / queue_dropped / redis_dropped / reconnects.

Options: --namespace (default KR260), --redis-host (127.0.0.1), --redis-port (6379),
--maxlen (stream cap, default 1000000), --queue-size (in-process queue, default 100000).

## Verify

    redis-cli XLEN KR260:tclk                        # climbs while publishing
    redis-cli XREVRANGE KR260:tclk + - COUNT 3       # newest 3 (event-time ordered)
    redis-cli HGETALL KR260:event:tclk:0x1D          # latest event for code 0x1D + count
    redis-cli GET KR260:status                       # 1 while a publisher is alive
    redis-cli TTL KR260:watchdog                     # counts down from ~30 while alive

Cross-check the stream against the console reader (they read the same FIFO, so the same
events appear; do NOT run both on the same /dev/uioN at once, they both POP the FIFO):
    sudo python3 tclk_read.py /dev/uio4 --wr

## Gotchas

- Nothing publishes until the WR timebase is armed and locked (UNSYNC events are
  dropped). If XLEN stays 0, run: sudo python3 wr_time.py /dev/uio6 status
- The publisher never blocks the hardware FIFO drain on a Redis stall: it drops the
  oldest queued records (queue_dropped climbs) rather than stalling. A rising
  queue_dropped / redis_dropped means Redis is not keeping up.
- The `reconnects` stat counts Redis connect/publish FAILURES (not successful
  reconnections). If `published` stays 0 while `reconnects` climbs, Redis is not
  reachable: check `redis-cli ping` (is redis-server running?) and that redis-py is
  installed (`pip install -r requirements-board.txt`) -- a missing redis-py shows up
  as this same climbing-reconnects, published=0 pattern.
- Stream IDs are the event time, guarded to never go backward. A WR re-arm that jumps
  the clock back briefly clusters a few entries at the last ms instead of erroring.
- Streams are capped at --maxlen (approximate) and Redis persistence is off
  (redis-kr260.conf), so streams are in-memory and start empty on a redis restart.
- redis-server binds localhost by default; keep it that way (no auth is configured).
```

- [ ] **Step 5: Update FUNCTIONALITY.md**

In `docs/FUNCTIONALITY.md`, section 5 (Deploy), replace the two existing redis bullets (the `redis_sink.py` and `redis_publish.py` lines added on the redis-publish branch) with:

```markdown
- **redis_sink.py** (+ test_redis_sink.py): background Redis writer. Bounded in-process queue + writer thread pipelines, per event record, XADD (event-time ID with a per-stream monotonic guard) + HSET/HINCRBY of a per-code index + a periodic status/watchdog refresh; submit() never blocks (drops oldest, counted) so a Redis stall cannot stall the UIO drain; auto-reconnects. redis-py imported lazily so PC unit tests need no server.
- **redis_publish.py** (+ test_redis_publish.py): per-source publisher. Drains one UIO readout via readout_common.drain_events, drops UNSYNC events, and builds KR260-namespaced records: stream KR260:tclk / KR260:aclk (from uio4 / uio5) plus a per-event-code index KR260:event:<src>:0x<CODE>. Config deploy/redis-kr260.conf (persistence off); runbook deploy/redis.md; board dep deploy/requirements-board.txt.
```

- [ ] **Step 6: Run the full deploy-side unit suite (regression)**

Run each and confirm the all-passed line:

```powershell
& .venv\Scripts\python.exe deploy\test_redis_sink.py
& .venv\Scripts\python.exe deploy\test_redis_publish.py
& .venv\Scripts\python.exe deploy\test_readout_common.py
& .venv\Scripts\python.exe deploy\test_wr_time.py
& .venv\Scripts\python.exe deploy\test_tclk_filter.py
```

Expected: every file prints its `all ... passed` line (or `OK`), no tracebacks.

- [ ] **Step 7: Commit**

```bash
git add deploy/redis-kr260.conf hw.ps1 deploy/redis.md docs/FUNCTIONALITY.md
git commit -m "feat(deploy): KR260 redis.conf + runbook/docs for the namespaced convention; ship the conf"
```

---

## Post-plan gates (not automated here)

1. Board integration (in `deploy/redis.md`): with the WR timebase armed + locked, the KR260 redis.conf applied, and a publisher running, confirm `XREVRANGE KR260:tclk` shows event-time-ordered entries, `HGETALL KR260:event:tclk:0x1D` shows the latest event + count, `GET KR260:status` returns 1, and `TTL KR260:watchdog` counts down. The dev environment has no board or Redis, so this is manual.
2. The threaded writer path (event-time IDs, index writes, watchdog) is exercised by the unit tests via the stub Redis; the real redis-py client path is covered only on the board.
