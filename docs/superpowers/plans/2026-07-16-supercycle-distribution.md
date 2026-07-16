# Supercycle Distribution (Stream Archiver + Folded-Raster Plot) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the published KR260 Redis event streams to daily CSVs on the board, and render a folded supercycle raster + distribution-shape figure for any chosen target event code on the PC.

**Architecture:** A standalone board-side archiver process (`stream_archive.py`) follows the Redis streams with batched XRANGE reads and appends CSV rows; it only talks to redis-server, never /dev/uio*, so publisher throughput is untouched. `run_pipeline.sh` gains a fourth tmux window running it. A PC-side plotter (`supercycle_plot.py`) anchors events on `$00`, segments and filters supercycles, and renders a marginal histogram over a cycle-by-cycle raster with reference combs drawn from real events.

**Tech Stack:** Python 3 stdlib (csv/json/argparse) + redis-py on the board; numpy + matplotlib (no pandas) on the PC. Tests follow the repo's `deploy/test_*.py` pattern (plain asserts, `__main__` runner, stub Redis client like `test_redis_sink.py`).

**Spec:** docs/superpowers/specs/2026-07-16-supercycle-distribution-design.md

## Global Constraints

- No em dashes anywhere (code, comments, docs): project style.
- Board scripts: Python 3 stdlib + redis-py only. PC plotter: numpy + matplotlib only (no pandas).
- CSV row schema exactly: `id,sec,ns,event,data` (values exactly as published; `event` is the decimal string from the stream).
- Archiver clean Ctrl-C exits 0; crash exits nonzero (launcher restart-loop contract).
- Tests live in `deploy/` beside the modules, runnable as `python test_x.py` printing `all ... tests passed`.
- Commit messages follow repo style: `type(scope): summary` lowercase.

---

### Task 1: stream_archive core (rows, daily writer, drain, state)

**Files:**
- Create: `deploy/stream_archive.py`
- Test: `deploy/test_stream_archive.py`

**Interfaces:**
- Produces (used by Task 2's CLI):
  - `row_from_entry(eid: str, fields: dict) -> list[str]`
  - `class DailyCsv(outdir: str, src: str, now=time.time)` with `.write_rows(rows: list[list[str]])`, `.close()`
  - `drain_source(client, stream: str, last_id: str|None, sink: callable, batch=10000) -> (str|None, int)` where sink takes `list[list[str]]`
  - `load_state(path: str) -> dict`, `save_state(path: str, state: dict)`
  - `HEADER = ["id", "sec", "ns", "event", "data"]`

- [ ] **Step 1: Write the failing tests**

Create `deploy/test_stream_archive.py`:

```python
"""Unit tests for stream_archive (no Redis server, no board).
Run: python test_stream_archive.py   or   pytest deploy -q"""
import csv
import json
import os
import tempfile

from stream_archive import (
    HEADER, row_from_entry, DailyCsv, drain_source, load_state, save_state,
)


class FakeStreamRedis:
    """Stub of the one redis-py call the archiver uses: xrange with an
    optional exclusive '(' min bound and a count limit."""
    def __init__(self, entries):
        self.entries = entries          # list of (id, fields), ascending

    @staticmethod
    def _key(eid):
        ms, seq = eid.split("-")
        return (int(ms), int(seq))

    def xrange(self, stream, min="-", max="+", count=None):
        excl = isinstance(min, str) and min.startswith("(")
        lo = None if min == "-" else self._key(min[1:] if excl else min)
        out = []
        for eid, f in self.entries:
            k = self._key(eid)
            if lo is not None and (k < lo or (excl and k == lo)):
                continue
            out.append((eid, dict(f)))
            if count is not None and len(out) >= count:
                break
        return out


def _entries(n, ms0=1000):
    return [("%d-0" % (ms0 + i),
             {"sec": "1", "ns": str(i), "event": "7", "data": "0"})
            for i in range(n)]


def test_row_from_entry_schema_and_defaults():
    r = row_from_entry("123-0", {"sec": "9", "ns": "8", "event": "29", "data": "5"})
    assert r == ["123-0", "9", "8", "29", "5"]
    r = row_from_entry("124-0", {})            # missing fields never crash
    assert r == ["124-0", "0", "0", "", "0"]


def test_drain_source_batches_and_resumes():
    fake = FakeStreamRedis(_entries(25))
    got = []
    last, n = drain_source(fake, "KR260:tclk", None, got.extend, batch=10)
    assert n == 25 and last == "1024-0"
    assert [g[0] for g in got] == ["%d-0" % (1000 + i) for i in range(25)]
    # resume: nothing new after last
    got2 = []
    last2, n2 = drain_source(fake, "KR260:tclk", last, got2.extend, batch=10)
    assert n2 == 0 and last2 == last and got2 == []
    # resume picks up only newer entries
    fake.entries += _entries(3, ms0=2000)
    got3 = []
    last3, n3 = drain_source(fake, "KR260:tclk", last, got3.extend, batch=10)
    assert n3 == 3 and last3 == "2002-0"


def test_daily_csv_rotates_by_utc_date():
    clock = [1_755_000_000.0]                  # mutable fake wall clock
    with tempfile.TemporaryDirectory() as d:
        w = DailyCsv(d, "tclk", now=lambda: clock[0])
        w.write_rows([["1-0", "1", "2", "7", "0"]])
        clock[0] += 86400.0                    # next UTC day -> new file
        w.write_rows([["2-0", "1", "3", "7", "0"]])
        w.close()
        files = sorted(os.listdir(d))
        assert len(files) == 2 and all(f.startswith("events-tclk-") for f in files)
        with open(os.path.join(d, files[0]), newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0] == HEADER and rows[1][0] == "1-0"


def test_state_roundtrip_and_missing():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "archive-state.json")
        assert load_state(p) == {}             # missing file -> empty
        save_state(p, {"tclk": "5-0"})
        assert load_state(p) == {"tclk": "5-0"}
        assert json.load(open(p)) == {"tclk": "5-0"}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all stream_archive tests passed")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd deploy && python test_stream_archive.py`
Expected: `ModuleNotFoundError: No module named 'stream_archive'`

- [ ] **Step 3: Write the implementation**

Create `deploy/stream_archive.py`:

```python
#!/usr/bin/env python3
"""Archive the published KR260 Redis streams to daily CSV files (board side).

The Redis streams retain only ~1M entries (~2.8 h at 99 ev/s); this process
follows them with batched XRANGE reads and appends one row per event to
events-<src>-YYYYMMDD.csv, resuming from archive-state.json across restarts,
so multi-day runs stay analyzable (see supercycle_plot.py).

Deliberately a SEPARATE process from the publishers: it only talks to
redis-server, never /dev/uio*, so it cannot affect the hardware FIFO drain.
Worst-case Redis backpressure lands in the publishers' bounded sink queue.

    python3 stream_archive.py                              # follow tclk+aclk
    python3 stream_archive.py --once --src tclk -o tail.csv  # dump retention, exit

Row schema: id,sec,ns,event,data (values exactly as published).
Ctrl-C exits 0 (clean stop); a crash exits nonzero so the launcher's
until-loop restarts it."""
import argparse
import csv
import json
import os
import sys
import time

HEADER = ["id", "sec", "ns", "event", "data"]


def row_from_entry(eid, fields):
    """One published stream entry -> one CSV row (missing fields never crash)."""
    return [eid, fields.get("sec", "0"), fields.get("ns", "0"),
            fields.get("event", ""), fields.get("data", "0")]


class DailyCsv:
    """Append-only CSV sink with UTC-daily rotation; header on file creation."""

    def __init__(self, outdir, src, now=time.time):
        self.outdir = outdir
        self.src = src
        self.now = now
        self._f = None
        self._w = None
        self._day = None

    def _path(self, day):
        return os.path.join(self.outdir, "events-%s-%s.csv" % (self.src, day))

    def _roll(self):
        day = time.strftime("%Y%m%d", time.gmtime(self.now()))
        if day == self._day:
            return
        self.close()
        path = self._path(day)
        fresh = not os.path.exists(path)
        self._f = open(path, "a", newline="", buffering=1)
        self._w = csv.writer(self._f)
        if fresh:
            self._w.writerow(HEADER)
        self._day = day

    def write_rows(self, rows):
        self._roll()
        self._w.writerows(rows)
        self._f.flush()

    def close(self):
        if self._f is not None:
            try:
                self._f.close()
            except OSError:
                pass
        self._f = None
        self._w = None
        self._day = None


def drain_source(client, stream, last_id, sink, batch=10000):
    """XRANGE everything newer than last_id into sink(rows). Returns
    (new_last_id, n_rows). last_id None/'-' means the start of retention."""
    total = 0
    while True:
        lo = "-" if last_id in (None, "-") else "(" + last_id
        entries = client.xrange(stream, min=lo, max="+", count=batch)
        if not entries:
            return last_id, total
        sink([row_from_entry(e, f) for e, f in entries])
        last_id = entries[-1][0]
        total += len(entries)
        if len(entries) < batch:
            return last_id, total


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd deploy && python test_stream_archive.py`
Expected: `all stream_archive tests passed`

- [ ] **Step 5: Commit**

```bash
git add deploy/stream_archive.py deploy/test_stream_archive.py
git commit -m "feat(deploy): stream_archive core (rows, daily csv, drain, state)"
```

---

### Task 2: stream_archive CLI (follow loop + --once)

**Files:**
- Modify: `deploy/stream_archive.py` (append below `save_state`)
- Test: `deploy/test_stream_archive.py` (append tests)

**Interfaces:**
- Consumes: Task 1's `drain_source`, `DailyCsv`, `HEADER`, `load_state`, `save_state`.
- Produces: `main(argv, connect=None) -> int` (0 on clean exit; `connect` injectable factory returning a client with `.xrange`, like `redis_sink.RedisSink`'s pattern).

- [ ] **Step 1: Append the failing tests**

Append to `deploy/test_stream_archive.py` (above the `__main__` block):

```python
def test_once_dumps_full_retention_to_file():
    from stream_archive import main
    fake = FakeStreamRedis(_entries(7))
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "tail.csv")
        rc = main(["--once", "--src", "tclk", "-o", out], connect=lambda h, p: fake)
        assert rc == 0
        with open(out, newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0] == HEADER and len(rows) == 8       # header + 7 events
        assert rows[1][0] == "1000-0" and rows[-1][0] == "1006-0"


def test_once_requires_exactly_one_src():
    from stream_archive import main
    rc = main(["--once", "--src", "tclk", "aclk", "-o", "x.csv"],
              connect=lambda h, p: FakeStreamRedis([]))
    assert rc != 0


def test_follow_writes_and_persists_state_then_stops():
    from stream_archive import main
    fake = FakeStreamRedis(_entries(5))
    with tempfile.TemporaryDirectory() as d:
        rc = main(["--src", "tclk", "--outdir", d, "--poll", "0", "--max-loops", "2"],
                  connect=lambda h, p: fake)
        assert rc == 0
        state = json.load(open(os.path.join(d, "archive-state.json")))
        assert state == {"tclk": "1004-0"}
        files = [f for f in os.listdir(d) if f.startswith("events-tclk-")]
        assert len(files) == 1
        with open(os.path.join(d, files[0]), newline="") as f:
            assert len(list(csv.reader(f))) == 6          # header + 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd deploy && python test_stream_archive.py`
Expected: `ImportError: cannot import name 'main'` (or AttributeError at the first new test)

- [ ] **Step 3: Append the implementation**

Append to `deploy/stream_archive.py`:

```python
def _default_connect(host, port):
    import redis   # lazy: module imports without redis-py (PC unit tests)
    return redis.Redis(host=host, port=port, decode_responses=True,
                       socket_connect_timeout=2.0, socket_timeout=5.0)


def main(argv, connect=None):
    ap = argparse.ArgumentParser(description="Archive KR260 Redis streams to CSV.")
    ap.add_argument("--src", nargs="+", default=["tclk", "aclk"])
    ap.add_argument("--namespace", default="KR260")
    ap.add_argument("--redis-host", default="127.0.0.1")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--poll", type=float, default=5.0)
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--batch", type=int, default=10000)
    ap.add_argument("--once", action="store_true",
                    help="dump the full retained stream to -o FILE and exit")
    ap.add_argument("-o", "--out", default=None, help="output file for --once")
    ap.add_argument("--max-loops", type=int, default=0,
                    help="follow mode: stop after N polls (0 = forever; tests only)")
    args = ap.parse_args(argv)
    connect = connect or _default_connect

    if args.once:
        if len(args.src) != 1 or not args.out:
            print("--once requires exactly one --src and -o FILE", file=sys.stderr)
            return 2
        client = connect(args.redis_host, args.redis_port)
        stream = "%s:%s" % (args.namespace, args.src[0])
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            _, n = drain_source(client, stream, None, w.writerows, batch=args.batch)
        print("wrote %d events from %s to %s" % (n, stream, args.out))
        return 0

    state_path = os.path.join(args.outdir, "archive-state.json")
    state = load_state(state_path)
    writers = {s: DailyCsv(args.outdir, s) for s in args.src}
    client = None
    loops = 0
    print("# archiving %s under %s every %gs (state: %s). Ctrl-C to stop."
          % (",".join(args.src), args.outdir, args.poll, state_path), flush=True)
    try:
        while True:
            try:
                if client is None:
                    client = connect(args.redis_host, args.redis_port)
                for s in args.src:
                    stream = "%s:%s" % (args.namespace, s)
                    last, n = drain_source(client, stream, state.get(s),
                                           writers[s].write_rows, batch=args.batch)
                    if n:
                        state[s] = last
                        save_state(state_path, state)
            except Exception as e:   # Redis down/hiccup: log, back off, reconnect
                print("# archiver: redis error (%s); retrying" % e, flush=True)
                client = None
            loops += 1
            if args.max_loops and loops >= args.max_loops:
                return 0
            time.sleep(args.poll)
    except KeyboardInterrupt:
        return 0
    finally:
        for w in writers.values():
            w.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd deploy && python test_stream_archive.py`
Expected: `all stream_archive tests passed` (7 tests)

- [ ] **Step 5: Commit**

```bash
git add deploy/stream_archive.py deploy/test_stream_archive.py
git commit -m "feat(deploy): stream_archive CLI (follow loop, --once dump, state resume)"
```

---

### Task 3: launcher + deploy-map hooks

**Files:**
- Modify: `deploy/run_pipeline.sh` (the tmux launch block and the window-list echo)
- Modify: `hw.ps1` (the `aclk_pipeline` entry of `$pyMap`)

**Interfaces:**
- Consumes: Task 2's `stream_archive.py` CLI defaults (follow mode, outdir `.`).
- Produces: `ARCHIVE` env toggle (default on; `ARCHIVE=""` disables).

- [ ] **Step 1: Add the ARCHIVE toggle next to DROP in run_pipeline.sh**

Below the `DROP="${DROP-07}"` comment block, add:

```bash
ARCHIVE="${ARCHIVE-1}"  # 1 = also run stream_archive.py (daily CSVs of every published
                        # event, ~260 MB/day/source; needed for supercycle analysis of
                        # runs longer than the ~2.8 h Redis stream retention).
                        # Set ARCHIVE="" to disable.
```

- [ ] **Step 2: Add the archive tmux window after the wr window**

After the `tmux new-window ... -n wr` line, add:

```bash
if [ -n "$ARCHIVE" ]; then
    tmux new-window -t "$SESSION" -n archive \
        "cd '$HERE' && until nice -n 10 python3 stream_archive.py; do echo '# archiver exited nonzero; restarting in 5 s'; sleep 5; done; exec bash"
fi
```

And update the launch echo line to:

```bash
echo "# launched tmux session '$SESSION' (windows: tclk, aclk, wr guard${ARCHIVE:+, archive})."
```

- [ ] **Step 3: Ship the archiver with the aclk_pipeline deploy map**

In `hw.ps1`, `$pyMap` key `"aclk_pipeline"`: append `"stream_archive.py"` to the array.

- [ ] **Step 4: Verify syntax**

Run: `cd deploy && bash -n run_pipeline.sh && echo OK`
Expected: `OK`
Run: `powershell -Command "[void][System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw hw.ps1), [ref]$null); 'OK'"` from the repo root
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add deploy/run_pipeline.sh hw.ps1
git commit -m "feat(deploy): archive tmux window (ARCHIVE toggle) + ship stream_archive.py"
```

---

### Task 4: supercycle_plot analysis helpers

**Files:**
- Create: `deploy/supercycle_plot.py` (helpers only; figure/CLI come in Task 5)
- Test: `deploy/test_supercycle_plot.py`

**Interfaces:**
- Produces (used by Task 5):
  - `load_events(paths: list[str]) -> (t: np.ndarray[float64], ev: np.ndarray[int])` deduped by stream id, time-sorted
  - `cycles_from_anchors(anchor_t: np.ndarray, tol=0.01) -> (starts, ends, stats: dict)` with stats keys `median_len, n_cycles, n_kept, n_rejected`
  - `assign_offsets(t, starts, ends) -> (mask: bool[], row: int[], off: float[])` where row/off are full-length arrays valid where mask

- [ ] **Step 1: Write the failing tests**

Create `deploy/test_supercycle_plot.py`:

```python
"""Unit tests for supercycle_plot pure helpers (synthetic data, no matplotlib).
Run: python test_supercycle_plot.py   or   pytest deploy -q"""
import csv
import os
import tempfile

import numpy as np

from supercycle_plot import load_events, cycles_from_anchors, assign_offsets


def test_load_events_dedupes_and_sorts():
    with tempfile.TemporaryDirectory() as d:
        p1 = os.path.join(d, "a.csv")
        p2 = os.path.join(d, "b.csv")
        rows1 = [["id", "sec", "ns", "event", "data"],
                 ["2-0", "10", "500", "12", "0"],
                 ["1-0", "10", "100", "0", "0"]]
        rows2 = [["id", "sec", "ns", "event", "data"],
                 ["2-0", "10", "500", "12", "0"],          # duplicate id
                 ["3-0", "11", "0", "24", "0"]]
        for p, rows in ((p1, rows1), (p2, rows2)):
            with open(p, "w", newline="") as f:
                csv.writer(f).writerows(rows)
        t, ev = load_events([p1, p2])
        assert len(t) == 3                                  # dupe dropped
        assert list(ev) == [0, 12, 24]                      # time-sorted
        assert abs(t[0] - 10.0000001) < 1e-9


def _synthetic(n_cycles=10, length=60.0, missing_anchor=5):
    """Anchor every `length` s with anchor #missing_anchor removed (folds two
    cycles into one 2x-length window that must be REJECTED), a bimodal target
    (offset 10 s in even cycles, 20 s in odd), and a 1 Hz ref comb."""
    anchors = [i * length for i in range(n_cycles + 1)]
    del anchors[missing_anchor]
    t, ev = [], []
    for a in anchors:
        t.append(a); ev.append(0x00)
    for i in range(n_cycles):
        t.append(i * length + (10.0 if i % 2 == 0 else 20.0)); ev.append(0x1E)
        for k in range(int(length)):
            t.append(i * length + k + 0.5); ev.append(0x8F)
    o = np.argsort(t, kind="stable")
    return np.asarray(t)[o], np.asarray(ev)[o]


def test_cycles_reject_missed_anchor_window():
    t, ev = _synthetic()
    starts, ends, stats = cycles_from_anchors(t[ev == 0x00])
    assert stats["n_cycles"] == 9                # 10 anchors -> 9 windows
    assert stats["n_rejected"] == 1              # the folded 120 s window
    assert stats["n_kept"] == 8
    assert abs(stats["median_len"] - 60.0) < 1e-9
    assert np.allclose(ends - starts, 60.0)


def test_assign_offsets_masks_and_measures():
    t, ev = _synthetic()
    starts, ends, _ = cycles_from_anchors(t[ev == 0x00])
    mask, row, off = assign_offsets(t, starts, ends)
    tgt = mask & (ev == 0x1E)
    offs = np.sort(np.unique(np.round(off[tgt], 6)))
    assert list(offs) == [10.0, 20.0]            # the two modes survive
    assert row[tgt].min() >= 0 and row[tgt].max() < len(starts)
    # events inside the rejected (folded) window are masked out entirely
    in_rejected = (t >= 4 * 60.0) & (t < 6 * 60.0) & (ev == 0x1E)
    assert not mask[in_rejected].any()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all supercycle_plot tests passed")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd deploy && python test_supercycle_plot.py`
Expected: `ModuleNotFoundError: No module named 'supercycle_plot'`

- [ ] **Step 3: Write the implementation**

Create `deploy/supercycle_plot.py`:

```python
#!/usr/bin/env python3
"""Folded supercycle raster + distribution shape for one TCLK event code (PC side).

Reads the CSVs written by stream_archive.py, anchors every event to the
preceding $00 (supercycle reset), folds all supercycles onto one time axis,
and renders: a marginal histogram of the target code's offsets (the shape) on
top of a raster (one row per supercycle, reference-comb events as faint dots,
target events as colored dots). Cycles whose length deviates from the median
by more than --tol are rejected (a missed anchor would fold two cycles).

    python supercycle_plot.py events-tclk-*.csv --target 1E --ref 0C,BA
    python supercycle_plot.py tail.csv --target 1F --theme poster -o bes.png
"""
import argparse
import csv
import sys

import numpy as np


def load_events(paths):
    """CSV file(s) -> (t seconds float64, event int), deduped by stream id,
    stably time-sorted."""
    seen = set()
    t, ev = [], []
    for p in paths:
        with open(p, newline="") as f:
            for row in csv.DictReader(f):
                eid = row["id"]
                if eid in seen:
                    continue
                seen.add(eid)
                t.append(int(row["sec"]) + int(row["ns"]) * 1e-9)
                ev.append(int(row["event"]))
    t = np.asarray(t, dtype=np.float64)
    ev = np.asarray(ev, dtype=np.int64)
    order = np.argsort(t, kind="stable")
    return t[order], ev[order]


def cycles_from_anchors(anchor_t, tol=0.01):
    """Consecutive-anchor windows filtered to |len - median| <= tol*median.
    Returns (starts, ends, stats). Raises ValueError with a clear message when
    fewer than 2 anchors exist."""
    if len(anchor_t) < 2:
        raise ValueError("need at least 2 anchor events to form a cycle "
                         "(got %d)" % len(anchor_t))
    lens = np.diff(anchor_t)
    med = float(np.median(lens))
    keep = np.abs(lens - med) <= tol * med
    stats = {"median_len": med, "n_cycles": int(len(lens)),
             "n_kept": int(keep.sum()), "n_rejected": int((~keep).sum())}
    return anchor_t[:-1][keep], anchor_t[1:][keep], stats


def assign_offsets(t, starts, ends):
    """Per event: (mask in-a-kept-cycle, dense row index, offset seconds).
    row/off are full-length arrays, meaningful only where mask is True."""
    idx = np.searchsorted(starts, t, side="right") - 1
    idx_c = np.clip(idx, 0, len(starts) - 1)
    mask = (idx >= 0) & (t < ends[idx_c])
    off = t - starts[idx_c]
    return mask, idx_c, off
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd deploy && python test_supercycle_plot.py`
Expected: `all supercycle_plot tests passed`

- [ ] **Step 5: Commit**

```bash
git add deploy/supercycle_plot.py deploy/test_supercycle_plot.py
git commit -m "feat(analysis): supercycle segmentation + offset helpers"
```

---

### Task 5: supercycle_plot figure + CLI + report

**Files:**
- Modify: `deploy/supercycle_plot.py` (append figure + CLI)
- Test: `deploy/test_supercycle_plot.py` (append smoke tests)

**Interfaces:**
- Consumes: Task 4's `load_events`, `cycles_from_anchors`, `assign_offsets`.
- Produces: `make_figure(off_t, row_t, off_r, row_r, n_rows, median_len, target, refs, theme, bins) -> matplotlib Figure`; `main(argv) -> int`.

- [ ] **Step 1: Append the failing smoke tests**

Append to `deploy/test_supercycle_plot.py` (above `__main__`):

```python
def _write_csv(d, t, ev):
    p = os.path.join(d, "events-tclk-x.csv")
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "sec", "ns", "event", "data"])
        for i, (tt, e) in enumerate(zip(t, ev)):
            sec = int(tt)
            ns = int(round((tt - sec) * 1e9))
            w.writerow(["%d-%d" % (int(tt * 1000), i), str(sec), str(ns),
                        str(int(e)), "0"])
    return p


def test_make_figure_two_axes():
    import matplotlib
    matplotlib.use("Agg")
    from supercycle_plot import make_figure
    rng = np.random.default_rng(1)
    off_t = rng.normal(10.0, 0.2, 200)
    row_t = rng.integers(0, 8, 200)
    off_r = np.tile(np.arange(60) + 0.5, 8)
    row_r = np.repeat(np.arange(8), 60)
    fig = make_figure(off_t, row_t, off_r, row_r, n_rows=8, median_len=60.0,
                      target=0x1E, refs=[0x8F], theme="default", bins=120)
    assert len(fig.axes) == 2


def test_main_reports_missing_target_with_available_codes(capsys=None):
    from supercycle_plot import main
    t, ev = _synthetic()
    with tempfile.TemporaryDirectory() as d:
        p = _write_csv(d, t, ev)
        rc = main([p, "--target", "AB", "-o", os.path.join(d, "x.png")])
        assert rc != 0                                     # 0xAB never occurs


def test_main_renders_png():
    from supercycle_plot import main
    t, ev = _synthetic()
    with tempfile.TemporaryDirectory() as d:
        p = _write_csv(d, t, ev)
        out = os.path.join(d, "sc.png")
        rc = main([p, "--target", "1E", "--ref", "8F", "-o", out])
        assert rc == 0 and os.path.exists(out) and os.path.getsize(out) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd deploy && python test_supercycle_plot.py`
Expected: `ImportError: cannot import name 'make_figure'`

- [ ] **Step 3: Append the implementation**

Append to `deploy/supercycle_plot.py`:

```python
# blue-and-white theme (matches the other poster figures)
INK, MUTED, FAINT, SURF = "#1b1b1b", "#6f6f6f", "#dfe6ee", "#ffffff"
C_TARGET, C_REF = "#1b5a8f", "#9aa7b4"


def _hex(code):
    return "0x%02X" % code if code <= 0xFF else "0x%04X" % code


def make_figure(off_t, row_t, off_r, row_r, n_rows, median_len,
                target, refs, theme="default", bins=600):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    poster = (theme == "poster")
    plt.rcParams.update({"font.family": "DejaVu Sans",
                         "font.size": 14 if poster else 11,
                         "svg.fonttype": "none"})
    fig = plt.figure(figsize=(12.5, 7.5) if poster else (11, 6.5),
                     dpi=300, facecolor=SURF)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.0, 2.9], hspace=0.07,
                          left=0.085, right=0.975, top=0.86, bottom=0.10)
    ax_h = fig.add_subplot(gs[0])
    ax_r = fig.add_subplot(gs[1], sharex=ax_h)

    edges = np.linspace(0.0, median_len, bins + 1)
    if len(off_r):
        ax_h.hist(off_r, bins=edges, color=C_REF, alpha=0.45, zorder=2,
                  label="ref " + ", ".join(_hex(r) for r in refs))
    ax_h.hist(off_t, bins=edges, color=C_TARGET, zorder=3,
              label="target " + _hex(target))
    ax_h.legend(loc="upper right", fontsize=10, frameon=False)
    ax_h.set_ylabel("events / bin", fontsize=10, color=MUTED)
    ax_h.tick_params(labelbottom=False, length=0)
    for sp in ("top", "right"):
        ax_h.spines[sp].set_visible(False)

    if len(off_r):
        ax_r.scatter(off_r, row_r, s=2, color=C_REF, alpha=0.25,
                     linewidths=0, zorder=2)
    ax_r.scatter(off_t, row_t, s=14, color=C_TARGET, linewidths=0, zorder=3)
    ax_r.set_xlim(0.0, median_len)
    ax_r.set_ylim(-0.5, n_rows - 0.5)
    ax_r.invert_yaxis()                       # first cycle at the top
    ax_r.set_xlabel("offset into supercycle (s)", fontsize=11, color=MUTED)
    ax_r.set_ylabel("supercycle (time order)", fontsize=11, color=MUTED)
    for sp in ("top", "right"):
        ax_r.spines[sp].set_visible(False)

    fig.suptitle("Event %s within the TCLK supercycle" % _hex(target),
                 x=0.085, y=0.965, ha="left", fontsize=19, fontweight="bold",
                 color=INK)
    fig.text(0.085, 0.895,
             "%d supercycles folded on $00, median length %.3f s"
             % (n_rows, median_len), ha="left", fontsize=12, color=MUTED)
    return fig


def _parse_codes(s):
    return [int(c, 16) for c in s.split(",") if c.strip()]


def main(argv):
    ap = argparse.ArgumentParser(description="Supercycle folded raster + shape.")
    ap.add_argument("csvs", nargs="+")
    ap.add_argument("--target", required=True, help="event code, hex (e.g. 1E)")
    ap.add_argument("--ref", default="0C,BA", help="reference codes, hex CSV")
    ap.add_argument("--anchor", default="00", help="cycle anchor code, hex")
    ap.add_argument("--tol", type=float, default=0.01)
    ap.add_argument("--bins", type=int, default=600)
    ap.add_argument("--theme", choices=("default", "poster"), default="default")
    ap.add_argument("--topn-report", type=int, default=5)
    ap.add_argument("-o", "--out", default="supercycle.png")
    args = ap.parse_args(argv)

    target = int(args.target, 16)
    refs = _parse_codes(args.ref)
    anchor = int(args.anchor, 16)

    t, ev = load_events(args.csvs)
    for code, what in [(anchor, "anchor"), (target, "target")]:
        if not (ev == code).any():
            uniq, cnt = np.unique(ev, return_counts=True)
            avail = "  ".join("%s:%d" % (_hex(int(u)), int(c))
                              for u, c in zip(uniq, cnt))
            print("no %s events %s in the data. Available codes:\n%s"
                  % (what, _hex(code), avail), file=sys.stderr)
            return 2

    try:
        starts, ends, stats = cycles_from_anchors(t[ev == anchor], tol=args.tol)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if stats["n_kept"] < 5:
        print("only %d usable cycles (need >= 5); capture longer or check the "
              "anchor code" % stats["n_kept"], file=sys.stderr)
        return 2

    mask, row, off = assign_offsets(t, starts, ends)
    is_t = mask & (ev == target)
    is_r = mask & np.isin(ev, refs)

    fig = make_figure(off[is_t], row[is_t], off[is_r], row[is_r],
                      n_rows=stats["n_kept"], median_len=stats["median_len"],
                      target=target, refs=refs, theme=args.theme,
                      bins=args.bins)
    fig.savefig(args.out, dpi=300, facecolor=SURF, bbox_inches="tight")
    svg = args.out.rsplit(".", 1)[0] + ".svg"
    fig.savefig(svg, facecolor=SURF, bbox_inches="tight")

    lens = ends - starts
    per_cycle = np.bincount(row[is_t], minlength=stats["n_kept"])
    hist, edges = np.histogram(off[is_t],
                               bins=np.linspace(0, stats["median_len"], args.bins + 1))
    top = np.argsort(hist)[::-1][:args.topn_report]
    top = [i for i in top if hist[i] > 0]
    print("cycles: %d kept / %d rejected (median %.6f s, sigma %.6f s)"
          % (stats["n_kept"], stats["n_rejected"], stats["median_len"],
             float(np.std(lens))))
    print("target %s: %d events; per cycle min/median/max = %d/%d/%d"
          % (_hex(target), int(is_t.sum()), per_cycle.min(),
             int(np.median(per_cycle)), per_cycle.max()))
    for i in sorted(top, key=lambda i: edges[i]):
        print("  mode near %8.3f s: %d events" % (edges[i], int(hist[i])))
    print("wrote %s and %s" % (args.out, svg))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd deploy && python test_supercycle_plot.py`
Expected: `all supercycle_plot tests passed` (6 tests)

- [ ] **Step 5: Run the full deploy suite (no regressions)**

Run: `cd deploy && python test_readout_common.py && python test_redis_sink.py && python test_redis_publish.py && python test_stats_report.py && python test_stream_archive.py && python test_supercycle_plot.py`
Expected: every file ends with its `all ... tests passed` line

- [ ] **Step 6: Commit**

```bash
git add deploy/supercycle_plot.py deploy/test_supercycle_plot.py
git commit -m "feat(analysis): supercycle folded-raster figure + CLI + mode report"
```

---

### Task 6: board validation + quick look (operator-assisted)

**Files:** none created on the PC (board run + scp back). Operator at the terminal required.

**Interfaces:**
- Consumes: Task 2's `--once` mode, Task 5's CLI.

- [ ] **Step 1: Copy the archiver to the board**

```powershell
scp deploy\stream_archive.py deploy\run_pipeline.sh ubuntu@aclk-timestamper:~/aclk_pipeline/
```

- [ ] **Step 2: Quick-look dump of the retained overnight tail (board)**

```bash
cd ~/aclk_pipeline
sed -i 's/\r$//' run_pipeline.sh
python3 stream_archive.py --once --src tclk -o overnight-tail-tclk.csv
# expect: "wrote ~1000000 events from KR260:tclk to overnight-tail-tclk.csv"
```

- [ ] **Step 3: Copy the dump back and plot (PC)**

```powershell
scp ubuntu@aclk-timestamper:~/aclk_pipeline/overnight-tail-tclk.csv deploy\
cd deploy
..\.venv\Scripts\python supercycle_plot.py overnight-tail-tclk.csv --target 1E --ref 0C,BA -o supercycle_1E.png
```
Expected: stdout reports kept/rejected cycles, median cycle length, mode positions; PNG+SVG written. Iterate targets freely (`--target 1F`, `--target 10`, ...).

- [ ] **Step 4: Throughput validation with the archiver live (board, ~10 min)**

```bash
rm -f stats-tclk.jsonl stats-aclk.jsonl
sudo bash run_pipeline.sh          # now includes the archive window
# ... 10 minutes ...
sudo tmux send-keys -t kr260:tclk C-c; sudo tmux send-keys -t kr260:aclk C-c
sleep 3; sudo tmux kill-session -t kr260
sudo python3 stats_report.py stats-tclk.jsonl stats-aclk.jsonl
wc -l events-tclk-*.csv
```
Expected: `missed @ pub = 0`, `missed @ HW` ~0 (mod the -512 backlog artifact), ledger OK, and the CSV row count grows at the publish rate. This is the spec's "throughput unaffected" gate.

- [ ] **Step 5: Commit nothing; report results in chat**

Paste the stats_report block and the plot stdout; the figure gets iterated interactively from here.
