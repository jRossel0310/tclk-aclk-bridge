# Unattended TCLK/ACLK Capture with Stats Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the KR260 pipeline capture TCLK and ACLK events unattended for a day, record a durable time-series of every relevant counter, and reconcile hardware truth against what was published (events published / missed / failed CRCs).

**Architecture:** The existing publisher owns each `/dev/uioN` and drains its FIFO. We add a wall-clock snapshot tick that reads the read-only PL counters plus the publisher's software counters and appends one JSON line per snapshot to an on-disk log. A tmux launcher runs the two publishers unattended; a board-side report tool reconciles the log (no device access, so it never races the live publisher); a PC-side script plots the time-series.

**Tech Stack:** Python 3 (stdlib only on the board), bash + tmux (launcher), matplotlib (PC-side plotting only), JSONL on-disk logs. Board register access via the existing `readout_common.RegIO` mmap.

## Global Constraints

- **Additive only.** No RTL, no bitstream, no Redis key-scheme change. Existing publish behavior is unchanged.
- **Board Python is stdlib-only and must import without `redis` or `matplotlib`.** The lazy-import guard in `redis_sink.py` must keep working; new board modules (`stats_log.py`, `stats_report.py`, `redis_publish.py`) import only stdlib.
- **No em dashes anywhere** (project style), in code comments, docs, or commit messages.
- **Tests run two ways:** `python <test_file>.py` (the repo's `__main__` runner) and `pytest deploy -q`. Test functions take no arguments (no pytest fixtures), matching the existing files.
- **Register semantics are authoritative from `rtl/aclk_readout/aclk_readout_axi.sv`:** `EVENT_COUNT` (0x70) counts events presented to the FIFO (including overflow-lost); `ERROR_COUNT` (0x90) is bad-CRC/decode errors; `NULL_COUNT` (0x80) idle drops; `FILTERED_COUNT` (0xE0) drop-mask; `STATUS` bit1 is the sticky overflow flag; `LOCK` bit0 is MMCM lock; reading any register has no side effect (only writing `POP` at 0x60 advances the FIFO).
- **Snapshot record schema (JSONL, one line per snapshot per source):**
  ```json
  {"utc":"2026-07-15T14:03:00Z","mono":1234.5,"src":"tclk",
   "hw":{"event_count":91234,"null_count":0,"error_count":7,"filtered_count":0,"overflow":0,"lock":1,"heartbeat":88123456},
   "sw":{"drained":91230,"unsync":3,"published":91230,"queued":0,"queue_dropped":0,"redis_dropped":0,"reconnects":0}}
  ```
- **Reconciliation math:** hardware metrics are baseline(first)-to-last deltas; software metrics are the last snapshot's cumulative totals. `Missed at HW = decodedDelta - drained - unsync` (FIFO overflow loss), tolerated to +/- one FIFO depth (64) of residual and cross-checked against the sticky overflow bit.

---

## File Structure

**Modify:**
- `deploy/readout_common.py` — add `read_hw_counters(io)`; add `tick_cb`/`tick_s` to `drain_events`.
- `deploy/redis_publish.py` — add `PublisherState`; wire snapshot writer + baseline/periodic/final snapshots; new `--statlog` / `--snapshot-interval` flags; add `unsync` to the stats line.
- `deploy/test_readout_common.py` — tests for `read_hw_counters` and the busy-FIFO tick.
- `deploy/test_redis_publish.py` — test for `PublisherState`.
- `deploy/redis.md` — pointer to the new capture runbook.
- `hw.ps1` — add new board files to the `aclk_pipeline` deploy map.

**Create:**
- `deploy/stats_log.py` — `now_utc()`, `sw_counters()`, `build_snapshot()`, `StatsLog`.
- `deploy/test_stats_log.py` — unit tests for the above.
- `deploy/stats_report.py` — `load_snapshots()`, `group_by_src()`, `reconcile()`, `format_report()`, `main()`.
- `deploy/test_stats_report.py` — unit tests for reconciliation + cross-check.
- `deploy/run_pipeline.sh` — tmux launcher with Redis + WR pre-flight.
- `deploy/plot_stats.py` — PC-side matplotlib plotter (not run on the board).
- `deploy/capture.md` — the unattended-run runbook.

---

## Task 1: Hardware-counter snapshot helper + busy-FIFO tick

**Files:**
- Modify: `deploy/readout_common.py` (add `read_hw_counters`; extend `drain_events` signature/loop)
- Test: `deploy/test_readout_common.py`

**Interfaces:**
- Produces: `read_hw_counters(io) -> dict` with keys `event_count, null_count, error_count, filtered_count, overflow, lock, heartbeat` (all ints; `overflow` and `lock` are 0/1).
- Produces: `drain_events(io, on_event, idle_cb=None, poll_s=0.001, tick_cb=None, tick_s=60.0)` — `tick_cb()` fires at most once per `tick_s` seconds, evaluated every loop iteration so it fires whether the FIFO is busy or idle.

- [ ] **Step 1: Write the failing tests**

Add to `deploy/test_readout_common.py`. First extend the imports at the top of the file:

```python
from readout_common import (
    RegIO, read_event, parse_args, dev_offset, apply_drop_filter,
    STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, FILTER_CFG, NAME, GT_CTRL,
    read_hw_counters,
    EVENT_COUNT, NULL_COUNT, ERROR_COUNT, FILTERED_COUNT, LOCK, HEARTBEAT,
)
```

Then append these two tests:

```python
def test_read_hw_counters_decodes_all_regs():
    io = make_io()
    io.wr(EVENT_COUNT, 1000)
    io.wr(NULL_COUNT, 5)
    io.wr(ERROR_COUNT, 7)
    io.wr(FILTERED_COUNT, 3)
    io.wr(LOCK, 1)
    io.wr(HEARTBEAT, 0x00ABCDEF)
    io.wr(STATUS, 0b10)                 # overflow bit set, empty bit clear
    hw = read_hw_counters(io)
    assert hw == {"event_count": 1000, "null_count": 5, "error_count": 7,
                  "filtered_count": 3, "overflow": 1, "lock": 1,
                  "heartbeat": 0x00ABCDEF}
    io.wr(STATUS, 0b01)                 # empty set, overflow clear
    io.wr(LOCK, 0)
    hw = read_hw_counters(io)
    assert hw["overflow"] == 0 and hw["lock"] == 0


def test_drain_events_tick_fires_when_fifo_busy():
    import readout_common as rc
    from readout_common import STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP

    class BusyIO:
        # STATUS always reads not-empty (bit0=0) until the events run out, then raises
        # KeyboardInterrupt. So idle_cb (empty-only) must NEVER fire, but tick_cb must.
        def __init__(self, n):
            self.n = n
            self.i = 0
        def rd(self, o):
            if o == STATUS:
                if self.i >= self.n:
                    raise KeyboardInterrupt
                return 0                # not empty
            return 0
        def wr(self, o, v=0):
            if o == POP:
                self.i += 1

    ticks = [0]
    idles = [0]
    io = BusyIO(3)
    rc.drain_events(io, on_event=lambda e: None,
                    idle_cb=lambda: idles.__setitem__(0, idles[0] + 1),
                    poll_s=0,
                    tick_cb=lambda: ticks.__setitem__(0, ticks[0] + 1),
                    tick_s=0.0)          # tick_s=0 -> fires every iteration
    assert idles[0] == 0, "idle_cb must not fire on a never-empty FIFO"
    assert ticks[0] >= 3, ticks[0]       # fired on each busy iteration
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd deploy && python -m pytest test_readout_common.py::test_read_hw_counters_decodes_all_regs test_readout_common.py::test_drain_events_tick_fires_when_fifo_busy -q`
Expected: FAIL (ImportError: cannot import name `read_hw_counters`, or `drain_events() got an unexpected keyword argument 'tick_cb'`).

- [ ] **Step 3: Implement `read_hw_counters`**

Add to `deploy/readout_common.py`, right after the `read_event` function (near line 165):

```python
def read_hw_counters(io):
    """Snapshot the read-only diagnostic counters for one readout block. Reads only
    (no POP), so it is safe to call from the drain thread without disturbing the FIFO.
    overflow is STATUS bit1 (sticky: an enqueued event was lost to a full FIFO); lock
    is LOCK bit0 (MMCM locked)."""
    status = io.rd(STATUS)
    return {
        "event_count":    io.rd(EVENT_COUNT),
        "null_count":     io.rd(NULL_COUNT),
        "error_count":    io.rd(ERROR_COUNT),
        "filtered_count": io.rd(FILTERED_COUNT),
        "overflow":       (status >> 1) & 1,
        "lock":           io.rd(LOCK) & 1,
        "heartbeat":      io.rd(HEARTBEAT),
    }
```

- [ ] **Step 4: Extend `drain_events` with the wall-clock tick**

Replace the whole `drain_events` function in `deploy/readout_common.py` (currently lines ~236-260) with:

```python
def drain_events(io, on_event, idle_cb=None, poll_s=0.001, tick_cb=None, tick_s=60.0):
    """Shared drain loop for the Redis publisher: poll STATUS, and for each buffered
    event call on_event(evt) with a decoded dict, popping it from the FIFO. While the
    FIFO is empty, call idle_cb() at most once per second (for a stats line). Separately,
    if tick_cb is given, call it at most once per tick_s seconds regardless of whether the
    FIFO is busy or idle (for the periodic stats snapshot); a sustained-busy run would
    never idle, so the snapshot cannot ride on idle_cb. Returns on KeyboardInterrupt.
    Source-agnostic: does NOT filter (the publisher drops UNSYNC). The console readers use
    stream_events instead; this is a separate, simpler loop kept deliberately."""
    last_idle = time.monotonic()
    last_tick = time.monotonic()
    try:
        while True:
            if tick_cb is not None:
                now = time.monotonic()
                if now - last_tick >= tick_s:
                    tick_cb()
                    last_tick = now
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

- [ ] **Step 5: Run the tests to verify they pass (and nothing regressed)**

Run: `cd deploy && python -m pytest test_readout_common.py -q`
Expected: PASS (all tests, including the pre-existing `test_drain_events_decodes_and_pops`).

- [ ] **Step 6: Commit**

```bash
git add deploy/readout_common.py deploy/test_readout_common.py
git commit -m "feat(readout): read_hw_counters + wall-clock tick in drain_events"
```

---

## Task 2: `stats_log.py` (snapshot record + JSONL writer)

**Files:**
- Create: `deploy/stats_log.py`
- Test: `deploy/test_stats_log.py`

**Interfaces:**
- Produces: `now_utc() -> str` (ISO-8601 `...Z`, seconds resolution).
- Produces: `sw_counters(drained, unsync, sink_stats) -> dict` with keys `drained, unsync, published, queued, queue_dropped, redis_dropped, reconnects`. `sink_stats` is the dict from `RedisSink.stats()` (keys `published, queued, queue_dropped, redis_dropped, reconnects`).
- Produces: `build_snapshot(utc, mono, src, hw, sw) -> dict` = `{"utc","mono","src","hw","sw"}`, with `hw`/`sw` copied.
- Produces: `StatsLog(path)` with `.write(record)` (append one JSON line, flush) and `.close()`.

- [ ] **Step 1: Write the failing tests**

Create `deploy/test_stats_log.py`:

```python
"""Unit tests for stats_log (no hardware, no Redis, no matplotlib).
Run: python test_stats_log.py   or   pytest deploy -q"""
import json
import os
import tempfile

from stats_log import now_utc, sw_counters, build_snapshot, StatsLog


def test_now_utc_format():
    s = now_utc()
    assert s.endswith("Z") and "T" in s and s[4] == "-" and s[7] == "-"


def test_sw_counters_maps_sink_stats():
    stats = {"published": 100, "queued": 2, "queue_dropped": 3,
             "redis_dropped": 4, "reconnects": 5}
    sw = sw_counters(drained=90, unsync=10, sink_stats=stats)
    assert sw == {"drained": 90, "unsync": 10, "published": 100, "queued": 2,
                  "queue_dropped": 3, "redis_dropped": 4, "reconnects": 5}


def test_build_snapshot_schema_and_copies():
    hw = {"event_count": 10, "null_count": 0, "error_count": 1, "filtered_count": 0,
          "overflow": 0, "lock": 1, "heartbeat": 99}
    sw = {"drained": 9, "unsync": 1, "published": 9, "queued": 0,
          "queue_dropped": 0, "redis_dropped": 0, "reconnects": 0}
    rec = build_snapshot("2026-07-15T00:00:00Z", 12.5, "tclk", hw, sw)
    assert rec["utc"] == "2026-07-15T00:00:00Z"
    assert rec["mono"] == 12.5 and rec["src"] == "tclk"
    assert rec["hw"] == hw and rec["sw"] == sw
    hw["event_count"] = 999                      # build_snapshot must have copied hw
    assert rec["hw"]["event_count"] == 10


def test_statslog_roundtrip():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        log = StatsLog(path)
        r1 = build_snapshot("t1", 1.0, "tclk", {"a": 1}, {"b": 2})
        r2 = build_snapshot("t2", 2.0, "tclk", {"a": 3}, {"b": 4})
        log.write(r1)
        log.write(r2)
        log.close()
        with open(path) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert lines == [r1, r2]
    finally:
        os.remove(path)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all stats_log tests passed")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd deploy && python -m pytest test_stats_log.py -q`
Expected: FAIL (ModuleNotFoundError: No module named `stats_log`).

- [ ] **Step 3: Implement `stats_log.py`**

Create `deploy/stats_log.py`:

```python
"""On-disk JSONL stats log for the KR260 capture publisher.

build_snapshot() assembles one snapshot record (pure); StatsLog appends records as JSON
lines, line-buffered and flushed so a crash never loses a completed snapshot. The report
tool (stats_report.py) reads these files back. Nothing here imports redis or matplotlib,
so it loads on any machine and the unit tests need no hardware."""
import json
import time


def now_utc():
    """Wall-clock UTC of the snapshot, as an ISO-8601 'Z' string (seconds resolution)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sw_counters(drained, unsync, sink_stats):
    """Software-side counters for a snapshot: the publisher's drained/unsync plus the
    RedisSink stats (published/queued/queue_dropped/redis_dropped/reconnects)."""
    return {
        "drained": drained, "unsync": unsync,
        "published": sink_stats["published"], "queued": sink_stats["queued"],
        "queue_dropped": sink_stats["queue_dropped"],
        "redis_dropped": sink_stats["redis_dropped"],
        "reconnects": sink_stats["reconnects"],
    }


def build_snapshot(utc, mono, src, hw, sw):
    """Assemble one snapshot record. hw/sw are copied so a later in-place mutation of the
    caller's counter dicts cannot rewrite an already-built record."""
    return {"utc": utc, "mono": mono, "src": src, "hw": dict(hw), "sw": dict(sw)}


class StatsLog:
    """Append-only JSONL sink. One record per line, flushed immediately so a completed
    snapshot survives a crash."""

    def __init__(self, path):
        self.path = path
        self._f = open(path, "a", buffering=1)      # line-buffered append

    def write(self, record):
        self._f.write(json.dumps(record) + "\n")
        self._f.flush()

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd deploy && python -m pytest test_stats_log.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add deploy/stats_log.py deploy/test_stats_log.py
git commit -m "feat(stats): stats_log JSONL snapshot writer + record schema"
```

---

## Task 3: Wire the snapshot writer into the publisher

**Files:**
- Modify: `deploy/redis_publish.py` (add `PublisherState`; snapshot closure; new flags; baseline/periodic/final snapshots; `unsync` in the stats line)
- Test: `deploy/test_redis_publish.py`

**Interfaces:**
- Consumes: `read_hw_counters` (Task 1); `now_utc`, `sw_counters`, `build_snapshot`, `StatsLog` (Task 2); `RedisSink.stats()`; `build_record`, `should_publish` (existing).
- Produces: `PublisherState` with `.drained`, `.unsync` ints and `.note(ts) -> bool` (True = publish, False = UNSYNC dropped).

- [ ] **Step 1: Write the failing test**

Append to `deploy/test_redis_publish.py`:

```python
def test_publisher_state_counts():
    from redis_publish import PublisherState
    st = PublisherState()
    assert st.note(0) is False and st.unsync == 1 and st.drained == 0
    assert st.note((1 << 32) | 5) is True and st.drained == 1 and st.unsync == 1
    st.note(0)
    assert st.unsync == 2 and st.drained == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd deploy && python -m pytest test_redis_publish.py::test_publisher_state_counts -q`
Expected: FAIL (ImportError: cannot import name `PublisherState`).

- [ ] **Step 3: Update the imports and add `PublisherState`**

In `deploy/redis_publish.py`, replace the import block (lines 19-23) with:

```python
import sys
import time

import readout_common as rc
from readout_common import say, wr_split, wr_utc, read_hw_counters
from redis_sink import RedisSink
from stats_log import StatsLog, build_snapshot, sw_counters, now_utc
```

Then add this class just below the imports (before `event_fields`):

```python
class PublisherState:
    """Drain-side counters. note(ts) classifies one event: a real event increments
    `drained` and returns True (publish it); an UNSYNC event (ts==0, WR not locked when
    stamped) increments `unsync` and returns False (dropped by design, see
    should_publish)."""

    def __init__(self):
        self.drained = 0
        self.unsync = 0

    def note(self, ts):
        if should_publish(ts):
            self.drained += 1
            return True
        self.unsync += 1
        return False
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd deploy && python -m pytest test_redis_publish.py -q`
Expected: PASS (all tests including the new one).

- [ ] **Step 5: Rewrite `main` to snapshot at baseline / periodically / at shutdown**

Replace the entire `main` function in `deploy/redis_publish.py` (lines ~57-98) with:

```python
def main(argv):
    rc.line_buffer_stdout()
    pos, flags = rc.parse_args(
        argv, value_flags=("--src", "--namespace", "--redis-host", "--redis-port",
                           "--maxlen", "--queue-size", "--statlog", "--snapshot-interval"))
    dev      = pos[0] if pos else "/dev/uio4"
    src      = flags.get("--src", "tclk")
    ns       = flags.get("--namespace", "KR260")
    host     = flags.get("--redis-host", "127.0.0.1")
    port     = int(flags.get("--redis-port", "6379"))
    maxlen   = int(flags.get("--maxlen", "1000000"))
    qsize    = int(flags.get("--queue-size", "100000"))
    statpath = flags.get("--statlog", "stats-%s.jsonl" % src)
    interval = float(flags.get("--snapshot-interval", "60"))

    io = rc.open_dev(dev)
    sink = RedisSink(host=host, port=port, maxlen=maxlen, queue_size=qsize,
                     status_key="%s:status" % ns, watchdog_key="%s:watchdog" % ns)
    sink.start()
    stream = "%s:%s" % (ns, src)
    statlog = StatsLog(statpath)
    state = PublisherState()
    say("# publishing %s events from %s to Redis stream '%s' (%s:%d); stats -> %s every "
        "%gs. Ctrl-C to stop." % (src, dev, stream, host, port, statpath, interval))

    def on_event(e):
        if state.note(e["ts"]):
            sink.submit(build_record(ns, src, e["event"], e["flags"], e["data"], e["ts"]))

    def snapshot():
        statlog.write(build_snapshot(
            now_utc(), time.monotonic(), src,
            read_hw_counters(io), sw_counters(state.drained, state.unsync, sink.stats())))

    def stats_line():
        s = sink.stats()
        say("[stats] drained=%d unsync=%d published=%d queued=%d queue_dropped=%d "
            "redis_dropped=%d reconnects=%d" % (
                state.drained, state.unsync, s["published"], s["queued"],
                s["queue_dropped"], s["redis_dropped"], s["reconnects"]))

    snapshot()                                     # baseline before draining
    try:
        rc.drain_events(io, on_event, idle_cb=stats_line,
                        tick_cb=snapshot, tick_s=interval)
    finally:
        say("\n# stopping; flushing queue ...")
        sink.stop(timeout=3.0)
        snapshot()                                 # final, post-flush
        statlog.close()
        stats_line()
```

- [ ] **Step 6: Verify the module still imports without redis and all tests pass**

Run: `cd deploy && python -c "import redis_publish; print('import ok, no redis needed')" && python -m pytest test_redis_publish.py test_redis_sink.py test_readout_common.py -q`
Expected: prints `import ok, no redis needed`, then all tests PASS. (If `python -c "import redis_publish"` raises `ModuleNotFoundError: redis`, the lazy-import guard regressed; the top-level imports must not pull in `redis`.)

- [ ] **Step 7: Commit**

```bash
git add deploy/redis_publish.py deploy/test_redis_publish.py
git commit -m "feat(publish): baseline/periodic/final stats snapshots + unsync counter"
```

---

## Task 4: `stats_report.py` (reconciliation report)

**Files:**
- Create: `deploy/stats_report.py`
- Test: `deploy/test_stats_report.py`

**Interfaces:**
- Consumes: JSONL snapshot records (Task 2 schema).
- Produces: `load_snapshots(path) -> list[dict]`; `group_by_src(snaps) -> dict[str, list]`; `reconcile(snaps) -> dict`; `format_report(rec) -> str`; `main(argv)`.
- `reconcile(snaps)` returns keys: `src, snapshots, duration_s, decoded, published, failed_crc, nulls, filtered, missed_hw, missed_pub, reconnects, unsync, overflow_ever, lock_lost, xcheck`.

- [ ] **Step 1: Write the failing tests**

Create `deploy/test_stats_report.py`:

```python
"""Unit tests for stats_report reconciliation (no hardware, no Redis).
Run: python test_stats_report.py   or   pytest deploy -q"""
import json
import os
import tempfile

from stats_report import load_snapshots, group_by_src, reconcile, format_report


def _snap(src, mono, ev, err=0, nul=0, filt=0, ovf=0, lock=1,
          drained=0, unsync=0, published=0, qd=0, rd=0, rec=0):
    return {"utc": "t", "mono": mono, "src": src,
            "hw": {"event_count": ev, "null_count": nul, "error_count": err,
                   "filtered_count": filt, "overflow": ovf, "lock": lock, "heartbeat": 0},
            "sw": {"drained": drained, "unsync": unsync, "published": published,
                   "queued": 0, "queue_dropped": qd, "redis_dropped": rd, "reconnects": rec}}


def test_reconcile_basic_deltas():
    snaps = [
        _snap("tclk", 0.0, ev=100, err=1),                              # baseline
        _snap("tclk", 60.0, ev=1100, err=4, nul=0, filt=2,
              drained=980, unsync=18, published=975, qd=1, rd=1, rec=2),
    ]
    r = reconcile(snaps)
    assert r["src"] == "tclk" and r["snapshots"] == 2 and r["duration_s"] == 60.0
    assert r["decoded"] == 1000                    # 1100 - 100
    assert r["failed_crc"] == 3                     # 4 - 1
    assert r["filtered"] == 2 and r["nulls"] == 0
    assert r["published"] == 975
    assert r["missed_hw"] == 1000 - 980 - 18        # decodedDelta - drained - unsync = 2
    assert r["missed_pub"] == 2                      # qd + rd
    assert r["reconnects"] == 2 and r["unsync"] == 18
    assert r["overflow_ever"] == 0 and r["lock_lost"] == 0
    assert "clean" in r["xcheck"]                   # missed_hw within FIFO residual


def test_reconcile_overflow_crosscheck_flags():
    snaps = [
        _snap("aclk", 0.0, ev=0),
        _snap("aclk", 10.0, ev=5000, ovf=1, drained=4000, unsync=0, published=4000),
    ]
    r = reconcile(snaps)
    assert r["overflow_ever"] == 1
    assert r["missed_hw"] == 1000                    # 5000 - 4000 - 0
    assert "overflow" in r["xcheck"]                 # bit set: loss expected


def test_reconcile_warns_missed_without_overflow():
    snaps = [
        _snap("tclk", 0.0, ev=0),
        _snap("tclk", 10.0, ev=5000, ovf=0, drained=4000, unsync=0, published=4000),
    ]
    r = reconcile(snaps)
    assert r["overflow_ever"] == 0 and r["missed_hw"] == 1000
    assert r["xcheck"].startswith("WARN")            # loss but overflow bit never set


def test_reconcile_lock_lost_if_any_snapshot_unlocked():
    snaps = [_snap("tclk", 0.0, ev=0, lock=1),
             _snap("tclk", 5.0, ev=10, lock=0),
             _snap("tclk", 10.0, ev=20, lock=1)]
    assert reconcile(snaps)["lock_lost"] == 1


def test_group_by_src_splits_and_preserves_order():
    snaps = [_snap("tclk", 0.0, ev=0), _snap("aclk", 0.0, ev=0),
             _snap("tclk", 1.0, ev=5)]
    g = group_by_src(snaps)
    assert set(g) == {"tclk", "aclk"}
    assert [s["mono"] for s in g["tclk"]] == [0.0, 1.0]


def test_load_snapshots_roundtrip():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        rows = [_snap("tclk", 0.0, ev=0), _snap("tclk", 1.0, ev=9)]
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
            f.write("\n")                            # blank line must be skipped
        assert load_snapshots(path) == rows
    finally:
        os.remove(path)


def test_format_report_is_readable():
    r = reconcile([_snap("tclk", 0.0, ev=0),
                   _snap("tclk", 60.0, ev=600, drained=600, published=600)])
    text = format_report(r)
    assert "tclk" in text and "published" in text and "failed CRC" in text


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all stats_report tests passed")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd deploy && python -m pytest test_stats_report.py -q`
Expected: FAIL (ModuleNotFoundError: No module named `stats_report`).

- [ ] **Step 3: Implement `stats_report.py`**

Create `deploy/stats_report.py`:

```python
#!/usr/bin/env python3
"""Reconcile a KR260 capture stats log (JSONL) into a per-source report.

Pure reader: reads stats-*.jsonl only, never opens /dev/uio*, so it is safe to run while
the publishers are still live. Hardware counters are reconciled as baseline(first) to
last deltas; software counters are the last snapshot's cumulative totals.

    sudo python3 stats_report.py stats-tclk.jsonl stats-aclk.jsonl

For each source it prints: decoded (good events the PL enqueued), published, failed CRCs,
nulls/filtered, events missed at the hardware (FIFO overflow) and at the publisher
(queue+redis drops), reconnects, WR-lock health, and an overflow cross-check."""
import json
import sys

FIFO_RESIDUAL = 64      # FIFO depth (ADDR_WIDTH=6): tolerated |missed_hw| from residual


def load_snapshots(path):
    """Read a JSONL stats log into a list of records (blank lines skipped)."""
    snaps = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                snaps.append(json.loads(line))
    return snaps


def group_by_src(snaps):
    """Group snapshots by their 'src' field, preserving per-source order."""
    groups = {}
    for s in snaps:
        groups.setdefault(s["src"], []).append(s)
    return groups


def reconcile(snaps):
    """Reconcile time-ordered snapshots for ONE source into a summary dict."""
    f, l = snaps[0], snaps[-1]
    hwf, hwl, swl = f["hw"], l["hw"], l["sw"]
    decoded = hwl["event_count"] - hwf["event_count"]
    missed_hw = decoded - swl["drained"] - swl["unsync"]
    overflow_ever = 1 if any(s["hw"]["overflow"] for s in snaps) else 0
    lock_lost = 1 if any(not s["hw"]["lock"] for s in snaps) else 0

    if overflow_ever:
        xcheck = "overflow bit set: hardware confirms FIFO loss"
    elif missed_hw > FIFO_RESIDUAL:
        xcheck = "WARN: missed_hw=%d but overflow bit never set" % missed_hw
    elif missed_hw < -FIFO_RESIDUAL:
        xcheck = "WARN: missed_hw=%d negative beyond FIFO residual" % missed_hw
    else:
        xcheck = "clean (loss within FIFO residual, overflow bit clear)"

    return {
        "src": l["src"], "snapshots": len(snaps),
        "duration_s": l["mono"] - f["mono"],
        "decoded": decoded, "published": swl["published"],
        "failed_crc": hwl["error_count"] - hwf["error_count"],
        "nulls": hwl["null_count"] - hwf["null_count"],
        "filtered": hwl["filtered_count"] - hwf["filtered_count"],
        "missed_hw": missed_hw,
        "missed_pub": swl["queue_dropped"] + swl["redis_dropped"],
        "reconnects": swl["reconnects"], "unsync": swl["unsync"],
        "overflow_ever": overflow_ever, "lock_lost": lock_lost,
        "xcheck": xcheck,
    }


def format_report(r):
    """Human-readable per-source reconciliation block."""
    dur = r["duration_s"]
    rate = r["decoded"] / dur if dur > 0 else 0.0
    errpct = 100.0 * r["failed_crc"] / r["decoded"] if r["decoded"] else 0.0
    lines = [
        "=== %s ===" % r["src"],
        "  snapshots     : %d over %.0f s (%.2f h)" % (r["snapshots"], dur, dur / 3600.0),
        "  decoded (good): %d  (%.1f ev/s)" % (r["decoded"], rate),
        "  published     : %d" % r["published"],
        "  failed CRC    : %d  (%.3f%% of decoded)" % (r["failed_crc"], errpct),
        "  nulls/filtered: %d / %d" % (r["nulls"], r["filtered"]),
        "  missed @ HW   : %d  (FIFO overflow loss)" % r["missed_hw"],
        "  missed @ pub  : %d  (queue + redis drops)" % r["missed_pub"],
        "  reconnects    : %d" % r["reconnects"],
        "  unsync drops  : %d  (WR not locked when stamped)" % r["unsync"],
        "  WR lock lost  : %s" % ("YES" if r["lock_lost"] else "no"),
        "  overflow bit  : %s" % ("SET" if r["overflow_ever"] else "clear"),
        "  cross-check   : %s" % r["xcheck"],
    ]
    if r["snapshots"] < 2:
        lines.append("  NOTE: only one snapshot; run longer for a real delta.")
    return "\n".join(lines)


def main(argv):
    if not argv:
        print("usage: stats_report.py stats-tclk.jsonl [stats-aclk.jsonl ...]")
        return
    all_snaps = []
    for path in argv:
        all_snaps.extend(load_snapshots(path))
    if not all_snaps:
        print("no snapshots found in: " + " ".join(argv))
        return
    groups = group_by_src(all_snaps)
    for src in sorted(groups):
        print(format_report(reconcile(groups[src])))
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd deploy && python -m pytest test_stats_report.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Smoke-test the CLI end to end**

Run:
```bash
cd deploy && python - <<'PY'
import json, subprocess, tempfile, os
rows = [
 {"utc":"t0","mono":0.0,"src":"tclk","hw":{"event_count":0,"null_count":0,"error_count":0,"filtered_count":0,"overflow":0,"lock":1,"heartbeat":0},"sw":{"drained":0,"unsync":0,"published":0,"queued":0,"queue_dropped":0,"redis_dropped":0,"reconnects":0}},
 {"utc":"t1","mono":60.0,"src":"tclk","hw":{"event_count":1000,"null_count":0,"error_count":2,"filtered_count":0,"overflow":0,"lock":1,"heartbeat":9},"sw":{"drained":998,"unsync":0,"published":998,"queued":0,"queue_dropped":0,"redis_dropped":0,"reconnects":0}},
]
fd,p=tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
open(p,"w").write("\n".join(json.dumps(r) for r in rows)+"\n")
print(subprocess.run(["python","stats_report.py",p],capture_output=True,text=True).stdout)
os.remove(p)
PY
```
Expected: a `=== tclk ===` block showing `decoded (good): 1000`, `published: 998`, `failed CRC: 2`, `missed @ HW: 2`, `cross-check: clean ...`.

- [ ] **Step 6: Commit**

```bash
git add deploy/stats_report.py deploy/test_stats_report.py
git commit -m "feat(stats): stats_report reconciliation with overflow cross-check"
```

---

## Task 5: `run_pipeline.sh` (tmux launcher with pre-flight)

**Files:**
- Create: `deploy/run_pipeline.sh`

**Interfaces:**
- Consumes: `wr_time.py` (WR status), `redis_publish.py` (Task 3), `redis-cli`, `tmux`.
- Produces: a detached tmux session `kr260` with windows `tclk` and `aclk`, each running a publisher with `--statlog`.

- [ ] **Step 1: Write the script**

Create `deploy/run_pipeline.sh`:

```bash
#!/usr/bin/env bash
# Launch the KR260 TCLK+ACLK Redis publishers in a detached tmux session, each writing a
# JSONL stats log for the later error-check. Pre-flight refuses to launch unless Redis is
# reachable AND the WR timebase is fully locked, because an unlocked timebase stamps every
# event UNSYNC and the publisher would drop them all (a wasted day-long run).
#
# Run as root (the publishers mmap /dev/uio*):
#     sudo ./run_pipeline.sh [TCLK_UIO] [ACLK_UIO] [WR_UIO]
# Defaults: /dev/uio4 (tclk)  /dev/uio5 (aclk)  /dev/uio6 (wr).
# Match indices with:  grep . /sys/class/uio/uio*/name
# Override the WR-lock refusal with FORCE=1 (e.g. deliberately capturing UNSYNC).
set -euo pipefail

TCLK_DEV="${1:-/dev/uio4}"
ACLK_DEV="${2:-/dev/uio5}"
WR_DEV="${3:-/dev/uio6}"
SESSION="kr260"
FORCE="${FORCE:-0}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# --- pre-flight: Redis ---
if ! redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "!! redis-cli ping did not return PONG. Is redis-server running?" >&2
    exit 1
fi

# --- pre-flight: WR timebase locked ---
WRSTATUS="$(python3 "$HERE/wr_time.py" "$WR_DEV" status || true)"
echo "$WRSTATUS"
if ! echo "$WRSTATUS" | grep -q "locked_tclk=1" || ! echo "$WRSTATUS" | grep -q "locked_aclk=1"; then
    echo "!! WR timebase is not fully locked (need locked_tclk=1 and locked_aclk=1)." >&2
    echo "   Arm it first:  sudo python3 wr_time.py $WR_DEV arm   (see wr.md)" >&2
    if [ "$FORCE" != "1" ]; then
        echo "   Refusing to launch (every event would be UNSYNC-dropped)." >&2
        echo "   Re-run with FORCE=1 to override." >&2
        exit 1
    fi
    echo "   FORCE=1 set: launching anyway." >&2
fi

# --- already running? ---
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "!! tmux session '$SESSION' already exists. Attach: sudo tmux attach -t $SESSION" >&2
    exit 1
fi

# --- launch (exec bash keeps each window open after Ctrl-C so final stats stay visible) ---
tmux new-session -d -s "$SESSION" -n tclk \
    "cd '$HERE' && python3 redis_publish.py $TCLK_DEV --src tclk --statlog stats-tclk.jsonl; exec bash"
tmux new-window -t "$SESSION" -n aclk \
    "cd '$HERE' && python3 redis_publish.py $ACLK_DEV --src aclk --statlog stats-aclk.jsonl; exec bash"

echo "# launched tmux session '$SESSION' (windows: tclk, aclk)."
echo "#   attach : sudo tmux attach -t $SESSION      (detach with Ctrl-b d)"
echo "#   stop   : sudo tmux send-keys -t $SESSION:tclk C-c ; sudo tmux send-keys -t $SESSION:aclk C-c"
echo "#            (Ctrl-C makes each publisher write its FINAL snapshot), then:"
echo "#            sudo tmux kill-session -t $SESSION"
echo "#   report : sudo python3 stats_report.py stats-tclk.jsonl stats-aclk.jsonl"
```

- [ ] **Step 2: Syntax-check the script**

Run: `bash -n deploy/run_pipeline.sh && chmod +x deploy/run_pipeline.sh && echo "syntax ok"`
Expected: prints `syntax ok` (no parse errors). Note: full behavior is only testable on the board (needs tmux, redis-cli, and `/dev/uio*`); this step verifies the bash parses.

- [ ] **Step 3: Commit**

```bash
git add deploy/run_pipeline.sh
git commit -m "feat(deploy): tmux launcher with Redis + WR-lock pre-flight"
```

---

## Task 6: `plot_stats.py` (PC-side time-series plots)

**Files:**
- Create: `deploy/plot_stats.py`

**Interfaces:**
- Consumes: JSONL snapshot records (Task 2 schema); matplotlib (PC only).
- Produces: one PNG per source (`plot-<src>.png`) with event rate, CRC-error rate, cumulative missed/drops, and WR-lock/overflow status over the run.

- [ ] **Step 1: Write the script**

Create `deploy/plot_stats.py`:

```python
#!/usr/bin/env python3
"""Plot a KR260 capture stats log (JSONL) as time-series PNGs. Runs on the PC (matplotlib
lives here, not on the board). Copy the logs over first, e.g.:
    scp ubuntu@kr260:~/aclk_pipeline/stats-*.jsonl .
    python plot_stats.py stats-tclk.jsonl stats-aclk.jsonl

Per source it derives per-interval rates from consecutive-snapshot deltas divided by the
monotonic-time delta, and saves plot-<src>.png with four stacked panels: event rate,
CRC-error rate, cumulative missed (HW overflow + publisher drops), and WR-lock/overflow."""
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load_snapshots(path):
    snaps = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                snaps.append(json.loads(line))
    return snaps


def group_by_src(snaps):
    groups = {}
    for s in snaps:
        groups.setdefault(s["src"], []).append(s)
    return groups


def series(snaps):
    """Build plot arrays from time-ordered snapshots of one source. Rates are per-interval
    deltas / dt; the first point (no predecessor) is dropped for rate panels."""
    t0 = snaps[0]["mono"]
    hrs, ev_rate, err_rate, missed, lock, ovf = [], [], [], [], [], []
    for i in range(1, len(snaps)):
        a, b = snaps[i - 1], snaps[i]
        dt = b["mono"] - a["mono"]
        if dt <= 0:
            continue
        hrs.append((b["mono"] - t0) / 3600.0)
        ev_rate.append((b["hw"]["event_count"] - a["hw"]["event_count"]) / dt)
        err_rate.append((b["hw"]["error_count"] - a["hw"]["error_count"]) / dt)
        decoded = b["hw"]["event_count"] - snaps[0]["hw"]["event_count"]
        missed.append(decoded - b["sw"]["drained"] - b["sw"]["unsync"]
                      + b["sw"]["queue_dropped"] + b["sw"]["redis_dropped"])
        lock.append(b["hw"]["lock"])
        ovf.append(b["hw"]["overflow"])
    return hrs, ev_rate, err_rate, missed, lock, ovf


def plot_src(src, snaps):
    hrs, ev_rate, err_rate, missed, lock, ovf = series(snaps)
    if not hrs:
        print("skip %s: need at least two snapshots" % src)
        return
    fig, ax = plt.subplots(4, 1, figsize=(10, 11), sharex=True)
    fig.suptitle("KR260 capture: %s" % src)
    ax[0].plot(hrs, ev_rate, color="tab:blue")
    ax[0].set_ylabel("events/s")
    ax[0].grid(True, alpha=0.3)
    ax[1].plot(hrs, err_rate, color="tab:red")
    ax[1].set_ylabel("CRC errors/s")
    ax[1].grid(True, alpha=0.3)
    ax[2].plot(hrs, missed, color="tab:orange")
    ax[2].set_ylabel("cumulative missed")
    ax[2].grid(True, alpha=0.3)
    ax[3].plot(hrs, lock, label="WR lock", color="tab:green")
    ax[3].plot(hrs, ovf, label="overflow", color="tab:red", linestyle="--")
    ax[3].set_ylabel("status")
    ax[3].set_ylim(-0.1, 1.1)
    ax[3].set_xlabel("hours since start")
    ax[3].legend(loc="center right")
    ax[3].grid(True, alpha=0.3)
    out = "plot-%s.png" % src
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print("wrote " + out)


def main(argv):
    if not argv:
        print("usage: plot_stats.py stats-tclk.jsonl [stats-aclk.jsonl ...]")
        return
    all_snaps = []
    for path in argv:
        all_snaps.extend(load_snapshots(path))
    for src, snaps in sorted(group_by_src(all_snaps).items()):
        plot_src(src, snaps)


if __name__ == "__main__":
    main(sys.argv[1:])
```

- [ ] **Step 2: Compile-check (no matplotlib import needed to verify syntax)**

Run: `python -m py_compile deploy/plot_stats.py && echo "compile ok"`
Expected: prints `compile ok`. (If matplotlib is installed on the PC, optionally run it against the Task 4 smoke-log to confirm a PNG is produced; not required for the plan.)

- [ ] **Step 3: Commit**

```bash
git add deploy/plot_stats.py
git commit -m "feat(stats): PC-side matplotlib plotter for capture logs"
```

---

## Task 7: Runbook + deploy map

**Files:**
- Create: `deploy/capture.md`
- Modify: `deploy/redis.md` (add a pointer), `hw.ps1` (deploy map at line 240)

**Interfaces:** none (docs + packaging).

- [ ] **Step 1: Write the capture runbook**

Create `deploy/capture.md`:

```markdown
# Unattended TCLK/ACLK capture + error check (KR260)

Runs both publishers unattended in a tmux session, snapshots every counter to an on-disk
JSONL log, and reconciles the log into an events-published / missed / failed-CRC report.
Builds on the Redis publisher (see redis.md); the JSONL log is the durable record, so it
survives a redis restart or reboot (Redis persistence stays off).

## 1. Bring-up (once, per the pasted checklist)

    cd aclk_pipeline
    # load the bitstream + overlay (see redis.md / the project runbook), then:
    grep . /sys/class/uio/uio*/name          # note tclk_readout / aclk_readout / wr_timebase indices
    timedatectl                              # System clock synchronized: yes
    sudo python3 wr_time.py /dev/uio6 arm
    sudo python3 wr_time.py /dev/uio6 status # want locked_tclk=1 locked_aclk=1 locked_mon=1
    redis-cli ping                           # PONG

## 2. Launch the capture (survives SSH disconnect)

    sudo ./run_pipeline.sh                   # defaults: uio4 tclk, uio5 aclk, uio6 wr
    # or pass indices:  sudo ./run_pipeline.sh /dev/uio4 /dev/uio5 /dev/uio6

Pre-flight refuses to launch unless Redis answers PONG and the WR timebase is fully
locked (an unlocked timebase stamps every event UNSYNC and they would all be dropped).
Override with FORCE=1 only if you deliberately want to capture while unlocked.

Spot-check while it runs:

    sudo tmux attach -t kr260                # Ctrl-b d to detach
    redis-cli XLEN KR260:tclk                # climbs
    tail -f stats-tclk.jsonl                 # one JSON line per snapshot (~60 s)

## 3. Stop cleanly (writes a final post-flush snapshot)

    sudo tmux send-keys -t kr260:tclk C-c
    sudo tmux send-keys -t kr260:aclk C-c
    sudo tmux kill-session -t kr260

If a publisher dies uncleanly, the report still works off the last periodic snapshot; you
just lose up to the last interval (~60 s) of counts.

## 4. Error check (on the board)

    sudo python3 stats_report.py stats-tclk.jsonl stats-aclk.jsonl

Per source it prints decoded (good events the PL enqueued), published, failed CRCs,
nulls/filtered, missed at the hardware (FIFO overflow) and at the publisher (queue+redis
drops), reconnects, WR-lock health, and an overflow cross-check. `decoded`, `failed CRC`,
`nulls`, and `filtered` are baseline-to-last deltas; the software counters are cumulative
totals from the last snapshot.

## 5. Plots (on the PC)

    scp ubuntu@<board>:~/aclk_pipeline/stats-*.jsonl .
    python plot_stats.py stats-tclk.jsonl stats-aclk.jsonl   # -> plot-tclk.png, plot-aclk.png

## Options

`redis_publish.py` gains `--statlog <path>` (default `stats-<src>.jsonl`) and
`--snapshot-interval <sec>` (default 60). Everything else is unchanged from redis.md.

## How "missed" is measured

`EVENT_COUNT` (0x70) counts events presented to the FIFO, including ones later lost to a
full FIFO, so `missed @ HW = decodedDelta - drained - unsync` recovers overflow loss as a
number even though the hardware exposes overflow only as a sticky bit (STATUS bit1). The
report cross-checks the two: if it computes loss but the overflow bit was never set (or
vice versa) it prints a WARN. Tolerance is one FIFO depth (64) of residual at the final
snapshot; a clean Ctrl-C stop drains first, so the equality is tight.
```

- [ ] **Step 2: Add a pointer from `redis.md`**

In `deploy/redis.md`, immediately under the top title line (`# Redis event publishing (board-side, KR260 convention)`), add:

```markdown

> For leaving the board capturing unattended for hours/days and then reconciling
> events-published / missed / failed-CRC statistics, see **capture.md** (tmux launcher +
> on-disk stats log + `stats_report.py`).
```

- [ ] **Step 3: Add the new board files to the deploy map**

In `hw.ps1`, replace the `aclk_pipeline` line (line 240) with:

```powershell
            "aclk_pipeline"   = @("tclk_read.py", "aclkgt_read.py", "wr_time.py", "tclk_filter.py", "readout_common.py", "redis_sink.py", "redis_publish.py", "stats_log.py", "stats_report.py", "run_pipeline.sh", "requirements-board.txt", "redis-kr260.conf", "aclk_pipeline.dts", "capture.md")
```

(`plot_stats.py` is intentionally NOT deployed to the board; it runs on the PC.)

- [ ] **Step 4: Verify the PowerShell map still parses**

Run: `pwsh -NoProfile -Command "& { . { $ErrorActionPreference='Stop'; \$null = [ScriptBlock]::Create((Get-Content -Raw hw.ps1)); 'parse ok' } }"` (or `powershell` if `pwsh` is unavailable).
Expected: prints `parse ok` (the script tokenizes without a syntax error). If neither shell is available, visually confirm the line is a single valid hashtable entry with balanced quotes/parens.

- [ ] **Step 5: Commit**

```bash
git add deploy/capture.md deploy/redis.md hw.ps1
git commit -m "docs(deploy): capture runbook + ship stats tooling in the aclk_pipeline map"
```

---

## Final verification (all tasks)

- [ ] **Run the whole board-side test suite**

Run: `cd deploy && python -m pytest -q`
Expected: PASS for `test_readout_common.py`, `test_stats_log.py`, `test_redis_publish.py`, `test_stats_report.py`, `test_redis_sink.py`, `test_wr_time.py`, `test_tclk_filter.py`.

- [ ] **Confirm no board module needs redis or matplotlib to import**

Run: `cd deploy && python -c "import readout_common, stats_log, stats_report, redis_publish; print('ok')"`
Expected: prints `ok` (none of these pull in `redis` or `matplotlib` at import time).

---

## Self-Review

**Spec coverage:**
- Publisher self-samples hardware counters (spec 3, 5.1, 5.2) -> Tasks 1, 3.
- UNSYNC counting closes the accounting (spec 5.2, 6) -> Task 3 (`PublisherState`).
- Wall-clock tick fires when busy (spec 5.1) -> Task 1.
- JSONL schema + StatsLog (spec 5.2, 5.3) -> Task 2.
- Baseline + periodic + final snapshots (spec 5.2) -> Task 3.
- tmux launcher with WR/Redis pre-flight (spec 5.4) -> Task 5.
- Pure-reader reconciliation report + overflow cross-check (spec 5.5, 6) -> Task 4.
- PC-side plotter (spec 5.6) -> Task 6.
- Tests (spec 7) -> Tasks 1-4 tests + final suite.
- Deployment: runbook, redis.md pointer, hw.ps1 map (spec 8) -> Task 7.
- Out-of-scope items (spec 9: systemd, on-board plots, Redis persistence, log rotation, bring-up automation, RTL/schema changes) -> none added. Confirmed absent.

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code step shows complete code; every run step shows the exact command and expected output.

**Type consistency:** `read_hw_counters` returns the exact `hw` dict keys consumed by `build_snapshot`/`reconcile`. `sw_counters` returns the exact `sw` keys read by `reconcile`. `RedisSink.stats()` keys (`published, queued, queue_dropped, redis_dropped, reconnects`) match `sw_counters`'s reads. `reconcile` output keys match every key `format_report` reads. `PublisherState.note` returns bool used by `on_event`. Snapshot schema in Global Constraints matches Tasks 2/3/4 verbatim.
```