# Redis Event Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the pipeline's WR-timestamped TCLK/ACLK readout events to local Redis Streams (`events:tclk`, `events:aclk`) via a board-side publisher that never stalls the hardware FIFO drain when Redis is slow.

**Architecture:** A per-source publisher (`redis_publish.py`) drains one UIO node using a new shared `drain_events` helper in `readout_common.py`, drops UNSYNC events (ts==0), and submits ready-to-XADD field dicts to a `RedisSink` (`redis_sink.py`). The sink owns a bounded in-process queue and a background writer thread that pipelines `XADD ... MAXLEN ~` to Redis and reconnects on error; a full queue drops the oldest entry (counted) so the drain thread never blocks. Board-side Python only, no RTL/bitstream change. Spec: `docs/superpowers/specs/2026-07-07-redis-event-publish-design.md`.

**Tech Stack:** Python 3 (board `sudo python3`, PC unit tests via `.venv\Scripts\python.exe`), `redis-py` (board only, imported lazily so PC tests need no server), stdlib `queue`/`threading`. Deploy pattern lives in `deploy/`.

## Global Constraints

- All work on branch `redis-publish`, cut from `wr-timestamp` (the publisher depends on `wr_split`/`wr_utc`/the readout tooling that lives on `wr-timestamp`, not yet on `main`).
- Never use em dashes anywhere (code, comments, docs, commit messages). Use commas, colons, or parentheses.
- Follow existing `deploy/` patterns: the `say()` helper for output, `readout_common.parse_args(argv, value_flags=(), bool_flags=())` for CLI, and the manual `if __name__ == "__main__"` test runner style of `deploy/test_readout_common.py` (plain asserts, no pytest fixtures).
- `redis-py` MUST be imported lazily (inside the connect factory), never at module top level, so `redis_sink.py` / `redis_publish.py` import cleanly on the PC without `redis` installed and the unit tests run with a stub.
- UNSYNC events (ts == 0) are dropped by the publisher, never published.
- Each stream is capped with `MAXLEN ~ <maxlen>` (approximate) on every XADD.
- Stream entry fields are all strings; numeric fields are decimal. Exact schema (Task 3): `sec, ns, utc, event, data, is_tclk, has_data, src`.
- PC unit tests run from the repo root as `& .venv\Scripts\python.exe deploy\<test>.py` and must end with an all-passed line (matching the existing deploy tests).
- The board is not available from the dev environment; board integration is manual and documented in `deploy/redis.md`. Do NOT attempt to connect to a real Redis or a board during implementation.
- Commit after every task with the message given in the task.

---

### Task 1: `drain_events` shared drain helper

**Files:**
- Modify: `deploy/readout_common.py` (add `drain_events` after `stream_events`)
- Modify: `deploy/test_readout_common.py` (add one test before the `if __name__` runner)

**Interfaces:**
- Consumes: existing `readout_common` module-level `STATUS`, `read_event(io)` (returns `(event, flags, data, ts)`), `POP`.
- Produces (used by Task 3): `drain_events(io, on_event, idle_cb=None, poll_s=0.001)`. Calls `on_event(evt)` per drained event where `evt` is `{"event": int, "flags": int, "data": int, "ts": int, "is_tclk": 0|1, "has_data": 0|1}`; calls `idle_cb()` roughly once per second while the FIFO is empty; returns on `KeyboardInterrupt`. Does NOT filter anything (source-agnostic).

- [ ] **Step 1: Write the failing test**

Add to `deploy/test_readout_common.py`, immediately before the `if __name__ == "__main__":` block:

```python
def test_drain_events_decodes_and_pops():
    import readout_common as rc
    from readout_common import STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP

    class FakeIO:
        def __init__(self, events):
            self.events = events   # list of (event, flags, data, ts)
            self.i = 0
            self.regs = {}
        def rd(self, o):
            if o == STATUS:
                if self.i >= len(self.events):
                    raise KeyboardInterrupt   # ends drain_events cleanly
                ev, fl, data, ts = self.events[self.i]
                self.regs = {
                    EVENT: (fl << 16) | (ev & 0xFFFF),
                    DATA_HI: (data >> 32) & 0xFFFFFFFF, DATA_LO: data & 0xFFFFFFFF,
                    TS_HI: (ts >> 32) & 0xFFFFFFFF, TS_LO: ts & 0xFFFFFFFF,
                }
                return 0   # not empty
            return self.regs.get(o, 0)
        def wr(self, o, v=0):
            if o == POP:
                self.i += 1

    SEC = 1_751_800_000
    events = [
        (0x07, 0x03, 0xABCD, (SEC << 32) | 1500),   # is_tclk=1, has_data=1
        (0x18, 0x02, 0,      0),                      # is_tclk=1, has_data=0, UNSYNC
    ]
    got = []
    io = FakeIO(events)
    rc.drain_events(io, lambda e: got.append(e), idle_cb=None, poll_s=0)
    assert len(got) == 2, got
    assert got[0] == {"event": 0x07, "flags": 0x03, "data": 0xABCD,
                      "ts": (SEC << 32) | 1500, "is_tclk": 1, "has_data": 1}, got[0]
    assert got[1]["event"] == 0x18 and got[1]["ts"] == 0
    assert got[1]["is_tclk"] == 1 and got[1]["has_data"] == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `& .venv\Scripts\python.exe deploy\test_readout_common.py`
Expected: FAIL with `AttributeError: module 'readout_common' has no attribute 'drain_events'`.

- [ ] **Step 3: Implement `drain_events`**

In `deploy/readout_common.py`, add this function immediately after the `stream_events` function:

```python
def drain_events(io, on_event, idle_cb=None, poll_s=0.001):
    """Shared drain loop for the Redis publisher: poll STATUS, and for each buffered
    event call on_event(evt) with a decoded dict, popping it from the FIFO. While the
    FIFO is empty, call idle_cb() at most once per second (for a stats line). Returns
    on KeyboardInterrupt. Source-agnostic: does NOT filter (the publisher drops
    UNSYNC). The console readers use stream_events instead; this is a separate, simpler
    loop kept deliberately (see the design doc)."""
    last_idle = time.monotonic()
    try:
        while True:
            if io.rd(STATUS) & 0x1:                     # empty
                if idle_cb is not None:
                    now = time.monotonic()
                    if now - last_idle >= 1.0:
                        idle_cb()
                        last_idle = now
                time.sleep(poll_s)
                continue
            event, flags, data, ts = read_event(io)
            on_event({
                "event": event, "flags": flags, "data": data, "ts": ts,
                "is_tclk": (flags >> 1) & 1, "has_data": flags & 1,
            })
    except KeyboardInterrupt:
        pass
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `& .venv\Scripts\python.exe deploy\test_readout_common.py`
Expected: PASS, ending with `all readout_common tests passed` (now including `ok: test_drain_events_decodes_and_pops`).

- [ ] **Step 5: Commit**

```bash
git add deploy/readout_common.py deploy/test_readout_common.py
git commit -m "feat(deploy): drain_events shared readout drain helper (callback per event, for the Redis publisher)"
```

---

### Task 2: `RedisSink` background writer

**Files:**
- Create: `deploy/redis_sink.py`
- Create: `deploy/test_redis_sink.py`

**Interfaces:**
- Consumes: nothing from the repo (stdlib only; `redis` imported lazily).
- Produces (used by Task 3):
  - `RedisSink(stream, host="127.0.0.1", port=6379, maxlen=1_000_000, queue_size=100_000, batch=1000, connect=None)`. `connect` is an optional zero-arg factory returning a Redis-client-like object (default builds a real `redis.Redis`); tests inject a stub.
  - `.submit(fields)` (producer side; never blocks; drops oldest on a full queue, counting it).
  - `.start()` / `.stop(timeout=2.0)` (start/join the writer thread; stop best-effort flushes).
  - `.stats()` -> `{"published", "queue_dropped", "redis_dropped", "reconnects", "queued"}`.
  - The client-like object must support `.pipeline(transaction=False)` returning an object with `.xadd(stream, fields, maxlen=..., approximate=True)` and `.execute()`.

- [ ] **Step 1: Write the failing test**

Create `deploy/test_redis_sink.py`:

```python
"""Unit tests for redis_sink.RedisSink (no hardware, no Redis server).
A stub Redis records XADDs and can be told to fail to exercise reconnect.
Run: python deploy/test_redis_sink.py   or   pytest deploy -q"""
import time

from redis_sink import RedisSink


class FakePipe:
    def __init__(self, added, fail):
        self.added = added
        self.fail = fail
        self.ops = []

    def xadd(self, stream, fields, maxlen=None, approximate=None):
        self.ops.append((stream, dict(fields), maxlen, approximate))

    def execute(self):
        if self.fail:
            raise RuntimeError("redis down")
        self.added.extend(self.ops)
        self.ops = []


class FakeRedis:
    """Records committed XADDs in `added`. First `fail_times` pipelines raise on
    execute(), to exercise the sink's reconnect/drop path."""
    def __init__(self, fail_times=0):
        self.added = []
        self.fail_times = fail_times
        self.pipelines = 0

    def pipeline(self, transaction=False):
        self.pipelines += 1
        fail = self.fail_times > 0
        if fail:
            self.fail_times -= 1
        return FakePipe(self.added, fail)


def _wait(pred, timeout=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _fields(n):
    return {"sec": str(n), "ns": "0", "event": "7", "src": "tclk"}


def test_field_mapping_and_maxlen():
    fake = FakeRedis()
    sink = RedisSink("events:tclk", maxlen=555, connect=lambda: fake)
    sink.start()
    sink.submit(_fields(1))
    assert _wait(lambda: len(fake.added) >= 1), sink.stats()
    sink.stop()
    stream, fields, maxlen, approx = fake.added[0]
    assert stream == "events:tclk"
    assert fields == _fields(1)
    assert maxlen == 555 and approx is True
    assert sink.stats()["published"] == 1


def test_queue_full_drops_oldest():
    # No writer started, so the queue never drains: submit 3 into a size-2 queue.
    sink = RedisSink("s", queue_size=2, connect=lambda: FakeRedis())
    sink.submit(_fields(1))   # A
    sink.submit(_fields(2))   # B
    sink.submit(_fields(3))   # C -> full, drop oldest (A), enqueue C -> [B, C]
    assert sink.stats()["queue_dropped"] == 1
    drained = [sink._q.get_nowait(), sink._q.get_nowait()]
    assert drained == [_fields(2), _fields(3)]


def test_reconnect_after_error():
    fake = FakeRedis(fail_times=1)          # first execute() raises
    sink = RedisSink("s", connect=lambda: fake)
    sink.start()
    sink.submit(_fields(1))                 # first batch fails -> redis_dropped, reconnect
    assert _wait(lambda: sink.stats()["reconnects"] >= 1), sink.stats()
    sink.submit(_fields(2))                 # after reconnect, this one lands
    assert _wait(lambda: sink.stats()["published"] >= 1), sink.stats()
    sink.stop()
    assert sink.stats()["redis_dropped"] >= 1


def test_stop_flushes_queue():
    fake = FakeRedis()
    sink = RedisSink("s", connect=lambda: fake)
    for i in range(50):
        sink.submit(_fields(i))
    sink.start()
    sink.stop(timeout=3.0)
    assert len(fake.added) == 50
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
Expected: FAIL with `ModuleNotFoundError: No module named 'redis_sink'`.

- [ ] **Step 3: Implement `RedisSink`**

Create `deploy/redis_sink.py`:

```python
"""Background Redis Streams writer for the readout publisher.

A bounded in-process queue decouples the caller (the UIO drain thread) from Redis
latency: submit() never blocks; if the queue is full it drops the OLDEST entry
(counted) so the hardware FIFO drain can never stall on a Redis hiccup. A writer
thread pops entries in batches and pipelines XADD into one stream with MAXLEN ~.
On any Redis error it counts the dropped batch, reconnects with backoff, and
continues.

Redis is reached through an injected `connect` factory (default: a real redis-py
client). redis-py is imported lazily inside that factory so this module imports
cleanly on a machine without redis-py and the unit tests run with a stub."""
import queue
import threading
import time


def _default_connect(host, port):
    import redis   # lazy: module imports without redis-py present (PC unit tests)
    return redis.Redis(host=host, port=port,
                       socket_connect_timeout=1.0, socket_timeout=1.0)


class RedisSink:
    def __init__(self, stream, host="127.0.0.1", port=6379, maxlen=1_000_000,
                 queue_size=100_000, batch=1000, connect=None):
        self.stream = stream
        self.maxlen = maxlen
        self.batch = batch
        self._connect = connect or (lambda: _default_connect(host, port))
        self._q = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.published = 0
        self.queue_dropped = 0
        self.redis_dropped = 0
        self.reconnects = 0

    # ---- producer side (drain thread) ----
    def submit(self, fields):
        """Enqueue one ready-to-XADD field dict. Never blocks: on a full queue drop
        the OLDEST entry (counted), then enqueue this one."""
        try:
            self._q.put_nowait(fields)
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
            self._q.put_nowait(fields)
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
                pipe = client.pipeline(transaction=False)
                for fields in batch:
                    pipe.xadd(self.stream, fields, maxlen=self.maxlen, approximate=True)
                pipe.execute()
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
Expected: PASS, ending with `all redis_sink tests passed` (4 tests).

- [ ] **Step 5: Commit**

```bash
git add deploy/redis_sink.py deploy/test_redis_sink.py
git commit -m "feat(deploy): RedisSink background XADD writer (bounded queue, drop-oldest, reconnect, MAXLEN cap)"
```

---

### Task 3: `redis_publish.py` entry point

**Files:**
- Create: `deploy/redis_publish.py`
- Create: `deploy/test_redis_publish.py`

**Interfaces:**
- Consumes: `readout_common` (`line_buffer_stdout`, `parse_args`, `open_dev`, `drain_events`, `wr_split`, `wr_utc`, `say`); `redis_sink.RedisSink`.
- Produces (tested here): `event_fields(event, flags, data, ts, src) -> dict` (the string-valued Stream schema) and `should_publish(ts) -> bool` (False for UNSYNC ts==0). `main(argv)` wires open_dev + RedisSink + drain_events (board integration, not unit tested).

- [ ] **Step 1: Write the failing test**

Create `deploy/test_redis_publish.py`:

```python
"""Unit tests for redis_publish pure helpers (no hardware, no Redis).
Run: python deploy/test_redis_publish.py   or   pytest deploy -q"""
from redis_publish import event_fields, should_publish


def test_event_fields_schema():
    SEC = 1_751_800_000
    f = event_fields(0x07, 0x03, 0xABCD, (SEC << 32) | 1500, "tclk")
    assert f["sec"] == str(SEC) and f["ns"] == "1500"
    assert f["event"] == "7" and f["data"] == str(0xABCD)
    assert f["is_tclk"] == "1" and f["has_data"] == "1"
    assert f["src"] == "tclk"
    assert f["utc"].startswith("20") and f["utc"].endswith("Z")
    assert set(f.keys()) == {"sec", "ns", "utc", "event", "data",
                             "is_tclk", "has_data", "src"}
    assert all(isinstance(v, str) for v in f.values())


def test_event_fields_flag_variants():
    f = event_fields(0x18, 0x00, 0, (1 << 32), "aclk")   # is_tclk=0 has_data=0
    assert f["is_tclk"] == "0" and f["has_data"] == "0" and f["src"] == "aclk"


def test_should_publish_drops_unsync():
    assert should_publish(0) is False
    assert should_publish((1 << 32) | 5) is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all redis_publish tests passed")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `& .venv\Scripts\python.exe deploy\test_redis_publish.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'redis_publish'`.

- [ ] **Step 3: Implement `redis_publish.py`**

Create `deploy/redis_publish.py`:

```python
#!/usr/bin/env python3
"""Publish WR-timestamped readout events to a local Redis Stream.

Drains one UIO readout (TCLK or ACLK), drops UNSYNC events (ts==0), and submits the
rest to a background RedisSink that XADDs them to `--stream`. Two threads: this
(main) thread drains the FIFO and enqueues; the sink's writer thread talks to Redis,
so a Redis stall never stalls the hardware FIFO drain.

    sudo python3 redis_publish.py /dev/uio4 --stream events:tclk --src tclk
    sudo python3 redis_publish.py /dev/uio5 --stream events:aclk --src aclk

Ctrl-C to stop (flushes the queue, prints final stats).

Register block base 0x8000_0000 (TCLK) / 0x8001_0000 (ACLK); read via the UIO
mapping, same as tclk_read.py / aclkgt_read.py. Needs redis-py on the board
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


def main(argv):
    rc.line_buffer_stdout()
    pos, flags = rc.parse_args(
        argv, value_flags=("--stream", "--src", "--redis-host", "--redis-port",
                           "--maxlen", "--queue-size"))
    dev    = pos[0] if pos else "/dev/uio4"
    stream = flags.get("--stream", "events:tclk")
    src    = flags.get("--src", "tclk")
    host   = flags.get("--redis-host", "127.0.0.1")
    port   = int(flags.get("--redis-port", "6379"))
    maxlen = int(flags.get("--maxlen", "1000000"))
    qsize  = int(flags.get("--queue-size", "100000"))

    io = rc.open_dev(dev)
    sink = RedisSink(stream, host=host, port=port, maxlen=maxlen, queue_size=qsize)
    sink.start()
    say("# publishing %s events from %s to Redis stream '%s' (%s:%d). Ctrl-C to stop."
        % (src, dev, stream, host, port))

    drained = [0]

    def on_event(e):
        if not should_publish(e["ts"]):        # UNSYNC: dropped by design
            return
        drained[0] += 1
        sink.submit(event_fields(e["event"], e["flags"], e["data"], e["ts"], src))

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
Expected: PASS, ending with `all redis_publish tests passed` (3 tests).

- [ ] **Step 5: Confirm the module imports without redis-py (lazy-import guard)**

Run: `& .venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'deploy'); import redis_publish; print('import ok')"`
Expected: `import ok` (proves neither `redis_publish` nor `redis_sink` imports `redis` at load time; the dev venv has no `redis` installed).

- [ ] **Step 6: Commit**

```bash
git add deploy/redis_publish.py deploy/test_redis_publish.py
git commit -m "feat(deploy): redis_publish entry point (per-source UIO drain -> Redis Stream, UNSYNC dropped)"
```

---

### Task 4: Deploy plumbing, dependency, and runbook

**Files:**
- Create: `deploy/requirements-board.txt`
- Modify: `deploy/redis.md` (create)
- Modify: `hw.ps1` (the `aclk_pipeline` entry of `$pyMap`, around line 240)
- Modify: `docs/FUNCTIONALITY.md` (deploy section)

**Interfaces:**
- Consumes: the three new deploy files from Tasks 1-3.
- Produces: a deployable file set + board runbook. No code interface.

- [ ] **Step 1: Create the board requirements file**

Create `deploy/requirements-board.txt`:

```
# Board-side Python deps for the KR260 readout tooling (NOT the sim venv; the sim
# deps are in the repo-root requirements.txt). Install on the board with:
#   pip install -r requirements-board.txt
redis
```

- [ ] **Step 2: Add the publisher files (and the missing .dts) to the deploy map**

In `hw.ps1`, the `aclk_pipeline` line of `$pyMap` currently reads:

```powershell
            "aclk_pipeline"   = @("tclk_read.py", "aclkgt_read.py", "wr_time.py", "tclk_filter.py", "readout_common.py")
```

Replace it with (adds the two publisher modules, the board requirements file, and the `aclk_pipeline.dts` overlay that this session's WR bring-up found was never shipped):

```powershell
            "aclk_pipeline"   = @("tclk_read.py", "aclkgt_read.py", "wr_time.py", "tclk_filter.py", "readout_common.py", "redis_sink.py", "redis_publish.py", "requirements-board.txt", "aclk_pipeline.dts")
```

- [ ] **Step 3: Verify the deploy map references only existing files**

Run: `& .venv\Scripts\python.exe -c "import pathlib; base=pathlib.Path('deploy'); missing=[f for f in ['tclk_read.py','aclkgt_read.py','wr_time.py','tclk_filter.py','readout_common.py','redis_sink.py','redis_publish.py','requirements-board.txt','aclk_pipeline.dts'] if not (base/f).exists()]; print('missing:', missing); assert not missing"`
Expected: `missing: []` (all files the `aclk_pipeline` map now lists exist in `deploy/`, so `hw.ps1 deploy` will not throw its "deploy file missing" error).

- [ ] **Step 4: Write the board runbook**

Create `deploy/redis.md`:

```markdown
# Redis event publishing (board-side)

Publishes WR-timestamped TCLK/ACLK readout events into local Redis Streams so
other processes on the KR260 can consume a durable, ordered feed. Publish side
only. UNSYNC events (ts==0, WR timebase not locked) are dropped, so arm the WR
timebase first (see wr.md).

## One-time setup on the board

    sudo apt update && sudo apt install -y redis-server
    sudo systemctl enable --now redis-server
    redis-cli ping                       # -> PONG
    pip install -r requirements-board.txt   # installs redis-py

## Run (one publisher per source; after the WR timebase is armed + locked)

    sudo python3 redis_publish.py /dev/uio4 --stream events:tclk --src tclk
    sudo python3 redis_publish.py /dev/uio5 --stream events:aclk --src aclk

Match the /dev/uioN indices to the readout names with:
    grep . /sys/class/uio/uio*/name

Ctrl-C stops a publisher (it flushes the queue and prints final stats). The 1 Hz
stats line reports drained / published / queued / queue_dropped / redis_dropped /
reconnects.

Options: --redis-host (default 127.0.0.1), --redis-port (6379), --maxlen (stream
cap, default 1000000), --queue-size (in-process queue, default 100000).

## Verify

    redis-cli XLEN events:tclk                       # climbs while publishing
    redis-cli XREVRANGE events:tclk + - COUNT 5      # newest 5 entries

Each entry carries: sec, ns, utc, event, data, is_tclk, has_data, src. Cross-check
against the console reader (they read the same FIFO, so the same events/timestamps
appear):
    sudo python3 tclk_read.py /dev/uio4 --wr

## Gotchas

- Nothing publishes until the WR timebase is armed and locked (UNSYNC events are
  dropped). If XLEN stays 0, run: sudo python3 wr_time.py /dev/uio6 status
- The publisher never blocks the hardware FIFO drain on a Redis stall: it drops the
  oldest queued entries (queue_dropped climbs) rather than stalling. A rising
  queue_dropped / redis_dropped means Redis is not keeping up.
- Streams are capped at --maxlen (approximate); old entries are trimmed by Redis.
- redis-server binds localhost by default; keep it that way (no auth is configured).
```

- [ ] **Step 5: Document the tools in FUNCTIONALITY.md**

In `docs/FUNCTIONALITY.md`, section 5 (Deploy), add these two bullets to the deploy tool list (after the `wr_time.py` bullet added earlier this session):

```markdown
- **redis_sink.py** (+ test_redis_sink.py): background Redis Streams writer. Bounded in-process queue + writer thread pipelines XADD (MAXLEN ~ cap); submit() never blocks (drops oldest, counted) so a Redis stall cannot stall the UIO drain; auto-reconnects. redis-py imported lazily so PC unit tests need no server.
- **redis_publish.py** (+ test_redis_publish.py): per-source publisher. Drains one UIO readout via readout_common.drain_events, drops UNSYNC events, and XADDs sec/ns/utc/event/data/is_tclk/has_data/src to a Redis Stream (events:tclk from uio4, events:aclk from uio5). Runbook: deploy/redis.md; board dep in deploy/requirements-board.txt.
```

Also, in the `Artifacts` line of section 5, after the `aclk_pipeline.dts` note, append: `; requirements-board.txt (board-only pip deps: redis-py)`.

- [ ] **Step 6: Run the full deploy-side unit suite (regression)**

Run each and confirm the all-passed line:

```powershell
& .venv\Scripts\python.exe deploy\test_readout_common.py
& .venv\Scripts\python.exe deploy\test_redis_sink.py
& .venv\Scripts\python.exe deploy\test_redis_publish.py
& .venv\Scripts\python.exe deploy\test_wr_time.py
& .venv\Scripts\python.exe deploy\test_tclk_filter.py
```

Expected: every file prints its `all ... passed` line, no tracebacks.

- [ ] **Step 7: Commit**

```bash
git add hw.ps1 deploy/requirements-board.txt deploy/redis.md docs/FUNCTIONALITY.md
git commit -m "feat(deploy): ship redis publisher + board deps + redis.md runbook; add aclk_pipeline.dts to deploy map"
```

---

## Post-plan gates (not automated here)

1. Board integration (in `deploy/redis.md`): with the WR timebase armed + locked, `redis-server` running, and `redis-py` installed, run both publishers and confirm `XLEN` climbs and `XREVRANGE` fields match `tclk_read.py --wr`. The dev environment has no board or Redis, so this is manual.
2. The publisher's threaded drain/writer path is exercised by the unit tests via the stub Redis; the real `redis.Redis` client path (the lazy `_default_connect`) is covered only on the board.
