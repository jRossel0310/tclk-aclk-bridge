# TCLK Sub-Sample Fine-Timing - Part 2: Readout Integration + MMCM - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Part-1 multiphase edge-TDC into the live TCLK readout so decoded events carry a jitter-free carrier-edge coarse timestamp plus 1.25 ns fine bits, and generate the four phase-shifted 200 MHz clocks in the block design - all sim-proven; bitstream synth and board validation are deferred to when the servers return.

**Architecture:** The deserializer raises a deterministic per-frame `ref_edge` strobe at frame detection. The TDC latches the shared `wr_timebase` at each carrier edge (same 200 MHz domain), and freezes `{coarse, fine_phase, fine_valid}` at `ref_edge`. `tclk_readout_top` feeds the frozen coarse as the event timestamp (`ts_ext` path) and packs the fine bits into `FLAGS[3:2]`/`[4]`; the decode/FIFO/AXI path is otherwise untouched. Software combines coarse + fine + bin calibration. The `clk_wiz` gains three phase-shifted 200 MHz outputs.

**Tech Stack:** SystemVerilog/Verilog (Icarus via the cocotb 2.0 runner), cocotb tests, Vivado block-design TCL (`clk_wiz` IP), NumPy.

## Global Constraints

- **The decode path stays bit-for-bit identical.** `serdec4_9MHz.v` and the decode FSM of `TCLK_DESERIALIZER2.v` must not change behavior; `ref_edge` is a pure tap on the existing detection condition. The decode-preservation regression (Task 3) is the gate.
- **`clk_p0` = `clk_40m` = 200 MHz.** The TDC's coarse capture and freeze logic run in `clk_40m` (same domain as `wr_timebase`); only the raw-line sampling uses the phase-shifted `clk_p90/p180/p270`. No new coarse-path CDC.
- **Fine encoding:** `FLAGS[3:2]` = `fine_phase` (2 bits, 0..3), `FLAGS[4]` = `fine_valid`; `FLAGS[1:0]` stay `is_tclk`/`has_data`. Matches Part 1 and the spec.
- **Graceful fallback / additive:** with the TDC wired in, a clean run must still decode every event with correct coarse timestamps; fine bits are advisory (`fine_valid` per event). Worst case = the shipped build.
- **Verilog-2001 for `.v` files** (`TCLK_DESERIALIZER2.v`, `TCLK_RCV.v`, `aclk_pipeline_bd_top.v`): `integer` not `int`, sized literals, no SV-only constructs.
- **Runner pattern** and **toolchain PATH** as in Part 1: tests via `tb/<name>/runner.py`; before any test run `export PATH="/c/Users/jacob/tools/oss-cad-suite/bin:/c/Users/jacob/tools/oss-cad-suite/lib:$PATH"` in Git Bash.
- **Board deferred:** bitstream synth, timing closure, and live-line validation are NOT part of this plan. The MMCM task (Task 4) is written + elaboration-checked only; Task 6 documents the board bring-up.

---

## File Structure

- Modify `rtl/aclk_bridge/TCLK_DESERIALIZER2.v` - add `REF_EDGE` output (1-cycle strobe at detection).
- Modify `rtl/aclk_bridge/TCLK_RCV.v` - thread `REF_EDGE` through (wraps serdec + deserializer).
- Modify `rtl/aclk_lite/tclk_fine_tdc.sv` - add `coarse_in[63:0]` + `ref_edge` inputs; capture coarse at each `edge_stb`; freeze `{coarse, fine_phase, fine_valid}` at `ref_edge`; new held outputs.
- Modify `rtl/aclk_lite/tclk_readout_top.sv` - add phase-clock inputs; instantiate `tclk_fine_tdc`; wire `ts_ext` as `coarse_in`; feed frozen coarse as the event timestamp; pack fine bits into FLAGS; expose `ref_edge` from `TCLK_RCV`.
- Modify `rtl/aclk_pipeline_bd_top.v` - add `clk_p90/p180/p270` inputs; thread to `tclk_readout_top`.
- Modify `vivado/build_aclk_pipeline.tcl` - add three 200 MHz phase-shifted `clk_wiz_0` outputs; connect to `u_pipeline`.
- Modify `deploy/fine_calibrate.py` + `deploy/test_fine_calibrate.py` - add `refine(coarse_ns, fine_phase, fine_valid, offsets)` end-to-end.
- Tests: extend `tb/tclk_fine_tdc/` (coarse-capture/freeze unit test) and `tb/tclk_readout/` (chain: fine bits in FLAGS + ref-edge coarse + refined spacing; decode-preservation regression).
- Create `deploy/tclk_fine_timing_bringup.md` - board bring-up checks (Task 6).

---

## Task 1: `ref_edge` tap in the deserializer + TCLK_RCV threading

Emit a deterministic one-cycle `clk_40m` strobe at the exact cycle the deserializer accepts a frame, and route it out through `TCLK_RCV`. No decode-behavior change.

**Files:**
- Modify: `rtl/aclk_bridge/TCLK_DESERIALIZER2.v`
- Modify: `rtl/aclk_bridge/TCLK_RCV.v`
- Create: `tb/tclk_refedge/runner.py`, `tb/tclk_refedge/test_tclk_refedge.py` (or extend `tb/tclk_rcv/` - implementer's choice, but keep it a focused suite)

**Interfaces:**
- Produces: `TCLK_DESERIALIZER2` gains `output reg REF_EDGE` - one `CLK_40M` cycle high on the same `SCLK_posedge` where the FSM asserts `DAVn_int=0` (frame accepted, `data_reg[10:8]==110` & `parity_reg==parity_calc`). `TCLK_RCV` gains `output wire REF_EDGE` passed through from the deserializer instance.
- Consumes: nothing new.

- [ ] **Step 1: Read the current files** `rtl/aclk_bridge/TCLK_DESERIALIZER2.v` (the FSM state 4'h0 detection at lines ~117-129, and the `DAVn` generation) and `rtl/aclk_bridge/TCLK_RCV.v` (how it instantiates the deserializer and names ports).

- [ ] **Step 2: Write the failing test**

Drive a few good frames (reuse `tclk_tx_model`), and assert `REF_EDGE` pulses exactly once per accepted frame, on the same `clk_40m` cycle as the existing `~DAVn` strobe (they are the same detection event; `DAVn` is registered one cycle later, so accept either "same cycle as DAVn_int" or "one cycle before DAVn" - pin the exact relationship you observe and assert it). A bad-parity frame must NOT pulse `REF_EDGE`.

```python
# tb/tclk_refedge/test_tclk_refedge.py  (sketch - implementer completes against the DUT it builds)
# Count REF_EDGE pulses over a stream of N good frames + 1 bad-parity frame;
# assert count == N, and that each REF_EDGE coincides with the frame-accept cycle
# (same cycle DAVn asserts low, or exactly one cycle before - whichever the RTL yields).
```

Choose the DUT top for this suite: simplest is `TCLK_RCV` (drives the real serdec+deserializer), asserting on its new `REF_EDGE` and existing `DAVn`.

- [ ] **Step 3: Run test to verify it fails** (`REF_EDGE` not a port yet -> elaboration error).

- [ ] **Step 4: Implement**

In `TCLK_DESERIALIZER2.v`: add `output reg REF_EDGE`. In the FSM combinational block, the frame-accept path already sets `DAVn_int = 1'b0`. Register a one-cycle strobe with the same one-cycle shape the existing `DAVn` uses: `REF_EDGE <= ~DAVn_int & SCLK_posedge;` in a `CLK_40M`/`RESETn` always block (mirrors the `DAVn <= DAVn_int | ~SCLK_posedge;` timing so `REF_EDGE` is high exactly when a frame is accepted on an `SCLK_posedge`). Reset `REF_EDGE <= 1'b0`. Do NOT touch the FSM, shift register, parity, or `DAVn` logic. In `TCLK_RCV.v`: add `output wire REF_EDGE` and connect the deserializer instance's `REF_EDGE` to it.

- [ ] **Step 5: Run test to verify it passes**, and run the existing `tb/tclk_rcv` and `tb/tclk_readout` suites to confirm decode is unchanged.

- [ ] **Step 6: Commit**

```bash
git add rtl/aclk_bridge/TCLK_DESERIALIZER2.v rtl/aclk_bridge/TCLK_RCV.v tb/tclk_refedge/
git commit -m "feat(tclk): ref_edge frame-detection strobe out of the deserializer + TCLK_RCV"
```

---

## Task 2: TDC coarse-capture + freeze

Extend `tclk_fine_tdc` to timestamp each carrier edge with the shared coarse timebase and freeze the reference-edge timing when `ref_edge` fires.

**Files:**
- Modify: `rtl/aclk_lite/tclk_fine_tdc.sv`
- Modify: `tb/tclk_fine_tdc/runner.py`, `tb/tclk_fine_tdc/test_tclk_fine_tdc.py` (add a capture/freeze test alongside the existing sweep/glitch tests)

**Interfaces:**
- Produces: `tclk_fine_tdc` gains `input [63:0] coarse_in` (the `wr_timebase`, in `clk_p0`=`clk_40m`), `input ref_edge` (a `clk_40m`-domain strobe; sync it into `clk_p0` with 2 FF). New outputs `output reg [63:0] frozen_coarse`, `output reg [1:0] frozen_phase`, `output reg frozen_valid` - the held reference-edge timing, stable after `ref_edge` until the next `ref_edge`. The existing `fine_phase`/`fine_valid`/`edge_stb` outputs stay.
- Consumes: the Part-1 sampler/decoder internals (`edge_stb`, `fine_phase`, `fine_valid`).

- [ ] **Step 1: Write the failing test**

Drive the sampler as in the sweep test (4 phase clocks, a periodic line edge), plus a free-running `coarse_in` counter incremented on `clk_p0`, and pulse `ref_edge` once. Assert: after `ref_edge`, `frozen_coarse` equals the `coarse_in` value captured at the most recent carrier `edge_stb` before the (synced) `ref_edge` - i.e. a real carrier-edge coarse value, NOT the `coarse_in` at the `ref_edge` instant - and `frozen_phase`/`frozen_valid` equal that edge's fine decode. Pulse `ref_edge` again after another edge; assert the frozen values update to the new edge. (The key property: `frozen_coarse` tracks carrier edges, and is immune to exactly WHEN `ref_edge` lands within a carrier period.)

- [ ] **Step 2: Run to verify it fails** (new ports absent).

- [ ] **Step 3: Implement**

In `clk_p0`: on every `edge_stb` (a decoded carrier edge), latch `edge_coarse <= coarse_in;` `edge_phase <= fine_phase;` `edge_valid <= fine_valid;` (the "held last carrier edge" registers). 2-FF synchronize `ref_edge` into `clk_p0` (`ref_m/ref_s`) and edge-detect it (`ref_s & ~ref_s_d`). On that synced `ref_edge` pulse, freeze: `frozen_coarse <= edge_coarse; frozen_phase <= edge_phase; frozen_valid <= edge_valid;`. Async-reset all new registers. Note: the boundary-quarter bin (bin 0) resolves one `clk_p0` cycle later than bins 1-3 (Part-1 extension caveat) - `edge_coarse`/`edge_phase` update together on `edge_stb`, so they stay consistent; verify the freeze picks up the matching pair.

- [ ] **Step 4: Run to verify it passes** + the existing `test_tclk_fine_decode`/`test_tclk_fine_tdc` still green.

- [ ] **Step 5: Commit**

```bash
git add rtl/aclk_lite/tclk_fine_tdc.sv tb/tclk_fine_tdc/
git commit -m "feat(tclk): TDC carrier-edge coarse capture + ref_edge freeze"
```

---

## Task 3: Readout integration + decode-preservation regression

Wire the TDC into `tclk_readout_top`: frozen coarse becomes the event timestamp, fine bits go into FLAGS, and the proven decode path is shown unchanged.

**Files:**
- Modify: `rtl/aclk_lite/tclk_readout_top.sv`
- Modify: `tb/tclk_readout/test_tclk_readout.py` (add a fine-bits chain test), `tb/tclk_readout/runner.py` (thread the new phase-clock ports + a runner entry for the new test module)

**Interfaces:**
- `tclk_readout_top` gains `input logic clk_p90, clk_p180, clk_p270` (200 MHz phase clocks; `clk_p0` reuses the existing `clk_40m`). It exposes the `TCLK_RCV` `REF_EDGE`, instantiates `tclk_fine_tdc` (`.clk_p0(clk_40m), .clk_p90, .clk_p180, .clk_p270, .line(tclk), .coarse_in(ts_ext), .ref_edge(REF_EDGE)`), and:
  - feeds `frozen_coarse` as the event timestamp into the readout core's `ts_ext` input (so the packed TS is the ref-edge carrier coarse, not the free-running counter). Set/confirm `USE_EXT_TS=1` for this wiring.
  - sets `flags = {11'b0, frozen_valid, frozen_phase, is_tclk, has_data}` -> `FLAGS[4]=frozen_valid`, `FLAGS[3:2]=frozen_phase`, `FLAGS[1]=is_tclk`, `FLAGS[0]=has_data`.
- Consumes: Task 1 `REF_EDGE`, Task 2 `tclk_fine_tdc` frozen outputs.

- [ ] **Step 1: Read** `rtl/aclk_lite/tclk_readout_top.sv` fully - how it builds `flags` (currently `16'h0002`), how `ts_ext`/`USE_EXT_TS` flows into the readout core, and how the AXI EVENT register returns `{FLAGS, EVENT}`.

- [ ] **Step 2: Write the failing chain test**

Extend `tb/tclk_readout` (a new test module `test_tclk_fine_chain`): start the four phase clocks (`clk_40m` as p0 plus p90/p180/p270 at 200 MHz, phases 90/180/270 as in the Part-1 sweep test) and drive `ts_ext` as a free-running 200 MHz counter. Drive a stream of events; read them over AXI. Assert:
- every event still decodes in order, `is_tclk=1`, `EVENT_COUNT` exact, no new `ERROR_COUNT` (decode-preservation);
- each event's `FLAGS[4]` (`fine_valid`) and `FLAGS[3:2]` (`fine_phase`) are present and, for clean frames, `fine_valid=1` with `fine_phase in 0..3`;
- the event TS equals a real carrier-edge `ts_ext` value at the reference edge (monotone increasing, and consistent with the ref-edge timing, not the DAVn-latched counter).

Keep the existing `test_tclk_readout` / parity / debug tests runnable with the new ports wired (they exercise decode-preservation directly).

- [ ] **Step 3: Run to verify it fails** (new ports / FLAGS bits absent).

- [ ] **Step 4: Implement** the wiring per the Interfaces block. Do not alter the decode, FIFO, PERR, or AXI logic - only add the TDC instance, the timestamp source swap, and the FLAGS bits.

- [ ] **Step 5: Run to verify it passes** - the new chain test AND every existing `tb/tclk_readout` test (decode-preservation is the gate).

- [ ] **Step 6: Commit**

```bash
git add rtl/aclk_lite/tclk_readout_top.sv tb/tclk_readout/
git commit -m "feat(tclk): wire fine-TDC into readout - ref-edge coarse TS + fine bits in FLAGS"
```

---

## Task 4: MMCM four-phase clocks (block design) + BD-top threading

Generate `clk_p90/p180/p270` (200 MHz, 90/180/270 deg) in the `clk_wiz` and thread them to `tclk_readout_top` through the pipeline BD top. Written + elaboration-checked; synth/board deferred.

**Files:**
- Modify: `vivado/build_aclk_pipeline.tcl`
- Modify: `rtl/aclk_pipeline_bd_top.v`

**Interfaces:**
- `aclk_pipeline_bd_top` gains `input wire clk_p90, clk_p180, clk_p270`, threaded to the `tclk_readout_top` instance's new inputs (`clk_p0` = existing `clk_40m`).
- `clk_wiz_0` gains `CLKOUT3/4/5` at 200 MHz with phases 90/180/270; connected to `u_pipeline/clk_p90|p180|p270`.

- [ ] **Step 1: Read** `vivado/build_aclk_pipeline.tcl` around the `clk_wiz_0` config (lines ~158-172) and `rtl/aclk_pipeline_bd_top.v` port list + the `tclk_readout_top` instantiation.

- [ ] **Step 2: Add the phase outputs to `clk_wiz_0`** in the TCL `set_property -dict` list:

```tcl
CONFIG.CLKOUT3_USED {true} CONFIG.CLKOUT3_REQUESTED_OUT_FREQ {200.000} CONFIG.CLKOUT3_REQUESTED_PHASE {90.000} \
CONFIG.CLKOUT4_USED {true} CONFIG.CLKOUT4_REQUESTED_OUT_FREQ {200.000} CONFIG.CLKOUT4_REQUESTED_PHASE {180.000} \
CONFIG.CLKOUT5_USED {true} CONFIG.CLKOUT5_REQUESTED_OUT_FREQ {200.000} CONFIG.CLKOUT5_REQUESTED_PHASE {270.000} \
```

and connect them:

```tcl
connect_bd_net [get_bd_pins clk_wiz_0/clk_out3] [get_bd_pins u_pipeline/clk_p90]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out4] [get_bd_pins u_pipeline/clk_p180]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out5] [get_bd_pins u_pipeline/clk_p270]
```

Add a comment: 6 of 7 MMCM outputs used (80, 200x4-phase); the 2:1 XDC async-clock-group wildcard (`clk_out*clk_wiz*`) still covers the same-frequency phase outputs, so no XDC change.

- [ ] **Step 3: Thread the ports in `aclk_pipeline_bd_top.v`** (add the three inputs, connect to the `tclk_readout_top` instance's `clk_p90/p180/p270`; `clk_p0` = `clk_40m`).

- [ ] **Step 4: Elaboration check** (sim-buildable surface only): run a cocotb elaboration/build of `tclk_readout_top` with the new ports through the existing `tb/tclk_readout` runner (already updated in Task 3) to confirm the RTL threading elaborates. The TCL itself cannot be validated without Vivado - visually verify it against the existing `clk_out1/2` pattern and note in the commit that synth is deferred.

- [ ] **Step 5: Commit**

```bash
git add vivado/build_aclk_pipeline.tcl rtl/aclk_pipeline_bd_top.v
git commit -m "feat(pipeline): clk_wiz 200 MHz 90/180/270 phase clocks for the fine-TDC (synth deferred)"
```

---

## Task 5: Software end-to-end refine

Combine the captured coarse timestamp with the fine bits and bin calibration into a refined timestamp.

**Files:**
- Modify: `deploy/fine_calibrate.py`, `deploy/test_fine_calibrate.py`

**Interfaces:**
- Produces: `refine(coarse_ns, fine_phase, fine_valid, offsets) -> np.ndarray` - returns `coarse_ns + offsets[fine_phase]` where `fine_valid`, else `coarse_ns` unchanged (fallback). Keep the existing `calibrate_bins`/`apply`.

- [ ] **Step 1: Write the failing test**

```python
# add to deploy/test_fine_calibrate.py
import numpy as np
from fine_calibrate import calibrate_bins, refine

def test_refine_applies_only_when_valid():
    off = np.array([0.625, 1.875, 3.125, 4.375])
    coarse = np.array([100.0, 100.0, 100.0])
    fp = np.array([0, 3, 2])
    fv = np.array([1, 1, 0])                      # 3rd event: fine invalid -> fallback
    out = refine(coarse, fp, fv, off)
    assert np.isclose(out[0], 100.625)
    assert np.isclose(out[1], 104.375)
    assert np.isclose(out[2], 100.0)             # unchanged: coarse only

def test_refine_tightens_periodic_spacing():
    # a periodic marker: true period exactly 5000.0 ns; coarse is quantized to 5 ns,
    # fine recovers the sub-tick, so refined spacing std < coarse spacing std.
    n = 500
    true_t = np.arange(n) * 5000.37             # sub-tick drift
    coarse = np.round(true_t / 5.0) * 5.0        # 5 ns quantized
    fp = ((true_t % 5.0) / 1.25).astype(int) % 4
    off = calibrate_bins(fp, n_bins=4, period_ns=5.0)
    ref = refine(coarse, fp, np.ones(n, int), off)
    assert np.std(np.diff(ref)) < np.std(np.diff(coarse))
```

- [ ] **Step 2: Run to verify it fails** (`refine` undefined).

- [ ] **Step 3: Implement**

```python
# add to deploy/fine_calibrate.py
def refine(coarse_ns, fine_phase, fine_valid, offsets):
    """Refined timestamp: coarse + calibrated sub-bin offset where fine_valid,
    else coarse alone (graceful fallback). Sign is + per the standalone convention;
    Part-2 integration pins it against the coarse-latch edge (spec: coarse - offset)."""
    coarse_ns = np.asarray(coarse_ns, float)
    out = coarse_ns.copy()
    v = np.asarray(fine_valid, bool)
    fp = np.asarray(fine_phase, int)
    out[v] = coarse_ns[v] + offsets[fp[v]]
    return out
```

- [ ] **Step 4: Run to verify it passes** (all `deploy/test_fine_calibrate.py`).

- [ ] **Step 5: Commit**

```bash
git add deploy/fine_calibrate.py deploy/test_fine_calibrate.py
git commit -m "feat(analysis): end-to-end fine-timing refine (coarse + calibrated sub-bin)"
```

---

## Task 6: Board bring-up checklist (deferred validation)

Document the exact steps to validate on the real line when the servers return. No code.

**Files:**
- Create: `deploy/tclk_fine_timing_bringup.md`

- [ ] **Step 1: Write the bring-up doc** covering, in order: (1) build the bitstream (`vivado/build_aclk_pipeline.tcl`) and confirm timing closes with the 6-output `clk_wiz` (WNS >= 0); (2) flash, confirm decode unchanged vs baseline (`ERROR_COUNT` delta 0 over a warm-up, `EVENT_COUNT` climbing); (3) capture `$02`/`$8F` with the fine bits, confirm `fine_valid` stays high on the live line (a low valid rate means the real-line ringing is defeating the sub-bin decode - the documented risk); (4) run `deploy/fine_calibrate.py` code-density calibration on the captured `fine_phase` to recover the bin offsets, then `refine`; (5) compare event-to-event jitter (via `deploy/marker_timing.py`) refined vs coarse - success = tightened toward ~1.25 ns. State the fallback: if `fine_valid` collapses on the real line, the coarse path is exactly the shipped build.

- [ ] **Step 2: Commit**

```bash
git add deploy/tclk_fine_timing_bringup.md
git commit -m "docs(deploy): TCLK fine-timing board bring-up checklist (deferred validation)"
```

---

## Self-Review

- **Spec coverage:** Block 1 `ref_edge` tap -> Task 1. Block 2 coarse capture + `ref_edge` freeze -> Task 2. Block 3 merge / edge-selection / FLAGS packing / ts source -> Tasks 2-3. MMCM 4-phase (Section: Block 2 clocking) -> Task 4. Software refine (Software calibration) -> Task 5. Board bring-up -> Task 6. Decode-preservation regression (validation item 2) -> Task 3.
- **Placeholders:** the two RTL-modification tasks (1, 3) point the implementer to read the exact current file + give the exact signal additions and test contracts rather than a full verbatim file rewrite - appropriate for editing large existing files, not a placeholder. Every step names its files, its assertions, and its command.
- **Type consistency:** `REF_EDGE` produced by Task 1 (deserializer/TCLK_RCV) is consumed by Task 3 (readout top -> TDC `ref_edge`). `frozen_coarse[63:0]`/`frozen_phase[1:0]`/`frozen_valid` produced by Task 2 are consumed by Task 3 (ts source + FLAGS). `clk_p90/p180/p270` added in Task 3's `tclk_readout_top`, threaded in Task 4's BD top, generated in Task 4's TCL. `refine(coarse_ns, fine_phase, fine_valid, offsets)` signature matches between Task 5's impl and test. `FLAGS[3:2]`/`FLAGS[4]` encoding matches the Global Constraints and Part 1.

## Deferred to the board (not in this plan)
Bitstream synth + timing closure with the 6-output `clk_wiz`; live-line confirmation that `fine_valid` holds and jitter tightens toward 1.25 ns. Covered by Task 6's checklist.
