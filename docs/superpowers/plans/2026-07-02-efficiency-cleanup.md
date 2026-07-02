# Efficiency Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove verified-dead code and consolidate the duplicated deploy-reader and testbench boilerplate into shared modules, with zero functionality loss (checklist: `docs/FUNCTIONALITY.md`).

**Architecture:** Three shared modules absorb the duplication: `deploy/readout_common.py` (register map, watchdog-guarded mmap access, event drain, probe), `tb/runner_common.py` (cocotb runner factory), `tb/cocotb_helpers.py` + extended `tb/plot_util.py` (test helpers). Every consuming script is rewritten to a thin wrapper that keeps its exact CLI, output format, and diagnostics.

**Tech Stack:** Python 3.12 (venv at `.venv/`), cocotb 2.0 + Icarus (via `sim.ps1`), PowerShell drivers (`hw.ps1`, `sim.ps1`), git.

## Global Constraints

- NO functionality may be lost. `docs/FUNCTIONALITY.md` is the authoritative checklist; verify against it before/after each task.
- NO em dashes in any file or output (project style rule).
- Reader scripts must keep byte-identical output formats (headers, event lines, stats lines, probe text) except where a task explicitly says a diagnostic message is being unified to the more detailed variant.
- Deploy scripts run on the board (Linux); they cannot be executed on this Windows dev box. Off-board verification = unit tests on the pure logic + `python -m py_compile` + careful diff. On-board smoke test is a documented follow-up.
- Simulations run from repo root: `.\sim.ps1 run -Module <name>` (venv must exist; if missing run `.\sim.ps1 setup` once).
- Unit tests for deploy: `cd deploy` then `..\..venv\Scripts\python.exe -m pytest -q` is WRONG PATH; use repo-root relative: `.venv\Scripts\python.exe -m pytest deploy -q` from repo root (pytest adds each test file's dir to sys.path, so `import readout_common` resolves).
- Commit after every task with the message given in the task. Do not amend, do not push.
- All work on branch `efficiency-cleanup` (created in Task 1).
- rtl/, constraints/, vivado/*.tcl are NOT touched by this plan (Tier 4 RTL dedup is deferred).

---

### Task 1: Branch + delete untracked dead weight

`rtl/Li_Files/` is a byte-identical, git-ignored, untracked copy of `rtl/aclk_bridge/` (verified: `git ls-files rtl/Li_Files` returns nothing, `diff -rq` returns nothing). Root Vivado session logs and stale generated IP dirs are likewise untracked junk. Deleting untracked files needs no commit; the branch is created here for everything that follows.

**Files:**
- Delete (untracked): `rtl/Li_Files/`, root `vivado.jou`, `vivado.log`, `vivado_*.backup.jou`, `vivado_*.backup.log`, `vivado/ip/aclkgt_gt_1/`, `vivado/ip/aclkgt_gt_2/`, `vivado/ip/aclkgt_gt_3/`

- [ ] **Step 1: Create the branch**

```powershell
git checkout -b efficiency-cleanup
```
Expected: `Switched to a new branch 'efficiency-cleanup'`

- [ ] **Step 2: Prove each target is untracked BEFORE deleting**

```powershell
git ls-files rtl/Li_Files vivado/ip/aclkgt_gt_1 vivado/ip/aclkgt_gt_2 vivado/ip/aclkgt_gt_3
git ls-files vivado.jou vivado.log
git ls-files --error-unmatch vivado_19792.backup.jou 2>$null
```
Expected: every command prints NOTHING (zero tracked files). If ANY path prints, STOP and exclude it from deletion.

- [ ] **Step 3: Delete**

```powershell
Remove-Item -Recurse -Force rtl/Li_Files
Get-ChildItem -File vivado*.jou, vivado*.log | Remove-Item -Force
foreach ($d in "vivado/ip/aclkgt_gt_1","vivado/ip/aclkgt_gt_2","vivado/ip/aclkgt_gt_3") {
    if (Test-Path $d) { Remove-Item -Recurse -Force $d }
}
```

- [ ] **Step 4: Verify the working tree is still clean**

```powershell
git status --porcelain
```
Expected: empty output (nothing tracked changed). No commit for this task.

---

### Task 2: Remove the 8 unused aclk_bridge legacy modules

Verified unreferenced in every build TCL, testbench, and runner (only hits are a comment in `tb/aclk_rcv/test_aclk_rcv.py` and a TCL variable coincidentally named `top_module` in `vivado/build.tcl`).

**Files:**
- Delete (tracked): `rtl/aclk_bridge/BitEncoder.v`, `rtl/aclk_bridge/FrameEncoder.v`, `rtl/aclk_bridge/fake_data.v`, `rtl/aclk_bridge/lfsr80.v`, `rtl/aclk_bridge/TimelineGenerator.v`, `rtl/aclk_bridge/aclk_data_source.v`, `rtl/aclk_bridge/top_module.v`, `rtl/aclk_bridge/ack_stimulus_gen.v`

**Interfaces:**
- Produces: `rtl/aclk_bridge/` containing ONLY the 7 live files: `ACLK_REV.v`, `crc8_calc.v`, `GEARBOX_16_TO_96.v`, `gearbox_96_to_16.v`, `serdec4_9MHz.v`, `TCLK_DESERIALIZER2.v`, `TCLK_RCV.v`

- [ ] **Step 1: Re-verify zero live references (the gate)**

```powershell
Select-String -Path vivado\*.tcl, tb\*\runner.py -Pattern "BitEncoder|FrameEncoder|fake_data|lfsr80|TimelineGenerator|aclk_data_source|ack_stimulus_gen|top_module\.v"
```
Expected: NO output. If anything matches, STOP: that file is live, remove it from the deletion list.

- [ ] **Step 2: Delete via git**

```powershell
git rm rtl/aclk_bridge/BitEncoder.v rtl/aclk_bridge/FrameEncoder.v rtl/aclk_bridge/fake_data.v rtl/aclk_bridge/lfsr80.v rtl/aclk_bridge/TimelineGenerator.v rtl/aclk_bridge/aclk_data_source.v rtl/aclk_bridge/top_module.v rtl/aclk_bridge/ack_stimulus_gen.v
```

- [ ] **Step 3: Prove the suites that compile aclk_bridge sources still pass**

```powershell
.\sim.ps1 run -Module aclk_rcv
.\sim.ps1 run -Module tclk_rcv
.\sim.ps1 run -Module clk_rcv
```
Expected: each ends with cocotb PASS (exit 0).

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(rtl): remove 8 unused aclk_bridge legacy modules

Verified unreferenced by any build tcl, testbench, or runner (only a comment
and an unrelated tcl variable name matched). Inventory: docs/FUNCTIONALITY.md.
aclk_data_source.v note: aclk_tclk_encoder.v is its simmable refactor."
```

---

### Task 3: Create deploy/readout_common.py (TDD)

One copy of what the four readers duplicated: register map, watchdog, `rd`/`wr`, `read_event`, drop-filter application, startup probe, drain loop, CLI parser. `RegIO` takes any mutable buffer, so unit tests run on a `bytearray` with no hardware. The watchdog message is unified to the 3-line variant (superset of clk_read's 2-line one; documented diagnostic improvement, not a loss).

**Files:**
- Create: `deploy/readout_common.py`
- Test: `deploy/test_readout_common.py`

**Interfaces:**
- Produces (used by Tasks 4-7):
  - constants `STATUS EVENT DATA_HI DATA_LO TS_HI TS_LO POP EVENT_COUNT NULL_COUNT ERROR_COUNT DEBUG HEARTBEAT LOCK FILTER_CFG FILTERED_COUNT GT_CTRL`, dict `NAME`
  - `say(msg)`, `line_buffer_stdout()`
  - `parse_args(argv, value_flags=(), bool_flags=()) -> (positionals, flags_dict)` (unknown tokens go to positionals, matching old reader behavior)
  - `dev_offset(dev) -> int` (0 for uio, else 0x8000_0000)
  - `class RegIO(buf, names=NAME)` with `.rd(o) -> int`, `.wr(o, v=0)`, `.start_watchdog()`
  - `open_dev(dev, announce=True, watchdog=True) -> RegIO` (board-side mmap)
  - `read_event(io) -> (event, flags, data, ts)` (reads EVENT/DATA/TS, writes POP)
  - `apply_drop_filter(io, drop_codes)` (writes FILTER_CFG words, prints suppression line)
  - `probe(io, counters, lock_desc, red_flag, trust_ok, stuck_warn)` (announced register reads + heartbeat trust check; the three verdict strings passed in per reader)
  - `stream_events(io, tick_ns, stats_line, format_event, header)` (the drain loop; `format_event(ts, dt, event, data, is_tclk, has_data) -> str`)
  - re-exports `parse_drop_codes`, `filter_cfg_word` from `tclk_filter`

- [ ] **Step 1: Write the failing tests**

Create `deploy/test_readout_common.py`:

```python
"""Unit tests for readout_common (no hardware: RegIO runs on a bytearray).
Run: python test_readout_common.py   or   pytest deploy -q"""
from readout_common import (
    RegIO, read_event, parse_args, dev_offset, apply_drop_filter,
    STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, FILTER_CFG, NAME, GT_CTRL,
)


def make_io():
    return RegIO(bytearray(0x1000))


def test_register_map_16_byte_stride():
    assert (STATUS, EVENT, DATA_HI, DATA_LO) == (0x00, 0x10, 0x20, 0x30)
    assert (TS_HI, TS_LO, POP) == (0x40, 0x50, 0x60)
    assert FILTER_CFG == 0xD0 and GT_CTRL == 0xF0
    assert NAME[EVENT] == "EVENT" and NAME[GT_CTRL] == "GT_CTRL"


def test_rd_wr_roundtrip_little_endian():
    io = make_io()
    io.wr(STATUS, 0xDEADBEEF)
    assert io.rd(STATUS) == 0xDEADBEEF
    assert io.m[0:4] == bytes([0xEF, 0xBE, 0xAD, 0xDE])
    io.wr(EVENT)                      # default value 0
    assert io.rd(EVENT) == 0


def test_read_event_unpacks_fields_and_pops():
    io = make_io()
    io.wr(EVENT, (0x0003 << 16) | 0xABCD)   # flags=3 (is_tclk|has_data), event=0xABCD
    io.wr(DATA_HI, 0xDEADBEEF)
    io.wr(DATA_LO, 0xCAFE0001)
    io.wr(TS_HI, 0x00000001)
    io.wr(TS_LO, 0x00000002)
    io.wr(POP, 0x55555555)                  # pre-load; read_event must overwrite with 0
    event, flags, data, ts = read_event(io)
    assert event == 0xABCD and flags == 0x0003
    assert data == 0xDEADBEEFCAFE0001
    assert ts == 0x0000000100000002
    assert io.rd(POP) == 0


def test_parse_args_matches_old_reader_behavior():
    pos, fl = parse_args(
        ["/dev/uio4", "--drop", "07,0F", "--gtreset", "--unknown"],
        value_flags=("--drop", "--tick-ns"), bool_flags=("--gtreset",))
    assert pos == ["/dev/uio4", "--unknown"]     # unknowns fall through to positionals
    assert fl == {"--drop": "07,0F", "--gtreset": True}
    pos, fl = parse_args([], value_flags=("--drop",))
    assert pos == [] and fl == {}


def test_dev_offset():
    assert dev_offset("/dev/uio4") == 0
    assert dev_offset("/dev/mem") == 0x8000_0000


def test_apply_drop_filter_writes_cfg_word():
    io = make_io()
    apply_drop_filter(io, [0x07])
    assert io.rd(FILTER_CFG) == 0x107            # bit8=drop | code


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all readout_common tests passed")
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
.venv\Scripts\python.exe -m pytest deploy\test_readout_common.py -q
```
Expected: collection error `ModuleNotFoundError: No module named 'readout_common'`

- [ ] **Step 3: Write deploy/readout_common.py**

```python
"""Shared board-side helpers for the AXI-Lite readout register block.

One copy of everything the per-build readers (clk_read / tclk_read / aclk_read /
aclkgt_read) used to duplicate: the register map (16-byte stride), the mmap open,
the watchdog-guarded rd/wr accessors, event draining, the drop-filter application,
the startup probe, and the streaming loop. Pure logic stays off the mmap so it is
unit-testable on the PC (test_readout_common.py); only open_dev() touches
/dev/uio* and needs the board.
"""
import mmap
import os
import struct
import sys
import threading
import time

from tclk_filter import parse_drop_codes, filter_cfg_word  # noqa: F401 (re-exported)

# Registers are spaced 16 BYTES apart: the hand-written AXI4-Lite slave only returns
# correct data at 16-byte-aligned offsets on this board (non-aligned reads read 0).
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xA0
)
HEARTBEAT, LOCK = 0xB0, 0xC0   # free-running rx-domain counter (trust check); clock-alive bit
FILTER_CFG, FILTERED_COUNT = 0xD0, 0xE0   # drop-mask config (write); dropped-event count (read)
GT_CTRL = 0xF0                 # aclkgt builds only (rx/tx polarity, loopback, TX driver, resets)

NAME = {STATUS: "STATUS", EVENT: "EVENT", DATA_HI: "DATA_HI", DATA_LO: "DATA_LO",
        TS_HI: "TS_HI", TS_LO: "TS_LO", POP: "POP", EVENT_COUNT: "EVENT_COUNT",
        NULL_COUNT: "NULL_COUNT", ERROR_COUNT: "ERROR_COUNT", DEBUG: "DEBUG",
        HEARTBEAT: "HEARTBEAT", LOCK: "LOCK",
        FILTER_CFG: "FILTER_CFG", FILTERED_COUNT: "FILTERED_COUNT", GT_CTRL: "GT_CTRL"}


def say(msg):
    print(msg, flush=True)


def line_buffer_stdout():
    """Force line buffering so a freeze can never hide already-printed output.
    Guarded: not every stdout supports reconfigure."""
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass


def parse_args(argv, value_flags=(), bool_flags=()):
    """Tiny common CLI parser. value_flags consume the next token; bool_flags set
    True. Anything else (including unknown --flags) falls through to positionals,
    exactly like the hand-rolled loops the readers used before."""
    pos, flags = [], {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in value_flags and i + 1 < len(argv):
            flags[a] = argv[i + 1]
            i += 2
        elif a in bool_flags:
            flags[a] = True
            i += 1
        else:
            pos.append(a)
            i += 1
    return pos, flags


def dev_offset(dev):
    return 0 if "uio" in dev else 0x8000_0000


class RegIO:
    """Watchdog-guarded 32-bit register access over a buffer: an mmap on the board,
    or any mutable buffer (e.g. bytearray) in unit tests.

    Watchdog rationale: an AXI read that never returns RVALID wedges the CPU's load
    instruction uninterruptibly (Ctrl-C will NOT break it). The daemon thread watches
    every access and, if one stalls >2 s, prints WHICH offset is stuck, pinning the
    freeze to a PL/BD fault (reset held, overlay/bitstream not loaded, dead bus),
    NOT a Python bug."""

    def __init__(self, buf, names=NAME):
        self.m = buf
        self.names = names
        self._watch = {"label": None, "t": 0.0}

    def start_watchdog(self):
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _watchdog(self):
        warned = False
        while True:
            time.sleep(0.5)
            lbl = self._watch["label"]
            if lbl is None:
                warned = False
                continue
            if (time.monotonic() - self._watch["t"]) > 2.0 and not warned:
                say("# !! WATCHDOG: blocked >2s in %s" % lbl)
                say("# !! An AXI read is wedging the bus (reset held / overlay-bitstream not "
                    "loaded / wrong address). This is the PL/BD side, NOT this Python script.")
                say("# !! Ctrl-C cannot break a wedged AXI load; reload the bitstream+overlay "
                    "and re-check s_axi_aresetn / the dcm_locked tie-off.")
                warned = True

    def _enter(self, label):
        self._watch["t"] = time.monotonic()
        self._watch["label"] = label

    def _leave(self):
        self._watch["label"] = None

    def rd(self, o):
        self._enter("read %s (0x%02X)" % (self.names.get(o, "?"), o))
        v = struct.unpack("<I", self.m[o:o + 4])[0]
        self._leave()
        return v

    def wr(self, o, v=0):
        self._enter("write %s (0x%02X)" % (self.names.get(o, "?"), o))
        self.m[o:o + 4] = struct.pack("<I", v & 0xFFFFFFFF)
        self._leave()


def open_dev(dev, announce=True, watchdog=True):
    """mmap the readout register block (board-side only); returns a RegIO."""
    off = dev_offset(dev)
    if announce:
        say("# opening %s (offset 0x%x) ..." % (dev, off))
    fd = os.open(dev, os.O_RDWR | os.O_SYNC)
    m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE, offset=off)
    io = RegIO(m)
    if watchdog:
        if announce:
            say("# mmap ok (0x1000 bytes). starting watchdog ...")
        io.start_watchdog()
    return io


def read_event(io):
    """Read one buffered event (EVENT + DATA + TS) and pop it from the FIFO."""
    ev = io.rd(EVENT)
    event = ev & 0xFFFF
    flags = (ev >> 16) & 0xFFFF
    data = (io.rd(DATA_HI) << 32) | io.rd(DATA_LO)
    ts = (io.rd(TS_HI) << 32) | io.rd(TS_LO)
    io.wr(POP)
    return event, flags, data, ts


def apply_drop_filter(io, drop_codes):
    for c in drop_codes:
        io.wr(FILTER_CFG, filter_cfg_word(c))
    if drop_codes:
        say("# drop-mask: suppressing " + ", ".join("0x%02X" % c for c in drop_codes))


def probe(io, counters, lock_desc, red_flag, trust_ok, stuck_warn):
    """One-time startup read of each register in `counters`, announced BEFORE each
    access (a freeze names the wedged offset), then the heartbeat TRUST CHECK:
    the counters cross clock domains through cdc_gray_count, and the free-running
    heartbeat (0xB0) takes the SAME path, so heartbeat moving => readback works.
    The three verdict strings differ per build and are passed in by the reader."""
    say("# --- startup probe (a freeze here names the wedged offset) ---")
    for o in counters:
        say("#   reading %-12s 0x%02X ..." % (NAME[o], o))
        say("#     %-12s = 0x%08X" % (NAME[o], io.rd(o)))
    lock = io.rd(LOCK) & 1
    hb1 = io.rd(HEARTBEAT)
    time.sleep(0.05)
    hb2 = io.rd(HEARTBEAT)
    say("#   %s (0xC0) = %d   heartbeat (0xB0): %d -> %d (+%d)"
        % (lock_desc, lock, hb1, hb2, hb2 - hb1))
    if lock != 1:
        say("# --- RED FLAG: " + red_flag + " ---")
    elif hb2 != hb1 and hb1 != 0:
        say("# --- TRUST OK: " + trust_ok + " ---")
    else:
        say("# --- WARNING: " + stuck_warn + " ---")
    say("# --- probe complete: AXI reads return, the bus is alive. ---")


def stream_events(io, tick_ns, stats_line, format_event, header):
    """The shared drain loop: poll STATUS, print each event via format_event, emit a
    stats line every ~1 s while idle. Runs until Ctrl-C, then prints a final stats
    line. format_event(ts, dt, event, data, is_tclk, has_data) -> str."""
    say(header)
    last_ts = None
    last_stats = time.monotonic()
    try:
        while True:
            if io.rd(STATUS) & 0x1:                    # empty
                now = time.monotonic()
                if now - last_stats >= 1.0:
                    say(stats_line())
                    last_stats = now
                time.sleep(0.001)
                continue
            event, flags, data, ts = read_event(io)
            is_tclk = (flags >> 1) & 1
            has_data = flags & 1
            dt = "   --  " if last_ts is None else "%7.1f" % ((ts - last_ts) * tick_ns / 1000.0)
            last_ts = ts
            say(format_event(ts, dt, event, data, is_tclk, has_data))
    except KeyboardInterrupt:
        say("\n# stopped.")
        say(stats_line())
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
.venv\Scripts\python.exe -m pytest deploy\test_readout_common.py deploy\test_tclk_filter.py -q
```
Expected: all PASS. Also run standalone: `.venv\Scripts\python.exe deploy\test_readout_common.py` prints `all readout_common tests passed`.

- [ ] **Step 5: Commit**

```bash
git add deploy/readout_common.py deploy/test_readout_common.py
git commit -m "feat(deploy): shared readout_common (register map, watchdog RegIO, drain loop) + unit tests"
```

---

### Task 4: Rewrite clk_read.py on readout_common

Output must stay identical: same header, same event line format, same stats fields, same probe verdicts (this reader's shorter wording preserved verbatim). Docstring preserved.

**Files:**
- Modify: `deploy/clk_read.py` (full rewrite)

**Interfaces:**
- Consumes: everything Task 3 produces.
- Produces: unchanged CLI (`clk_read.py [/dev/uioN] [--drop CODES]`), unchanged output.

- [ ] **Step 1: Rewrite deploy/clk_read.py to exactly this content**

```python
#!/usr/bin/env python3
"""Stream decoded events from the unified ACLK/TCLK readout over UIO.

Drains the AXI-Lite readout at 0x8000_0000: polls STATUS, reads each buffered event
(16-bit id + flags + 64-bit data + 64-bit hardware timestamp), pops it, prints a line.
is_tclk=1 marks a legacy 8-bit TCLK event; has_data=1 marks a full ACLK packet with a
64-bit payload. Every ~1 s prints a stats line incl. the DEBUG activity register (raw
serial-line transitions + level + serdec sig_err).

    sudo python3 clk_read.py /dev/uio4

Output is LINE-BUFFERED on purpose; a startup probe + watchdog name the exact register
if an AXI read wedges the bus. Shared plumbing lives in readout_common.py.
"""
import sys

import readout_common as rc
from readout_common import (
    STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG, HEARTBEAT, LOCK,
    FILTERED_COUNT, parse_drop_codes, say,
)

rc.line_buffer_stdout()

_pos, _flags = rc.parse_args(sys.argv[1:], value_flags=("--drop",))
DEV = _pos[0] if _pos else "/dev/uio4"
DROP_CODES = parse_drop_codes(_flags.get("--drop", ""))
TICK_NS = 25.0  # clk_40m = 40 MHz timestamp tick

io = rc.open_dev(DEV)
rc.apply_drop_filter(io, DROP_CODES)


def stats_line():
    dbg = io.rd(DEBUG)
    return "[stats] EVT=%d NULL=%d ERR=%d FILT=%d | line_edges=%d level=%d sig_err=%d | hb=%d lock=%d" % (
        io.rd(EVENT_COUNT), io.rd(NULL_COUNT), io.rd(ERROR_COUNT), io.rd(FILTERED_COUNT),
        dbg & 0x3FFFFFFF, (dbg >> 30) & 1, (dbg >> 31) & 1,
        io.rd(HEARTBEAT), io.rd(LOCK) & 1)


def format_event(ts, dt, event, data, is_tclk, has_data):
    data_str = "0x%016X" % data if has_data else "       --         "
    return "  %16d %s   0x%04X  %s    %d      %d" % (ts, dt, event, data_str, is_tclk, has_data)


say("# streaming ACLK/TCLK events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, rc.dev_offset(DEV)))
rc.probe(
    io, (STATUS, EVENT_COUNT, ERROR_COUNT, DEBUG),
    lock_desc="MMCM lock",
    red_flag="MMCM not locked => clk_40m/clk_80m dead; the decoder has no clock.",
    trust_ok=("heartbeat moving => AXI counter readback works. line_edges=0 just "
              "means no signal at the pin yet."),
    stuck_warn="MMCM locked but heartbeat STUCK => counter readback broken.",
)
say(stats_line())
rc.stream_events(io, TICK_NS, stats_line, format_event,
                 header="#        ts_ticks    dt_us   event     data               tclk  has_data")
```

- [ ] **Step 2: Verify**

```powershell
.venv\Scripts\python.exe -m py_compile deploy\clk_read.py
git diff deploy/clk_read.py
```
Expected: compiles clean. In the diff, confirm every printed string (header, stats format, probe verdicts, event line) survives character-for-character (the probe verdict strings are now passed as arguments, with the shared "heartbeat moving => ..." prefix identical).

- [ ] **Step 3: Commit**

```bash
git add deploy/clk_read.py
git commit -m "refactor(deploy): clk_read.py on readout_common (identical CLI + output)"
```

---

### Task 5: Rewrite tclk_read.py and aclk_read.py on readout_common

Same pattern as Task 4. tclk_read keeps `--tick-ns`, its `0x%02X` event line without a data column, its `tclk_edges` stats label, its NULL_COUNT in the probe list, and its longer verdict strings. aclk_read keeps its 120 MHz tick, `line_edges` stats without `sig_err`, and its verdicts.

**Files:**
- Modify: `deploy/tclk_read.py` (full rewrite)
- Modify: `deploy/aclk_read.py` (full rewrite)

**Interfaces:**
- Consumes: Task 3 API.
- Produces: unchanged CLIs (`tclk_read.py [dev] [--drop CODES] [--tick-ns NS]`, `aclk_read.py [dev] [--drop CODES]`), unchanged output.

- [ ] **Step 1: Rewrite deploy/tclk_read.py to exactly this content**

```python
#!/usr/bin/env python3
"""Stream decoded TCLK events from the PL readout over UIO.

Drains the AXI-Lite readout at 0x8000_0000: polls STATUS, reads each buffered event
(code + flags + 64-bit hardware timestamp), pops it, prints a line. Every ~1 s prints
a stats line: EVENT/NULL/ERROR counts + the DEBUG activity register (raw TCLK
transitions, which climb even if the decoder never locks).

    sudo python3 tclk_read.py /dev/uio4

Ctrl-C to stop. Diagnostic reading: tclk_edges climbing + EVT flat => signal present
but not decoding; tclk_edges flat => no signal / pin / front-end.

Output is LINE-BUFFERED on purpose; a startup probe + watchdog name the exact register
if an AXI read wedges the bus. Shared plumbing lives in readout_common.py.
"""
import sys

import readout_common as rc
from readout_common import (
    STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG, HEARTBEAT, LOCK,
    FILTERED_COUNT, parse_drop_codes, say,
)

rc.line_buffer_stdout()

_pos, _flags = rc.parse_args(sys.argv[1:], value_flags=("--drop", "--tick-ns"))
DEV = _pos[0] if _pos else "/dev/uio4"
DROP_CODES = parse_drop_codes(_flags.get("--drop", ""))
# Standalone: clk_40m 40 MHz = 25 ns. For the integrated pipeline (USE_EXT_TS=1) the
# timestamp is the shared pl_clk0 timebase, so pass --tick-ns 10 to make dt_us correct.
TICK_NS = float(_flags["--tick-ns"]) if "--tick-ns" in _flags else 25.0

io = rc.open_dev(DEV)
rc.apply_drop_filter(io, DROP_CODES)


def stats_line():
    dbg = io.rd(DEBUG)
    return "[stats] EVT=%d NULL=%d ERR=%d FILT=%d | tclk_edges=%d level=%d sig_err=%d | hb=%d lock=%d" % (
        io.rd(EVENT_COUNT), io.rd(NULL_COUNT), io.rd(ERROR_COUNT), io.rd(FILTERED_COUNT),
        dbg & 0x3FFFFFFF, (dbg >> 30) & 1, (dbg >> 31) & 1,
        io.rd(HEARTBEAT), io.rd(LOCK) & 1)


def format_event(ts, dt, event, data, is_tclk, has_data):
    return "  %16d %s   0x%02X    %d      %d" % (ts, dt, event & 0xFF, is_tclk, has_data)


say("# streaming TCLK events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, rc.dev_offset(DEV)))
rc.probe(
    io, (STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG),
    lock_desc="MMCM lock",
    red_flag=("MMCM not locked => clk_40m/clk_80m are dead; the TCLK "
              "receiver has no clock. Fix clocking before anything else."),
    trust_ok=("heartbeat moving => AXI counter readback works, so "
              "EVENT_COUNT / tclk_edges are trustworthy. Path is healthy; tclk_edges=0 "
              "just means no signal at the pin yet -> safe to wire up real TCLK."),
    stuck_warn=("MMCM locked but heartbeat STUCK => the cdc_gray_count AXI "
                "readback is still broken. EVENT_COUNT / tclk_edges read 0 even when alive, "
                "so fix the readback BEFORE wiring TCLK (else bring-up is uninterpretable)."),
)
say(stats_line())
rc.stream_events(io, TICK_NS, stats_line, format_event,
                 header="#        ts_ticks    dt_us   event  tclk  has_data")
```

- [ ] **Step 2: Rewrite deploy/aclk_read.py to exactly this content**

```python
#!/usr/bin/env python3
"""Stream decoded ACLK-Lite events from the PL readout over UIO.

Drains the AXI-Lite readout at 0x8000_0000: polls STATUS, reads each buffered event
(16-bit event id + flags + 64-bit data + 64-bit hardware timestamp), pops it, prints a
line. Every ~1 s prints a stats line: EVENT/NULL/ERROR/FILTERED counts + the DEBUG
activity register (raw Manchester line transitions, which climb even if the decoder
never frames).

    sudo python3 aclk_read.py /dev/uio4

Ctrl-C to stop. Diagnostic reading: line_edges climbing + EVT flat => signal present but
not decoding (check OVERSAMPLE / line bit rate); line_edges flat => no signal / pin.

Output is LINE-BUFFERED on purpose; a startup probe + watchdog name the exact register
if an AXI read wedges the bus. Shared plumbing lives in readout_common.py.
"""
import sys

import readout_common as rc
from readout_common import (
    STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG, HEARTBEAT, LOCK,
    FILTERED_COUNT, parse_drop_codes, say,
)

rc.line_buffer_stdout()

_pos, _flags = rc.parse_args(sys.argv[1:], value_flags=("--drop",))
DEV = _pos[0] if _pos else "/dev/uio4"
DROP_CODES = parse_drop_codes(_flags.get("--drop", ""))
TICK_NS = 1000.0 / 120.0  # clk_os = 120 MHz oversample/timestamp tick (~8.333 ns)

io = rc.open_dev(DEV)
rc.apply_drop_filter(io, DROP_CODES)


def stats_line():
    dbg = io.rd(DEBUG)
    return "[stats] EVT=%d NULL=%d ERR=%d FILT=%d | line_edges=%d level=%d | hb=%d lock=%d" % (
        io.rd(EVENT_COUNT), io.rd(NULL_COUNT), io.rd(ERROR_COUNT), io.rd(FILTERED_COUNT),
        dbg & 0x3FFFFFFF, (dbg >> 30) & 1,
        io.rd(HEARTBEAT), io.rd(LOCK) & 1)


def format_event(ts, dt, event, data, is_tclk, has_data):
    data_str = "0x%016X" % data if has_data else "       --         "
    return "  %16d %s   0x%04X  %s    %d      %d" % (ts, dt, event, data_str, is_tclk, has_data)


say("# streaming ACLK-Lite events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, rc.dev_offset(DEV)))
rc.probe(
    io, (STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG),
    lock_desc="MMCM lock",
    red_flag=("MMCM not locked => clk_os is dead; the ADM has no clock. "
              "Fix clocking before anything else."),
    trust_ok=("heartbeat moving => AXI counter readback works, so "
              "EVENT_COUNT / line_edges are trustworthy. line_edges=0 just means no signal "
              "at the pin yet -> safe to wire up a real ACLK-Lite source."),
    stuck_warn="MMCM locked but heartbeat STUCK => counter readback broken.",
)
say(stats_line())
rc.stream_events(io, TICK_NS, stats_line, format_event,
                 header="#        ts_ticks    dt_us   event     data               tclk  has_data")
```

- [ ] **Step 3: Verify + commit**

```powershell
.venv\Scripts\python.exe -m py_compile deploy\tclk_read.py deploy\aclk_read.py
git diff deploy/tclk_read.py deploy/aclk_read.py
```
Expected: compiles; diffs show only plumbing replaced, all printed strings preserved.

```bash
git add deploy/tclk_read.py deploy/aclk_read.py
git commit -m "refactor(deploy): tclk_read + aclk_read on readout_common (identical CLI + output)"
```

---

### Task 6: Rewrite aclkgt_read.py on readout_common

The GT reader keeps its unique pieces in-file: GT_CTRL flag parsing (`--gtctrl/--txdiff/--txpost/--txpre/--gtreset`), `set_gt_ctrl()`, the GT-health stats decode, and the GT probe verdicts. Everything else moves to readout_common.

**Files:**
- Modify: `deploy/aclkgt_read.py` (full rewrite)

**Interfaces:**
- Consumes: Task 3 API.
- Produces: unchanged CLI and output.

- [ ] **Step 1: Rewrite deploy/aclkgt_read.py to exactly this content**

```python
#!/usr/bin/env python3
"""Stream decoded gigabit ACLK / GT events from the PL readout over UIO.

Drains the AXI-Lite readout at 0x8000_0000: polls STATUS, reads each buffered event
(16-bit event id + flags + 64-bit data + 64-bit hardware timestamp), pops it, prints a
line. Every ~1 s prints a stats line: EVENT/NULL/ERROR/FILTERED counts + the DEBUG
activity register (raw activity transitions, which climb even if the decoder
never frames).

    sudo python3 aclkgt_read.py /dev/uio4

Ctrl-C to stop. Diagnostic reading: line_edges climbing + EVT flat => signal present but
not decoding (check GTH alignment / line bit rate); line_edges flat => no signal / SFP.

Output is LINE-BUFFERED on purpose; a startup probe + watchdog name the exact register
if an AXI read wedges the bus. Shared plumbing lives in readout_common.py.
"""
import sys
import time

import readout_common as rc
from readout_common import (
    STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG, HEARTBEAT, LOCK,
    FILTERED_COUNT, GT_CTRL, parse_drop_codes, say,
)

rc.line_buffer_stdout()

_pos, _flags = rc.parse_args(
    sys.argv[1:],
    value_flags=("--drop", "--gtctrl", "--txdiff", "--txpost", "--txpre", "--tick-ns"),
    bool_flags=("--gtreset",))
DEV = _pos[0] if _pos else "/dev/uio4"
DROP_CODES = parse_drop_codes(_flags.get("--drop", ""))
_gtreset = bool(_flags.get("--gtreset"))   # pulse GT_CTRL[24]: full RX PLL+CDR relock (use after a
                                           # runtime loopback/source switch; [8] does NOT relock the CDR)
GTCTRL = int(_flags["--gtctrl"], 0) if "--gtctrl" in _flags else 0x00  # default: known normal state
# TX driver sweep fields live in GT_CTRL[23:9]: [13:9]=txdiffctrl [18:14]=txpostcursor
# [23:19]=txprecursor. The PL uses 0x18 when the txdiffctrl field is 0, so leaving --txdiff
# unset keeps the proven default swing. Sweep these live to find a TX eye the SFP link locks on.
GTCTRL |= (int(_flags.get("--txdiff", "0"), 0) & 0x1F) << 9
GTCTRL |= (int(_flags.get("--txpost", "0"), 0) & 0x1F) << 14
GTCTRL |= (int(_flags.get("--txpre", "0"), 0) & 0x1F) << 19
# Standalone: GT RX usrclk2 62.5 MHz = 16 ns. For the integrated pipeline (USE_EXT_TS=1)
# the timestamp is the shared pl_clk0 timebase, so pass --tick-ns 10.
TICK_NS = float(_flags["--tick-ns"]) if "--tick-ns" in _flags else 1000.0 / 62.5

io = rc.open_dev(DEV)
rc.apply_drop_filter(io, DROP_CODES)


def set_gt_ctrl(val, full=False):
    """Write GT_CTRL with an RX re-init pulse so a new rxpolarity/loopback actually takes
    effect: assert bit8 (gtwiz_reset_rx_datapath) WITH the config bits, then release. With
    full=True also pulse bit24 (gtwiz_reset_rx_pll_and_datapath) for a TRUE CDR/PLL relock --
    needed when SWITCHING loopback/source at runtime (datapath-only does NOT relock the CDR to
    a new source). On the shared-QPLL self-test a full reset also blips TX briefly, then recovers.
    val: [0]=rxpol [1]=txpol [4:2]=loopback [13:9]=txdiff [18:14]=txpost [23:19]=txpre."""
    # Assert exactly ONE reset input: asserting rx_datapath ([8]) and rx_pll_and_datapath ([24])
    # together can wedge the gtwizard reset FSM (rx_done never re-asserts -> lock stays 0).
    # full => pll+datapath ([24], a superset that also resets the datapath); else datapath ([8]).
    reinit = 0x1000000 if full else 0x100
    io.wr(GT_CTRL, (val | reinit) & 0xFFFFFFFF)   # config + RX re-init asserted
    time.sleep(0.05 if full else 0.02)
    io.wr(GT_CTRL, val & ~reinit & 0xFFFFFFFF)     # release: RX re-inits & re-aligns under new config
    time.sleep(0.20 if full else 0.10)
    rb = io.rd(GT_CTRL)
    _td = (val >> 9) & 0x1F
    want = val & ~reinit & 0xFFFFFF             # config bits only (the re-init pulses self-release to 0)
    if (rb & 0xFFFFFF) != want:
        say("# !! WARNING: GT_CTRL readback 0x%08X != written 0x%06X (masked) -- AXI write may "
            "have failed (wrong stride / wedged write channel); the GT config did NOT change." % (rb, want))
    say("# GT_CTRL <- 0x%06X (rxpol=%d txpol=%d loopback=%d | txdiff=%s txpost=%d txpre=%d | full_reset=%d), readback=0x%08X" % (
        val, val & 1, (val >> 1) & 1, (val >> 2) & 7,
        ("0x18(dflt)" if _td == 0 else "0x%02X" % _td), (val >> 14) & 0x1F, (val >> 19) & 0x1F, int(full), rb))


def stats_line():
    # GT-link health DEBUG word (0xA0):
    #   [7:0]   commadet  = GT RX comma-detect count (8b, wraps)
    #   [11:8]  recover   = RX link-recovery FSM firings (4b, wraps); 0/low = link holding lock
    #   [12]    mod_abs   = SFP module absent (1 = no module present)
    #   [13]    tx_fault  = SFP TX fault (1 = fault asserted)
    #   [27:14] disperr   = GT 8b10b disparity-error count (14b, wraps)
    #   [28]    notintbl  = an 8b10b not-in-table (invalid-code) symbol seen this lock session
    #   [29]    rx_los    = SFP RX loss-of-signal (1 = NO optical input reaching the receiver)
    #   [30]    byteali   = GT RX byte-aligned
    #   [31]    rcv_algn  = ACLK_RCV decoder locked
    # Healthy link: rx_los=0, byteali=1 and rcv_aligned=1 holding solid, EVT climbing every
    # second, recover NOT climbing. recover climbing => the link keeps losing lock and the FSM is
    # self-healing it (eye still marginal). Read counters by "climbing" vs "frozen" (they wrap).
    dbg = io.rd(DEBUG)
    commadet = dbg & 0xFF
    recover  = (dbg >> 8) & 0xF
    mod_abs  = (dbg >> 12) & 1
    tx_fault = (dbg >> 13) & 1
    disperr  = (dbg >> 14) & 0x3FFF
    notintbl = (dbg >> 28) & 1
    rx_los   = (dbg >> 29) & 1
    byteali  = (dbg >> 30) & 1
    rcv_algn = (dbg >> 31) & 1
    return ("[stats] EVT=%d NULL=%d ERR=%d FILT=%d | commadet=%d disperr=%d recover=%d rx_los=%d "
            "tx_fault=%d mod_abs=%d notintbl=%d byteali=%d rcv_aligned=%d | dbg=0x%08X lock=%d") % (
        io.rd(EVENT_COUNT), io.rd(NULL_COUNT), io.rd(ERROR_COUNT), io.rd(FILTERED_COUNT),
        commadet, disperr, recover, rx_los, tx_fault, mod_abs, notintbl, byteali, rcv_algn,
        dbg, io.rd(LOCK) & 1)


def format_event(ts, dt, event, data, is_tclk, has_data):
    data_str = "0x%016X" % data if has_data else "       --         "
    return "  %16d %s   0x%04X  %s    %d      %d" % (ts, dt, event, data_str, is_tclk, has_data)


set_gt_ctrl(GTCTRL, full=_gtreset)   # apply a known config (default 0x00 = normal) so a prior run can't bleed in

say("# streaming gigabit ACLK / GT events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, rc.dev_offset(DEV)))
rc.probe(
    io, (STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG),
    lock_desc="GT RX lock",
    red_flag=("GT RX not locked => usrclk2 is dead; the readout has no clock. "
              "Fix GTH clocking / SFP before anything else."),
    trust_ok=("heartbeat moving => AXI counter readback works, so "
              "EVENT_COUNT / line_edges are trustworthy. line_edges=0 just means no signal "
              "at the SFP yet -> safe to wire up a real gigabit ACLK source."),
    stuck_warn="GT RX locked but heartbeat STUCK => counter readback broken.",
)
say(stats_line())
rc.stream_events(io, TICK_NS, stats_line, format_event,
                 header="#        ts_ticks    dt_us   event     data               tclk  has_data")
```

NOTE one intentional ordering nuance: the original applied the drop filter, then `set_gt_ctrl`, then printed the streaming banner. This rewrite preserves that order (filter via `apply_drop_filter` right after open, then `set_gt_ctrl`, then banner).

- [ ] **Step 2: Verify + commit**

```powershell
.venv\Scripts\python.exe -m py_compile deploy\aclkgt_read.py
git diff deploy/aclkgt_read.py
```
Expected: compiles; every printed string, flag, and GT_CTRL bit operation preserved.

```bash
git add deploy/aclkgt_read.py
git commit -m "refactor(deploy): aclkgt_read on readout_common (GT_CTRL + stats decode stay local)"
```

---

### Task 7: Convert aclkgt_monitor.py and aclkgt_sweep.py to readout_common

These two only re-implement the mmap open and bare `rd`/`wr` (no watchdog). Replace exactly that block with `rc.open_dev(DEV, announce=False, watchdog=False)`; keep their own arg loops (their unknown-flag behavior differs from the readers, and changing it would alter behavior), stats decode, and reporting untouched.

**Files:**
- Modify: `deploy/aclkgt_monitor.py:44-50` (the OFF/fd/mmap/rd block)
- Modify: `deploy/aclkgt_sweep.py:57-66` (the OFF/fd/mmap/rd/wr block)

**Interfaces:**
- Consumes: `rc.open_dev`, `io.rd`, `io.wr`.

- [ ] **Step 1: In aclkgt_monitor.py replace**

```python
STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = 0x00, 0x70, 0x80, 0x90, 0xA0
LOCK, FILTERED_COUNT = 0xC0, 0xE0
OFF = 0 if "uio" in DEV else 0x8000_0000

fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OFF)

def rd(o):
    return struct.unpack("<I", m[o:o + 4])[0]
```

with

```python
import readout_common as rc
from readout_common import STATUS, EVENT_COUNT, ERROR_COUNT, DEBUG, LOCK

_io = rc.open_dev(DEV, announce=False, watchdog=False)

def rd(o):
    return _io.rd(o)
```

and change the module import line `import mmap, os, struct, sys, time` to `import sys, time` (mmap/os/struct now unused).

- [ ] **Step 2: In aclkgt_sweep.py replace**

```python
# ---- register map (16-byte stride, matches aclk_readout_axi) ----
EVENT_COUNT, DEBUG, GT_CTRL = 0x70, 0xA0, 0xF0
OFF = 0 if "uio" in DEV else 0x8000_0000

fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OFF)

def rd(o):
    return struct.unpack("<I", m[o:o + 4])[0]

def wr(o, v):
    m[o:o + 4] = struct.pack("<I", v & 0xFFFFFFFF)
```

with

```python
# ---- register map (16-byte stride, matches aclk_readout_axi) ----
import readout_common as rc
from readout_common import EVENT_COUNT, DEBUG, GT_CTRL

_io = rc.open_dev(DEV, announce=False, watchdog=False)

def rd(o):
    return _io.rd(o)

def wr(o, v):
    _io.wr(o, v)
```

and change `import mmap, os, struct, sys, time` to `import sys, time`.

- [ ] **Step 3: Verify + commit**

```powershell
.venv\Scripts\python.exe -m py_compile deploy\aclkgt_monitor.py deploy\aclkgt_sweep.py
```
Expected: compiles clean (py_compile does not execute the module body, so the board-only open is not triggered).

```bash
git add deploy/aclkgt_monitor.py deploy/aclkgt_sweep.py
git commit -m "refactor(deploy): monitor + sweep use readout_common register access"
```

---

### Task 8: hw.ps1 deploy map ships readout_common.py (+ aclkgt/pipeline entries)

The readers now import `readout_common`, so `hw.ps1 deploy` must scp it. Also add the previously missing mappings for the aclkgt builds and the pipeline (their runbooks currently make users scp by hand; additive improvement).

**Files:**
- Modify: `hw.ps1:233-238` (the `$pyMap` literal)
- Modify: `deploy/README.md` (one sentence noting readout_common.py must sit beside the readers)

**Interfaces:**
- Consumes: file names from Tasks 3-7.

- [ ] **Step 1: Replace the `$pyMap` block in hw.ps1 with**

```powershell
        $pyMap = @{
            "tclk"            = @("tclk_read.py", "tclk_filter.py", "readout_common.py")
            "aclk"            = @("aclk_read.py", "tclk_filter.py", "readout_common.py")
            "clk"             = @("clk_read.py", "tclk_filter.py", "readout_common.py")
            "aclkgt_loop"     = @("aclkgt_read.py", "aclkgt_monitor.py", "aclkgt_sweep.py", "tclk_filter.py", "readout_common.py")
            "aclkgt_rx"       = @("aclkgt_read.py", "aclkgt_monitor.py", "aclkgt_sweep.py", "tclk_filter.py", "readout_common.py")
            "aclkgt_selftest" = @("aclkgt_read.py", "aclkgt_monitor.py", "aclkgt_sweep.py", "tclk_filter.py", "readout_common.py")
            "aclk_pipeline"   = @("tclk_read.py", "aclkgt_read.py", "tclk_filter.py", "readout_common.py")
            "uart_echo"       = @("uart_echo_test.py")
        }
```

- [ ] **Step 2: Add to deploy/README.md (near the reader description)**

```markdown
All readers import `readout_common.py` (shared register map + watchdog + drain loop);
`hw.ps1 deploy` copies it automatically. If you scp a reader by hand, copy
`readout_common.py` and `tclk_filter.py` alongside it.
```

- [ ] **Step 3: Syntax-check hw.ps1 (deploy needs a board, so validate parse + map only)**

```powershell
powershell -NoProfile -Command "$t = Get-Content .\hw.ps1 -Raw; $null = [scriptblock]::Create($t); 'parse ok'"
.\hw.ps1 help
```
Expected: `parse ok`, then the help text.

- [ ] **Step 4: Commit**

```bash
git add hw.ps1 deploy/README.md
git commit -m "feat(deploy): hw.ps1 ships readout_common.py; add aclkgt/pipeline deploy maps"
```

---

### Task 9: tb/runner_common.py factory + 4 pilot runner conversions

The factory absorbs the ~40 lines each of the 30 runners duplicated. Pilots cover the four variation axes: plain module (counter), multi-source with shared model dir (clk_rcv), SV wrapper + shared BFM (aclk_readout_axi), HDL parameters (uart_receiver).

**Files:**
- Create: `tb/runner_common.py`
- Modify: `tb/counter/runner.py`, `tb/clk_rcv/runner.py`, `tb/aclk_readout_axi/runner.py`, `tb/uart_receiver/runner.py` (full rewrites)

**Interfaces:**
- Produces: `run_cocotb(name, sources, hdl_toplevel, parameters=None, test_module=None)`; `sources` are repo-root-relative strings; `name` = tb dir = sim_build dir; default test module `test_<name>`.

- [ ] **Step 1: Create tb/runner_common.py**

```python
"""Shared cocotb 2.0 runner factory.

One place for what every tb/<name>/runner.py used to duplicate: the SIM env var,
the OSS_CAD_SUITE PATH setup, the sys.path/PYTHONPATH wiring, the build dir layout,
and the build+test call. A runner reduces to:

    from runner_common import run_cocotb
    run_cocotb("<name>", sources=["rtl/x.sv", ...], hdl_toplevel="x")

Why a Python runner (not a Makefile)?
  - No `make` dependency, which matters on Windows.
  - It is the direction cocotb is steering for 2.0; pure Python and portable.

Switch simulators from the shell:
    $env:SIM = "verilator"      # PowerShell
    export SIM=verilator        # bash
"""
import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

TB_ROOT  = Path(__file__).resolve().parent       # tb/
PROJ_DIR = TB_ROOT.parent                        # repo root


def run_cocotb(name, sources, hdl_toplevel, parameters=None, test_module=None):
    """Build + run one testbench.

    name          tb/<name>/ suite dir; the build lands in sim_build/<name>/
    sources       HDL paths RELATIVE TO THE REPO ROOT, e.g. "rtl/async_fifo.sv"
                  or "tb/<name>/tb_x_top.sv"
    hdl_toplevel  the top module compiled for the sim
    parameters    optional dict of HDL parameters
    test_module   cocotb test module (default: test_<name>)
    """
    sim = os.getenv("SIM", "icarus")
    tb_dir = TB_ROOT / name
    build = PROJ_DIR / "sim_build" / name

    # The runner propagates sys.path to the simulator process as PYTHONPATH: the
    # suite dir for test_<name>.py, tb/ for the shared models + cocotb_helpers.
    for p in (str(tb_dir), str(TB_ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)

    # Best-effort: honor OSS_CAD_SUITE if set; otherwise rely on the tools already
    # being on PATH (the sim.sh / sim.ps1 wrappers put them there for you).
    _oss = os.getenv("OSS_CAD_SUITE")
    if _oss and (Path(_oss) / "bin").is_dir():
        os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")

    runner = get_runner(sim)
    # Verilator traces to FST only when asked; these args are ignored by Icarus.
    build_args = ["--trace-fst", "--trace-structs"] if sim == "verilator" else []
    runner.build(
        sources=[PROJ_DIR / s for s in sources],
        hdl_toplevel=hdl_toplevel,
        build_dir=build,
        build_args=build_args,
        parameters=parameters or {},
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel=hdl_toplevel,
        test_module=test_module or f"test_{name}",
        build_dir=build,
        waves=True,
    )
```

- [ ] **Step 2: Rewrite tb/counter/runner.py**

```python
"""Cocotb 2.0 runner for the counter smoke test (shared plumbing: tb/runner_common.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tb/ for runner_common
from runner_common import run_cocotb


def test_counter():
    run_cocotb("counter", sources=["rtl/counter.sv"], hdl_toplevel="counter")


if __name__ == "__main__":
    test_counter()
```

- [ ] **Step 3: Rewrite tb/clk_rcv/runner.py**

```python
"""Cocotb 2.0 runner for the unified ACLK/TCLK decoder rtl/aclk_lite/clk_rcv
(serdec4_9MHz + clk_byte_framer). The line is driven by the real-framing model in
tb/clk_tx_model.py. Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_clk_rcv():
    run_cocotb(
        "clk_rcv",
        sources=[
            "rtl/aclk_bridge/serdec4_9MHz.v",
            "rtl/aclk_lite/clk_byte_framer.sv",
            "rtl/aclk_lite/clk_rcv.sv",
        ],
        hdl_toplevel="clk_rcv",
    )


if __name__ == "__main__":
    test_clk_rcv()
```

- [ ] **Step 4: Rewrite tb/aclk_readout_axi/runner.py**

```python
"""Cocotb 2.0 runner for the AXI-Lite readout testbench: the real decoder
(ACLK_RCV + GEARBOX_16_TO_96 + CRC8_CALC) feeding aclk_readout_axi. Driven by the
shared tb/aclk_tx_model.py. Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_readout_axi():
    run_cocotb(
        "aclk_readout_axi",
        sources=[
            "rtl/aclk_bridge/crc8_calc.v",
            "rtl/aclk_bridge/GEARBOX_16_TO_96.v",
            "rtl/aclk_bridge/ACLK_REV.v",
            "rtl/synchronizer.sv",
            "rtl/async_fifo.sv",
            "rtl/cdc_gray_count.sv",
            "rtl/aclk_readout/aclk_readout_core.sv",
            "rtl/aclk_readout/aclk_readout_axi.sv",
            "tb/aclk_readout_axi/tb_aclk_readout_axi_top.sv",
        ],
        hdl_toplevel="tb_aclk_readout_axi_top",
    )


if __name__ == "__main__":
    test_aclk_readout_axi()
```

- [ ] **Step 5: Rewrite tb/uart_receiver/runner.py**

Open the current file first and carry over its exact sources and parameters; the shape is:

```python
"""Cocotb 2.0 runner for uart_receiver (shared plumbing: tb/runner_common.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_uart_receiver():
    run_cocotb(
        "uart_receiver",
        sources=["rtl/uart_receiver.sv"],          # carry over the CURRENT file's exact list
        hdl_toplevel="uart_receiver",
        parameters={"CLOCK_FREQ": 100, "BAUD_RATE": 10},
    )


if __name__ == "__main__":
    test_uart_receiver()
```

- [ ] **Step 6: Run all four pilots**

```powershell
.\sim.ps1 run -Module counter
.\sim.ps1 run -Module clk_rcv
.\sim.ps1 run -Module aclk_readout_axi
.\sim.ps1 run -Module uart_receiver
```
Expected: 4x cocotb PASS. If a suite fails to IMPORT a shared model, the factory's tb/ sys.path insert is wrong; fix the factory, not the suite.

- [ ] **Step 7: Commit**

```bash
git add tb/runner_common.py tb/counter/runner.py tb/clk_rcv/runner.py tb/aclk_readout_axi/runner.py tb/uart_receiver/runner.py
git commit -m "refactor(tb): runner_common factory + 4 pilot runner conversions"
```

---

### Task 10: Convert the remaining 26 runners + full-suite regression

Mechanical rule per runner: open it, note its `sources=[...]` list (translate `RTL_DIR / "x"` to `"rtl/x"`, `TB_DIR / "y"` to `"tb/<name>/y"`), its `hdl_toplevel`, any `parameters={...}`, and its `test_module` (only pass `test_module=` if it is NOT `test_<name>`, e.g. `aclk_readout_ext_ts` uses `test_ext_ts`). Rewrite in the Task 9 pilot shape, keeping the original docstring's first line. Do not change any source list, toplevel, or parameter value.

**Files:**
- Modify: `tb/<name>/runner.py` for every suite except the 4 pilots: aclk_gen_bd_top, aclk_lite_bridge, aclk_lite_decoder, aclk_lite_encoder, aclk_lite_gen_loopback, aclk_lite_readout, aclk_pipeline_chain, aclk_rcv, aclk_readout, aclk_readout_ext_ts, aclk_tclk_encoder_loop, aclkgt_gen, aclkgt_gen_loop, aclkgt_readout, async_fifo, button_parser, clk_readout, debouncer, edge_detector, fifo, global_timebase, synchronizer, tclk_rcv, tclk_readout, uart_echo_top, uart_transmitter

**Interfaces:**
- Consumes: `run_cocotb` from Task 9.

- [ ] **Step 1: Convert every runner per the rule above**

- [ ] **Step 2: Confirm no runner still carries the old boilerplate**

```powershell
Select-String -Path tb\*\runner.py -Pattern "get_runner|OSS_CAD_SUITE"
```
Expected: NO output (only runner_common.py contains these now).

- [ ] **Step 3: Full-suite regression (the real gate)**

```powershell
$fail = @()
Get-ChildItem tb -Directory | Where-Object { Test-Path (Join-Path $_.FullName "runner.py") } | ForEach-Object {
    Write-Host "=== $($_.Name) ==="
    .\sim.ps1 run -Module $_.Name
    if ($LASTEXITCODE -ne 0) { $fail += $_.Name }
}
if ($fail) { throw "FAILED suites: $($fail -join ', ')" } else { "ALL SUITES PASS" }
```
Expected: `ALL SUITES PASS` (30 suites). Any failure: fix that suite's conversion (usually a mistranslated source path) before proceeding.

- [ ] **Step 4: Commit**

```bash
git add tb/*/runner.py
git commit -m "refactor(tb): all runners on runner_common (full 30-suite regression green)"
```

---

### Task 11: Update the sim.ps1 / sim.sh scaffold templates

`sim new <name>` writes a runner from an embedded template; it must emit the factory style so new suites do not reintroduce the boilerplate.

**Files:**
- Modify: `sim.ps1` (the `$runnerTpl` here-string, currently the old ~60-line template ending at line 208)
- Modify: `sim.sh` (its equivalent runner template here-doc; locate with `grep -n "get_runner" sim.sh`)

- [ ] **Step 1: Replace the `$runnerTpl` here-string body in sim.ps1 with**

```python
"""Cocotb 2.0 runner for rtl/__MOD__.sv (shared plumbing: tb/runner_common.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tb/ for runner_common
from runner_common import run_cocotb


def test___MOD__():
    run_cocotb("__MOD__", sources=["rtl/__MOD__.sv"], hdl_toplevel="__MOD__")


if __name__ == "__main__":
    test___MOD__()
```

(keep it inside the existing `@'...'@` here-string with the `__MOD__` placeholder; do not touch `$testTpl` or `$rtlTpl`).

- [ ] **Step 2: Make the same replacement in sim.sh's runner template**

- [ ] **Step 3: Test the scaffold end-to-end, then remove the scratch module**

```powershell
.\sim.ps1 new scaffoldtest
.\sim.ps1 run -Module scaffoldtest
Remove-Item -Recurse -Force tb\scaffoldtest, rtl\scaffoldtest.sv, sim_build\scaffoldtest
```
Expected: scaffolded suite builds and its smoke test passes before removal.

- [ ] **Step 4: Commit**

```bash
git add sim.ps1 sim.sh
git commit -m "refactor(sim): scaffold template emits runner_common-style runners"
```

---

### Task 12: tb/cocotb_helpers.py and de-duplicate _b / start_clock

`_b(sig)` is defined in 10 test files; single-clock `start_clock(dut)` wrappers in ~10 more. Both move to `tb/cocotb_helpers.py` (importable because runner_common puts tb/ on PYTHONPATH). Multi-clock `_start_clocks` helpers and per-DUT `reset_dut` stay local (genuinely suite-specific).

**Files:**
- Create: `tb/cocotb_helpers.py`
- Modify: every `tb/*/test_*.py` that defines `_b` or a single-clock `start_clock` (find with Step 2's grep). Known `_b` definers: aclk_readout, aclk_readout_axi, aclk_lite_bridge, aclk_lite_encoder, aclk_lite_decoder, aclk_lite_gen_loopback, clk_rcv, tclk_rcv, async_fifo. Known `start_clock` definers: counter, uart_receiver, uart_transmitter, uart_echo_top, synchronizer, fifo, debouncer, edge_detector, aclk_rcv, button_parser.

**Interfaces:**
- Produces: `_b(sig) -> int` (int value, -1 while unresolved) and `start_clock(sig, period_ns=10)` in `cocotb_helpers`.

- [ ] **Step 1: Create tb/cocotb_helpers.py**

```python
"""Shared cocotb test helpers, importable from any suite because runner_common
puts tb/ on the simulator's PYTHONPATH."""
import cocotb
from cocotb.clock import Clock


def _b(sig) -> int:
    """Signal value as int; -1 while unresolved (x/z)."""
    try:
        return int(sig.value)
    except Exception:
        return -1


def start_clock(sig, period_ns=10):
    """Start a free-running clock on `sig` (pass the clock SIGNAL, e.g. dut.clk)."""
    cocotb.start_soon(Clock(sig, period_ns, unit="ns").start())
```

- [ ] **Step 2: Find every local definition**

```powershell
Select-String -Path tb\*\test_*.py -Pattern "^def _b\(|^def start_clock\("
```

- [ ] **Step 3: Convert each file**

For `_b`: delete the local `def _b(...)` block and add `from cocotb_helpers import _b` to the imports. NOTE: `tb/axi_lite_bfm.py` keeps its own `_b` (it is a standalone shared model; leave it).

For `start_clock`: delete the local def; add `from cocotb_helpers import start_clock`; rewrite each call site from `start_clock(dut)` to `start_clock(dut.clk, <the period the local def used>)`. Worked example (tb/counter/test_counter.py pattern):

```python
# before
def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
...
    start_clock(dut)

# after
from cocotb_helpers import start_clock
...
    start_clock(dut.clk, CLK_PERIOD_NS)
```

If a suite's local helper clocks a signal not named `clk`, pass that signal explicitly. If a suite's `_b` differs from the shared one in ANY way, leave that suite alone and note it in the commit message.

- [ ] **Step 4: Run every touched suite**

```powershell
# run each suite modified in Step 3, e.g.:
.\sim.ps1 run -Module counter
.\sim.ps1 run -Module clk_rcv
# ...one call per touched suite
```
Expected: PASS for every touched suite.

- [ ] **Step 5: Commit**

```bash
git add tb/cocotb_helpers.py tb/*/test_*.py
git commit -m "refactor(tb): shared _b/start_clock in cocotb_helpers (suites converted, all green)"
```

---

### Task 13: plot_util.save_line_plot + de-duplicate plot code

`tb/plot_util.py` gains `save_line_plot`; the suites that hand-roll a line plot switch to it. The FIFO-occupancy reimplementations are converted ONLY where visually equivalent to `save_fifo_plot` (same two panels, same series); if a local plot draws anything extra, leave it (no functionality loss).

**Files:**
- Modify: `tb/plot_util.py` (append function)
- Modify: `tb/aclk_lite_encoder/test_aclk_lite_encoder.py`, `tb/aclk_lite_decoder/test_aclk_lite_decoder.py`, `tb/aclk_lite_bridge/test_aclk_lite_bridge.py` (line-plot users)
- Modify (conditionally): `tb/aclk_readout/test_aclk_readout.py`, `tb/aclk_lite_readout/test_aclk_lite_readout.py`, `tb/aclk_readout_axi/test_aclk_readout_axi.py`, `tb/tclk_readout/test_tclk_readout.py` (only if their local FIFO plot is equivalent to `save_fifo_plot`)

**Interfaces:**
- Produces: `save_line_plot(levels, title, out_path) -> Path | None` in `plot_util`.

- [ ] **Step 1: Append to tb/plot_util.py**

```python
def save_line_plot(levels, title, out_path):
    """Write a step plot of captured serial-line levels vs sample index.

    Parameters
    ----------
    levels   : list of 0/1 line samples
    title    : plot title string
    out_path : Path (or str) -- destination .png file; parent dirs are created
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return None
    if not levels:
        return None

    xs = list(range(len(levels)))
    fig, ax = plt.subplots(figsize=(11, 3))
    ax.step(xs, levels, where="post", color="tab:green", lw=1.4)
    ax.set_ylim(-0.2, 1.2)
    ax.set_yticks([0, 1])
    ax.set_xlabel("oversampling-clock sample")
    ax.set_ylabel("line")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
```

- [ ] **Step 2: Convert the encoder test (worked example; decoder and bridge follow the same shape)**

In `tb/aclk_lite_encoder/test_aclk_lite_encoder.py`: delete the local `_save_line_plot` (lines 55-76), add imports, and update the call:

```python
from pathlib import Path
from plot_util import save_line_plot

PLOTS = Path(__file__).resolve().parents[2] / "sim_build" / "aclk_lite_encoder" / "plots"
```

```python
        plot_path = save_line_plot(
            full_frame_levels,
            "ACLK-Lite encoder line: full 12-byte packet (event 0x1234 + data)",
            PLOTS / "encoder_frame.png",
        )
```

Repeat for `aclk_lite_decoder` and `aclk_lite_bridge` with their own titles/filenames/sim_build subdirs, carried over from their local functions.

- [ ] **Step 3: Assess the four FIFO-plot reimplementations**

For each of aclk_readout / aclk_lite_readout / aclk_readout_axi / tclk_readout: read the local plot function; if it draws exactly the two-panel occupancy + cumulative figure from the same `(time_ns, cum_in, cum_read)` series, replace with `from plot_util import save_fifo_plot` + one call. If it differs (extra panel, different series), LEAVE IT and note it.

- [ ] **Step 4: Run every touched suite and confirm the plot file exists**

```powershell
.\sim.ps1 run -Module aclk_lite_encoder
Test-Path sim_build\aclk_lite_encoder\plots\encoder_frame.png
# ... same pattern per touched suite
```
Expected: PASS + `True` for each.

- [ ] **Step 5: Commit**

```bash
git add tb/plot_util.py tb/*/test_*.py
git commit -m "refactor(tb): shared save_line_plot; converted equivalent local plot code"
```

---

### Task 14: Update the documentation to match

**Files:**
- Modify: `docs/FUNCTIONALITY.md` (mark the 8 legacy modules + Li_Files as removed; add the three new shared modules under their sections)
- Modify: `docs/PROJECT.md` (repository-layout notes if they mention the removed files)
- Modify: `README.md` (repository-layout block: add `deploy/readout_common.py`, `tb/runner_common.py`, `tb/cocotb_helpers.py` one-liners if the tree there lists neighbors at that granularity)

- [ ] **Step 1: Edit docs/FUNCTIONALITY.md**

In section "Legacy-unused (safe-to-remove candidates)" retitle to "Removed 2026-07-02 (efficiency-cleanup branch)" and state the 8 files + Li_Files were deleted (history retrievable via git). In section 5 (Deploy) add `readout_common.py` with one line. In section 3 (Testbenches) add `runner_common.py` and `cocotb_helpers.py` to the shared-models sentence.

- [ ] **Step 2: Grep docs for now-stale references**

```powershell
Select-String -Path README.md, docs\PROJECT.md -Pattern "Li_Files|BitEncoder|FrameEncoder|TimelineGenerator|lfsr80"
```
Fix any hit.

- [ ] **Step 3: Final full check + commit**

```powershell
.venv\Scripts\python.exe -m pytest deploy -q
git status --porcelain
```
Expected: deploy tests pass; only doc files staged/modified.

```bash
git add docs/FUNCTIONALITY.md docs/PROJECT.md README.md
git commit -m "docs: record efficiency cleanup (removed legacy modules, new shared modules)"
```

---

## On-board follow-up (manual, next board session; NOT part of this plan's automation)

1. `.\hw.ps1 deploy -Name clk -DeployHost ubuntu@<board>` then on the board `sudo python3 -u clk_read.py /dev/uio4 --drop 07`: confirm probe text, stats line, event lines match a pre-refactor capture.
2. Same smoke for `tclk_read.py --tick-ns 10` (pipeline build) and `aclkgt_read.py --txdiff 0x18`.
3. `aclkgt_monitor.py --interval 1 --report 10` for a minute; confirm summary renders.
