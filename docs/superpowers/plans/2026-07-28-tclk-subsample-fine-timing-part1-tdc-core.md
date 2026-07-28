# TCLK Sub-Sample Fine-Timing - Part 1: Multiphase Edge-TDC Core - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and sim-prove the standalone multiphase edge-TDC (thermometer decoder + 4-phase sampler) and its software calibration, plus a characterization task that verifies the byte-completion timestamp-dither premise - the self-contained core of the sub-sample timestamp feature.

**Architecture:** A pure-combinational thermometer decoder (`tclk_fine_decode`) maps four phase-shifted line samples to a sub-bin + validity flag. A sampler wrapper (`tclk_fine_tdc`) captures the raw line on four 200 MHz clocks at 0/90/180/270 degrees, synchronizes them into the 0-degree domain, and feeds the decoder. A Python routine calibrates the (non-uniform) bin widths from a known-periodic marker. This part does not touch the decoder, clocking BD, or readout path - that is Part 2.

**Tech Stack:** SystemVerilog/Verilog (Icarus via the cocotb 2.0 Python runner, `tb/runner_common.py`), cocotb tests, NumPy for the calibration routine.

## Global Constraints

- **Do not modify the existing receiver** (`serdec4_9MHz.v`, `TCLK_DESERIALIZER2.v`, `TCLK_RCV.v`) in this part - the new blocks are standalone. (Part 2 adds one tap.)
- **Verilog-2001 for `.v` files** (Vivado synth): use `integer` not `int`, sized zero-fill literals. New files here are `.sv` and sim-only-proven, but keep them synthesizable (no test-only constructs).
- **Integer-nanosecond / picosecond timing in tests**; the sim time unit is picoseconds where sub-ns matters.
- **Runner pattern:** every testbench is `tb/<name>/runner.py` + `tb/<name>/test_<name>.py`, built via `from runner_common import run_cocotb`. Default `SIM=icarus`. Sources are repo-root-relative.
- **Fine encoding (fixed for both parts):** `fine_phase` is 2 bits; `fine_valid` is 1 bit. In the packed word (Part 2) these live at `FLAGS[3:2]` and `FLAGS[4]`; `FLAGS[1:0]` stay `is_tclk`/`has_data`.
- **Graceful fallback:** the decoder must raise `fine_valid = 0` for any non-thermometer (glitch) sample pattern rather than emit a wrong bin.

---

## File Structure

- Create `rtl/aclk_lite/tclk_fine_decode.sv` - combinational thermometer decoder (4 samples -> `fine_phase`, `fine_valid`).
- Create `rtl/aclk_lite/tclk_fine_tdc.sv` - 4-phase sampler + synchronizers, instantiates `tclk_fine_decode`.
- Create `tb/tclk_fine_tdc/runner.py`, `tb/tclk_fine_tdc/test_tclk_fine_tdc.py` - unit tests for both new modules (decoder truth table + sampler sweep + glitch).
- Create `deploy/fine_calibrate.py` - software bin-width calibration from a periodic marker.
- Create `deploy/test_fine_calibrate.py` - test for the calibration routine.
- Create `tb/tclk_readout/test_tclk_ts_jitter.py` - characterization test (measures byte-completion timestamp dither; added to the existing `tclk_readout` suite).

---

## Task 1: Characterize the byte-completion timestamp dither

Establish, in sim, how much the current `DAVn`-latched timestamp dithers relative to the true line-edge cadence, and confirm the source is the recovered-SCLK-to-`clk_40m` resync (bounded by ~1 `clk_40m` period + sync), not byte-assembly latency. This validates the increment-C premise before Part 2 builds on it.

**Files:**
- Create: `tb/tclk_readout/test_tclk_ts_jitter.py`
- Reference (read only): `tb/tclk_readout/test_tclk_readout.py`, `tb/tclk_readout/runner.py`, `tb/tclk_tx_model.py`

**Interfaces:**
- Consumes: the existing `tclk_readout_top` DUT and its `runner.py` (already builds all sources); `tclk_tx_model` (`biphase_samples`, `event_bits`, `drive_samples`, `SAMPLES_PER_CELL`); `axi_lite_bfm` (`axi_read`, `axi_write`).
- Produces: nothing consumed by later tasks; a measurement + a soft assertion that the dither is bounded by the resync hypothesis.

- [ ] **Step 1: Write the characterization test**

Drive a long, strictly-periodic single-event stream (repeat the same byte at a fixed cell cadence so the true event period is constant), read every timestamp over AXI, and measure the interval dither. Because the driver places line samples on `clk_80m` edges, the *true* period is exact; any spread in the read-back intervals is the readout's own dither.

```python
# tb/tclk_readout/test_tclk_ts_jitter.py
"""Characterization: quantify the DAVn-latched timestamp dither and confirm it is
the recovered-SCLK-to-clk_40m resync beat (bounded), not byte-assembly latency.
Runs on the 200 MHz timestamp clock (TCLK_CLK40_PS=5000) to match the board build."""
import os
import statistics
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer

from tclk_tx_model import biphase_samples, event_bits, drive_samples, SAMPLES_PER_CELL
from axi_lite_bfm import axi_read, axi_write

# reuse the register map + helpers from the sibling test
from test_tclk_readout import (
    STATUS, EVENT, TS_HI, TS_LO, POP,
    _start_clocks, reset_dut, axi_read_event, CLK40_PERIOD_PS,
)

N_EVENTS = 30
GAP_CELLS = 12


async def _drive_periodic(dut, byte, n, acct):
    warm, level = biphase_samples([1] * 40, level=1)
    await drive_samples(dut.clk_80m, dut.tclk, warm)
    acct["warm_done"] = True
    for _ in range(n):
        s, level = biphase_samples(event_bits(byte), level)
        await drive_samples(dut.clk_80m, dut.tclk, s)
        g, level = biphase_samples([1] * GAP_CELLS, level)
        await drive_samples(dut.clk_80m, dut.tclk, g)
    acct["drive_done"] = True
    while not acct.get("stop"):
        g, level = biphase_samples([1] * GAP_CELLS, level)
        await drive_samples(dut.clk_80m, dut.tclk, g)


@cocotb.test()
async def test_ts_dither_bounded(dut):
    _start_clocks(dut)
    await reset_dut(dut)
    acct = {}
    cocotb.start_soon(_drive_periodic(dut, 0x9D, N_EVENTS, acct))
    while not acct.get("warm_done"):
        await ClockCycles(dut.clk_40m, 8)
    while not acct.get("drive_done"):
        await ClockCycles(dut.clk_40m, 16)
    await ClockCycles(dut.clk_40m, 40)

    ts = []
    while True:
        if (await axi_read(dut, STATUS)) & 0x1:
            break
        _ev, _fl, _d, t = await axi_read_event(dut)
        ts.append(t)
    acct["stop"] = True

    assert len(ts) >= N_EVENTS - 2, f"only {len(ts)} events read"
    intervals = [b - a for a, b in zip(ts, ts[1:])]
    # timestamp ticks -> ns (200 MHz build -> 5 ns per tick when CLK40_PERIOD_PS=5000)
    tick_ns = CLK40_PERIOD_PS / 1000.0
    spread_ticks = max(intervals) - min(intervals)
    dut._log.info(
        f"period ticks median={statistics.median(intervals)}, "
        f"spread={spread_ticks} ticks (~{spread_ticks*tick_ns:.1f} ns), "
        f"stdev={statistics.pstdev(intervals):.2f} ticks"
    )
    # Resync hypothesis: dither is bounded by a small number of clk_40m periods,
    # NOT hundreds of ns of byte-assembly variation. Assert the bound.
    assert spread_ticks <= 6, (
        f"interval spread {spread_ticks} ticks exceeds the resync bound; "
        f"the dither is not a simple resync beat - revisit the increment-C premise"
    )
```

- [ ] **Step 2: Add the test module to the runner**

`run_cocotb` runs the module named `test_<name>` by default. Add an explicit runner entry so the characterization module is built and run alongside the suite.

```python
# append to tb/tclk_readout/runner.py
def test_tclk_ts_jitter():
    run_cocotb(
        "tclk_readout",
        sources=[
            "rtl/synchronizer.sv", "rtl/async_fifo.sv", "rtl/cdc_gray_count.sv",
            "rtl/aclk_readout/aclk_readout_core.sv", "rtl/aclk_readout/aclk_readout_axi.sv",
            "rtl/aclk_bridge/serdec4_9MHz.v", "rtl/aclk_bridge/TCLK_DESERIALIZER2.v",
            "rtl/aclk_bridge/TCLK_RCV.v", "rtl/aclk_lite/tclk_readout_top.sv",
        ],
        hdl_toplevel="tclk_readout_top",
        parameters={"OSR": int(os.getenv("TCLK_OSR", "8"))},
        test_module="test_tclk_ts_jitter",
    )
```

- [ ] **Step 3: Run it on the 200 MHz timestamp clock and record the finding**

Run: `TCLK_CLK40_PS=5000 python -m pytest tb/tclk_readout/runner.py::test_tclk_ts_jitter -s`
Expected: PASS, with a logged period spread. Record the measured spread (ticks and ns) and the stdev in the commit message - this is the characterization result Part 2 correlates against.

- [ ] **Step 4: Commit**

```bash
git add tb/tclk_readout/test_tclk_ts_jitter.py tb/tclk_readout/runner.py
git commit -m "test(tclk): characterize DAVn timestamp dither (resync-beat bound)"
```

---

## Task 2: Thermometer decoder (`tclk_fine_decode`)

Pure-combinational core: map four phase-ordered line samples to a sub-bin and a validity flag. A clean single crossing is a monotone (thermometer) pattern; anything else is a glitch and must be flagged invalid.

**Files:**
- Create: `rtl/aclk_lite/tclk_fine_decode.sv`
- Create: `tb/tclk_fine_tdc/runner.py`
- Create: `tb/tclk_fine_tdc/test_tclk_fine_decode.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `module tclk_fine_decode(input [3:0] samples, output [1:0] fine_phase, output fine_valid)`. `samples[i]` is the line at phase i (0=earliest .. 3=latest) of one 200 MHz period. Contract: `fine_valid = 1` iff `samples` is monotone non-decreasing (rising crossing) or monotone non-increasing (falling) **and** not all-equal; then `fine_phase = (leading-run length of the first sample) - 1` (0..2). Otherwise `fine_valid = 0`, `fine_phase = 0`.

- [ ] **Step 1: Write the failing test (all 16 patterns, expected computed from the rule)**

```python
# tb/tclk_fine_tdc/test_tclk_fine_decode.py
import cocotb
from cocotb.triggers import Timer


def _expect(s):
    """Reference model of the decoder contract for a 4-bit sample vector s[0..3]."""
    bits = [(s >> i) & 1 for i in range(4)]           # bits[0]=phase0 (earliest)
    if len(set(bits)) == 1:                            # all-equal: no edge
        return (0, 0)
    rising = bits == sorted(bits)                      # 0..0 1..1
    falling = bits == sorted(bits, reverse=True)       # 1..1 0..0
    if not (rising or falling):
        return (0, 0)                                  # non-monotone glitch
    run = 1
    while run < 4 and bits[run] == bits[0]:
        run += 1
    return (run - 1, 1)


@cocotb.test()
async def test_decode_truth_table(dut):
    for s in range(16):
        dut.samples.value = s
        await Timer(1, unit="ns")
        exp_phase, exp_valid = _expect(s)
        got_valid = int(dut.fine_valid.value)
        assert got_valid == exp_valid, f"s={s:04b}: fine_valid {got_valid} != {exp_valid}"
        if exp_valid:
            got_phase = int(dut.fine_phase.value)
            assert got_phase == exp_phase, f"s={s:04b}: fine_phase {got_phase} != {exp_phase}"
```

```python
# tb/tclk_fine_tdc/runner.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_tclk_fine_decode():
    run_cocotb("tclk_fine_tdc",
               sources=["rtl/aclk_lite/tclk_fine_decode.sv"],
               hdl_toplevel="tclk_fine_decode",
               test_module="test_tclk_fine_decode")


if __name__ == "__main__":
    test_tclk_fine_decode()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tb/tclk_fine_tdc/runner.py::test_tclk_fine_decode -s`
Expected: FAIL (module `tclk_fine_decode` not found / elaboration error).

- [ ] **Step 3: Implement the decoder**

```systemverilog
// rtl/aclk_lite/tclk_fine_decode.sv
// Thermometer decode of four phase-ordered line samples -> sub-bin + validity.
// samples[i] is the line at phase i (0 = earliest .. 3 = latest) of one 200 MHz
// period. A clean crossing is monotone; fine_phase is the leading-run length - 1.
// Any non-monotone (glitch) pattern raises fine_valid = 0 (graceful fallback).
`default_nettype none
module tclk_fine_decode (
    input  wire [3:0] samples,
    output reg  [1:0] fine_phase,
    output reg        fine_valid
);
    // leading-run length of samples[0] within samples[0..3]
    reg [2:0] run;
    always @(*) begin
        run = 3'd1;
        if (samples[1] == samples[0]) begin
            run = 3'd2;
            if (samples[2] == samples[0]) begin
                run = 3'd3;
                if (samples[3] == samples[0]) run = 3'd4;
            end
        end
    end

    // monotone-and-not-all-equal == exactly the six thermometer codes
    // (bit order {samples[3],samples[2],samples[1],samples[0]}).
    wire monotone =
        (samples == 4'b0001) | (samples == 4'b0011) | (samples == 4'b0111) |
        (samples == 4'b1110) | (samples == 4'b1100) | (samples == 4'b1000);

    always @(*) begin
        if (monotone) begin
            fine_valid = 1'b1;
            fine_phase = run[1:0] - 2'd1;      // run in 1..3 -> phase 0..2
        end else begin
            fine_valid = 1'b0;
            fine_phase = 2'd0;
        end
    end
endmodule
`default_nettype wire
```

Note: `samples == 4'bXXXX` uses the bit order `{samples[3],samples[2],samples[1],samples[0]}`. The six listed codes are exactly the monotone-and-not-all-equal patterns; `run` gives the phase. If the truth-table test flags a mismatch, reconcile the code list against `_expect` (the test is the contract).

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tb/tclk_fine_tdc/runner.py::test_tclk_fine_decode -s`
Expected: PASS (all 16 patterns).

- [ ] **Step 5: Commit**

```bash
git add rtl/aclk_lite/tclk_fine_decode.sv tb/tclk_fine_tdc/
git commit -m "feat(tclk): thermometer fine-phase decoder + truth-table test"
```

---

## Task 3: Multiphase sampler (`tclk_fine_tdc`) + sub-sample sweep

Wrap the decoder with the four-phase front-end: sample the raw line on four 200 MHz clocks at 0/90/180/270 degrees, 2-FF synchronize each into the 0-degree domain, form the `samples` vector, decode, and register `fine_phase`/`fine_valid` with an `edge_stb` when a decoded edge is present. Prove sub-sample resolution by sweeping a single line edge across the 5 ns period.

**Files:**
- Create: `rtl/aclk_lite/tclk_fine_tdc.sv`
- Modify: `tb/tclk_fine_tdc/runner.py` (add a second runner entry)
- Create: `tb/tclk_fine_tdc/test_tclk_fine_tdc.py` (sweep + glitch tests)

**Interfaces:**
- Consumes: `tclk_fine_decode`; `rtl/synchronizer.sv` (existing 2-FF synchronizer - confirm its port names when wiring).
- Produces: `module tclk_fine_tdc(input rstn, clk_p0, clk_p90, clk_p180, clk_p270, line, output [1:0] fine_phase, output fine_valid, output edge_stb)`. All outputs are in the `clk_p0` domain. `edge_stb` pulses one `clk_p0` cycle when the sample vector shows a crossing (monotone or glitch); `fine_phase`/`fine_valid` are valid that cycle.

- [ ] **Step 1: Write the failing sweep + glitch tests**

```python
# tb/tclk_fine_tdc/test_tclk_fine_tdc.py
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

PERIOD_PS = 5000          # 200 MHz
PHASE_PS = PERIOD_PS // 4 # 1.25 ns


async def _start_phases(dut):
    cocotb.start_soon(Clock(dut.clk_p0, PERIOD_PS, unit="ps").start())
    for sig, ph in ((dut.clk_p90, 1), (dut.clk_p180, 2), (dut.clk_p270, 3)):
        await Timer(PHASE_PS * ph, unit="ps")
        cocotb.start_soon(Clock(sig, PERIOD_PS, unit="ps").start())


@cocotb.test()
async def test_edge_sweep(dut):
    dut.line.value = 0
    dut.rstn.value = 0
    await _start_phases(dut)
    await ClockCycles(dut.clk_p0, 5)
    dut.rstn.value = 1
    await ClockCycles(dut.clk_p0, 5)

    # Align to a clk_p0 rising edge, then place a rising line edge at a controlled
    # sub-period offset; the reported fine_phase must be non-decreasing across the
    # offset sweep (finer than one 12.5 ns decode sample).
    seen = []
    for off_ps in range(200, PERIOD_PS, 400):
        dut.line.value = 0
        await RisingEdge(dut.clk_p0)
        await Timer(off_ps, unit="ps")
        dut.line.value = 1                     # the sub-sample edge
        # wait for the edge to propagate through the synchronizers + decode
        await ClockCycles(dut.clk_p0, 4)
        if int(dut.fine_valid.value):
            seen.append((off_ps, int(dut.fine_phase.value)))
        await ClockCycles(dut.clk_p0, 2)

    phases = [p for _, p in seen]
    assert len(seen) >= 3, f"too few valid captures: {seen}"
    assert phases == sorted(phases), f"fine_phase not monotone across offset sweep: {seen}"
    assert phases[0] != phases[-1], f"fine_phase did not resolve sub-sample motion: {seen}"


@cocotb.test()
async def test_glitch_flagged(dut):
    dut.line.value = 0
    dut.rstn.value = 0
    await _start_phases(dut)
    await ClockCycles(dut.clk_p0, 5)
    dut.rstn.value = 1
    await ClockCycles(dut.clk_p0, 5)

    # A narrow glitch (up then immediately down inside one period) is non-monotone.
    got_invalid = False
    await RisingEdge(dut.clk_p0)
    await Timer(PHASE_PS + 200, unit="ps")
    dut.line.value = 1
    await Timer(PHASE_PS // 2, unit="ps")       # shorter than a phase step
    dut.line.value = 0
    for _ in range(6):
        await RisingEdge(dut.clk_p0)
        await Timer(1, unit="ns")
        if int(dut.edge_stb.value) and not int(dut.fine_valid.value):
            got_invalid = True
    assert got_invalid, "glitch did not raise edge_stb with fine_valid=0"
```

```python
# add to tb/tclk_fine_tdc/runner.py
def test_tclk_fine_tdc():
    run_cocotb("tclk_fine_tdc",
               sources=["rtl/synchronizer.sv",
                        "rtl/aclk_lite/tclk_fine_decode.sv",
                        "rtl/aclk_lite/tclk_fine_tdc.sv"],
               hdl_toplevel="tclk_fine_tdc",
               test_module="test_tclk_fine_tdc")
```

Note: the two testbenches live in the same suite dir (`tb/tclk_fine_tdc/`) but use separate test modules and runner entries - `test_tclk_fine_decode` (Task 2, builds only `tclk_fine_decode.sv`) and `test_tclk_fine_tdc` (this task, builds the synchronizer + decoder + sampler). Each `run_cocotb` build lands in `sim_build/tclk_fine_tdc/`; running them sequentially rebuilds for the respective top, which is expected.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tb/tclk_fine_tdc/runner.py::test_tclk_fine_tdc -s`
Expected: FAIL (module `tclk_fine_tdc` not found).

- [ ] **Step 3: Implement the sampler**

```systemverilog
// rtl/aclk_lite/tclk_fine_tdc.sv
// Multiphase edge time-to-digital: sample the raw line on four 200 MHz clocks at
// 0/90/180/270 deg, synchronize each into the 0-deg domain, and decode the sub-bin
// of a crossing. Off the decode path; a glitch pattern yields fine_valid=0.
`default_nettype none
module tclk_fine_tdc (
    input  wire       rstn,
    input  wire       clk_p0,
    input  wire       clk_p90,
    input  wire       clk_p180,
    input  wire       clk_p270,
    input  wire       line,
    output reg  [1:0]  fine_phase,
    output reg         fine_valid,
    output reg         edge_stb
);
    // First-rank capture, one FF per phase in that phase's own clock domain.
    reg s0_c, s90_c, s180_c, s270_c;
    always @(posedge clk_p0)   s0_c   <= line;
    always @(posedge clk_p90)  s90_c  <= line;
    always @(posedge clk_p180) s180_c <= line;
    always @(posedge clk_p270) s270_c <= line;

    // 2-FF synchronize the three off-phase captures into the clk_p0 domain.
    reg s90_m, s90_s, s180_m, s180_s, s270_m, s270_s, s0_s;
    always @(posedge clk_p0 or negedge rstn) begin
        if (!rstn) begin
            {s0_s, s90_m, s90_s, s180_m, s180_s, s270_m, s270_s} <= '0;
        end else begin
            s0_s   <= s0_c;
            s90_m  <= s90_c;   s90_s  <= s90_m;
            s180_m <= s180_c;  s180_s <= s180_m;
            s270_m <= s270_c;  s270_s <= s270_m;
        end
    end

    wire [3:0] samples = {s270_s, s180_s, s90_s, s0_s};  // [3]=latest .. [0]=earliest
    wire [1:0] dphase;
    wire       dvalid;
    tclk_fine_decode u_dec (.samples(samples), .fine_phase(dphase), .fine_valid(dvalid));

    // An edge is "present" this cycle iff the four samples are not all-equal.
    wire present = ~(&samples) & (|samples);

    always @(posedge clk_p0 or negedge rstn) begin
        if (!rstn) begin
            fine_phase <= 2'd0; fine_valid <= 1'b0; edge_stb <= 1'b0;
        end else begin
            edge_stb   <= present;
            fine_valid <= present & dvalid;
            fine_phase <= dphase;
        end
    end
endmodule
`default_nettype wire
```

Note: confirm `rtl/synchronizer.sv`'s interface; the inline 2-FF chains above are used instead of instantiating it to keep the per-phase timing explicit. If house style requires the shared synchronizer, swap each `_m/_s` pair for a `synchronizer` instance - functionally identical.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tb/tclk_fine_tdc/runner.py::test_tclk_fine_tdc -s`
Expected: PASS (sweep monotone + resolves motion; glitch flagged invalid).

- [ ] **Step 5: Commit**

```bash
git add rtl/aclk_lite/tclk_fine_tdc.sv tb/tclk_fine_tdc/
git commit -m "feat(tclk): 4-phase edge-TDC sampler + sub-sample sweep test"
```

---

## Task 4: Software bin-width calibration

The four phase bins are not exactly 1.25 ns each. Calibrate the `fine_phase` -> sub-ns offset table from the code density of a known-periodic marker (the phase of an asynchronous, uniformly-distributed edge sweeps all bins; each bin's occupancy is proportional to its width). Pure Python, testable without hardware.

**Files:**
- Create: `deploy/fine_calibrate.py`
- Create: `deploy/test_fine_calibrate.py`

**Interfaces:**
- Consumes: nothing (NumPy only).
- Produces: `calibrate_bins(fine_phase: np.ndarray, n_bins=4, period_ns=5.0) -> np.ndarray` returning a length-`n_bins` array of the sub-ns offset (bin center time) for each code; and `apply(coarse_ns, fine_phase, offsets) -> np.ndarray` returning refined timestamps.

- [ ] **Step 1: Write the failing test**

```python
# deploy/test_fine_calibrate.py
import numpy as np
from fine_calibrate import calibrate_bins, apply


def test_uniform_bins_recover_even_spacing():
    rng = np.random.default_rng(0)
    # asynchronous edge: fine_phase uniform over 4 equal bins
    fp = rng.integers(0, 4, size=200_000)
    off = calibrate_bins(fp, n_bins=4, period_ns=5.0)
    # equal bins -> centers at 0.625, 1.875, 3.125, 4.375 ns
    assert np.allclose(off, [0.625, 1.875, 3.125, 4.375], atol=0.05)


def test_nonuniform_bins_recover_widths():
    # bin 0 twice as wide as the others (occupancy 2:1:1:1)
    fp = np.concatenate([np.zeros(4000), np.ones(2000), np.full(2000, 2), np.full(2000, 3)]).astype(int)
    off = calibrate_bins(fp, n_bins=4, period_ns=5.0)
    assert off[0] < off[1] < off[2] < off[3]
    assert abs(off[0] - 1.0) < 0.1          # wide first bin -> center ~1.0 ns


def test_apply_refines_within_coarse_tick():
    off = np.array([0.625, 1.875, 3.125, 4.375])
    coarse = np.array([100.0, 100.0])
    ref = apply(coarse, np.array([0, 3]), off)
    assert ref[1] - ref[0] > 3.0            # bin 3 later than bin 0 within the tick
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest deploy/test_fine_calibrate.py -q`
Expected: FAIL (`No module named 'fine_calibrate'`).

- [ ] **Step 3: Implement the calibration**

```python
# deploy/fine_calibrate.py
"""Code-density calibration for the TCLK multiphase fine-timing bins.

An asynchronous line edge lands in each fine-phase bin with probability equal to
that bin's fractional width. So the histogram of fine_phase over many events
recovers the bin widths; the cumulative width gives each bin's center time. Use a
periodic, source-async marker ($02 5 s or $8F 1 Hz) whose sub-tick phase walks
uniformly. Pairs with deploy/marker_timing.py + deploy/tclk_faithfulness.py.
"""
import numpy as np


def calibrate_bins(fine_phase, n_bins=4, period_ns=5.0):
    counts = np.bincount(np.asarray(fine_phase, int), minlength=n_bins)[:n_bins]
    frac = counts / counts.sum()                 # each bin's fractional width
    edges = np.concatenate([[0.0], np.cumsum(frac)]) * period_ns
    centers = 0.5 * (edges[:-1] + edges[1:])      # bin center time, ns
    return centers


def apply(coarse_ns, fine_phase, offsets):
    return np.asarray(coarse_ns, float) + offsets[np.asarray(fine_phase, int)]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest deploy/test_fine_calibrate.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add deploy/fine_calibrate.py deploy/test_fine_calibrate.py
git commit -m "feat(analysis): code-density calibration for the fine-timing bins"
```

---

## Self-Review

- **Spec coverage (Part 1 scope):** Block 2 multiphase sampler -> Task 3. Thermometer + `fine_valid` glitch rule (Section 3) -> Tasks 2, 3. Software calibration (Section 4) -> Task 4. Build-order characterization step 0 -> Task 1. Validation items 1 (sweep), 3 (ringing/glitch), 4 (calibration) -> Tasks 3, 4. Deferred to Part 2 (noted, not gaps): `ref_edge` tap (Block 1), coarse-capture at ref edge / increment C, FLAGS packing (Block 3 / Section 4 data path), MMCM 4-phase clock generation on the board, chain integration + decode-preservation regression (validation item 2).
- **Placeholders:** none - every step has runnable code or an exact command.
- **Type consistency:** `tclk_fine_decode(samples[3:0]) -> (fine_phase[1:0], fine_valid)` is produced in Task 2 and consumed by `tclk_fine_tdc` in Task 3 with matching ports. `calibrate_bins`/`apply` signatures match between Task 4's impl and test. `fine_phase[1:0]`/`fine_valid` encoding matches the Global Constraints and the spec's `FLAGS[3:2]`/`FLAGS[4]` (wired in Part 2).

## Part 2 (follow-on, after Task 1's characterization)

Integration, written once the dither behavior is measured: `ref_edge` frame-detection tap in `TCLK_DESERIALIZER2.v`; capture coarse 200 MHz TS + `fine_phase`/`fine_valid` at the reference edge and hold to `DAVn`; pack into `FLAGS[3:2]`/`FLAGS[4]` in `tclk_readout_top.sv`; generate the four phase-shifted 200 MHz clocks in the clocking BD (MMCM CLKOUT budget); extend `tb/tclk_readout` for the fine bits and the decode-preservation regression; write the board bring-up check commands.
