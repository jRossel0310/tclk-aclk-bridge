# TCLK 5 ns Clock Upgrade + ACLK Rate-Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a deployable `aclk_pipeline` bitstream that timestamps TCLK at 5 ns (clk_40m=200 MHz / clk_80m=400 MHz) and hardens the ACLK readout for higher event density, proven in cocotb sim before the board.

**Architecture:** The TCLK serdec is parameterized by an oversample ratio `OSR` (default 8, reproducing today; 40 for 400 MHz) threaded through `TCLK_RCV` and `tclk_readout_top`; the TCLK `wr_timebase` constants and the `clk_wiz` output frequencies are updated for 200/400 MHz; the ACLK readout FIFO is deepened 512->2048. The cocotb harness (already parameterized by `SAMPLES_PER_CELL`) validates OSR=8 bit-identically and OSR=40 end to end.

**Tech Stack:** SystemVerilog/Verilog RTL, cocotb 2.0 + Icarus (`.\sim.ps1 run -Module <name>`), Vivado 2024.2 block design built via `.\hw.ps1 build`, Python 3.12 deploy tooling.

## Global Constraints

- Target: `clk_40m` = 200 MHz (CLKOUT2), `clk_80m` = 400 MHz (CLKOUT1), 2:1 ratio preserved. Resolution goal 5 ns.
- `OSR` defaults to **8** everywhere; only `aclk_pipeline` instantiates `OSR=40`. Every other build and consumer must stay bit-identical on decode.
- ACLK path (rx_usrclk2, GT-locked) is NOT retimed; ACLK resolution stays 16 ns. Part B is FIFO depth + a drain-ceiling measurement only.
- Decode correctness is the acceptance bar: known event codes decode in order, bad parity raises PERR / one ERROR_COUNT and yields no event, timestamps strictly increasing, EVENT_COUNT exact, no false overflow.
- `sig_err` is diagnostic only (decode FSM never reads it); it may be reformulated/pipelined freely.
- Exact TCLK `wr_timebase` constants at 200 MHz: `CLK_PERIOD_DS=50`, `CLK10_TIMEOUT=80`, `PPS_TIMEOUT=220_000_000`.
- Signal names `clk_80m`/`clk_40m` are kept (generic domain labels; they carry 400/200 here).
- Run sims from the repo root: `.\sim.ps1 run -Module <name>` (PowerShell) or `./sim.sh run <name>` (bash). Set overrides with `$env:VAR="..."` before the call.
- Branch: `tclk-clock-upgrade` (already created).

---

## Task 1: Deepen the ACLK readout FIFO to 2048 (Part B)

**Files:**
- Modify: `rtl/aclk_pipeline_bd_top.v` (the `aclk_gt_readout_top` instantiation, `ADDR_WIDTH (9)` near line 535)
- Modify: `tb/async_fifo/test_async_fifo.py` (make `DEPTH` env-driven)
- Modify: `tb/async_fifo/runner.py` (pass `ADDR_WIDTH` parameter from env)

**Interfaces:**
- Consumes: `async_fifo` (`WIDTH`, `ADDR_WIDTH`; `DEPTH = 2**ADDR_WIDTH`; sticky `overflow`).
- Produces: a proven-at-depth-2048 ACLK FIFO. No new signatures.

- [ ] **Step 1: Make the async_fifo test depth configurable**

In `tb/async_fifo/test_async_fifo.py`, replace the hardcoded depth block (lines ~26-29):

```python
import os
# RTL default is ADDR_WIDTH=6 -> DEPTH=64; override with FIFO_ADDR_WIDTH.
WIDTH = 96
ADDR_WIDTH = int(os.getenv("FIFO_ADDR_WIDTH", "6"))
DEPTH = 1 << ADDR_WIDTH
MASK = (1 << WIDTH) - 1
```

- [ ] **Step 2: Pass the parameter from the runner**

In `tb/async_fifo/runner.py`, change the `run_cocotb(...)` call to forward the override:

```python
import os
run_cocotb(
    "async_fifo",
    sources=["rtl/synchronizer.sv", "rtl/async_fifo.sv"],
    hdl_toplevel="async_fifo",
    parameters={"ADDR_WIDTH": int(os.getenv("FIFO_ADDR_WIDTH", "6"))},
)
```

(Keep the existing `sources`/`hdl_toplevel` exactly as they already are if they differ; only add the `parameters=` argument.)

- [ ] **Step 3: Regression at the default depth**

Run: `.\sim.ps1 run -Module async_fifo`
Expected: PASS - `test_integrity_under_backpressure` and `test_overflow_latches_and_keeps_first_words` both pass at DEPTH=64 (behavior unchanged).

- [ ] **Step 4: Prove the FIFO at depth 2048**

Run: `$env:FIFO_ADDR_WIDTH=11; .\sim.ps1 run -Module async_fifo; Remove-Item Env:\FIFO_ADDR_WIDTH`
Expected: PASS - both tests pass at DEPTH=2048 (overflow latches after the 2048th word, first 2048 survive, integrity holds). This is slower (thousands of words); allow a few minutes.

- [ ] **Step 5: Deepen the ACLK FIFO in the pipeline**

In `rtl/aclk_pipeline_bd_top.v`, the `aclk_gt_readout_top` instantiation, change ONLY the ACLK readout's depth:

```verilog
    aclk_gt_readout_top #(
        .ADDR_WIDTH (11),
        .AXI_ADDR_W (8),
        .USE_EXT_TS (1'b1)
```

Leave the `tclk_readout_top` instantiation at `.ADDR_WIDTH (9)` unchanged.

- [ ] **Step 6: Commit**

```bash
git add tb/async_fifo/test_async_fifo.py tb/async_fifo/runner.py rtl/aclk_pipeline_bd_top.v
git commit -m "feat(pipeline): deepen ACLK readout FIFO 512->2048 for burst headroom"
```

---

## Task 2: ACLK drain-ceiling benchmark + operator procedure (Part B)

A deeper FIFO only buys burst time; sustained rate is bounded by the PS drain. This task adds a benchmark that measures raw drain throughput and documents the operator procedure. The board run is an operator follow-up; the script's rate math is unit-tested locally with a fake register interface.

**Files:**
- Create: `deploy/bench_drain.py`
- Create: `deploy/test_bench_drain.py`
- Modify: `deploy/capture.md` (add an "ACLK drain ceiling" operator section)

**Interfaces:**
- Produces: `measure_rate(reader, seconds, now) -> dict` with keys `reads`, `elapsed_s`, `reads_per_s`, `overflow`. `reader` exposes `.drain_once() -> int` (events popped this call) and `.overflow() -> bool`.

- [ ] **Step 1: Write the failing test for the rate math**

Create `deploy/test_bench_drain.py`:

```python
from bench_drain import measure_rate


class FakeReader:
    def __init__(self, per_call, overflow_at=None):
        self.per_call = per_call
        self.calls = 0
        self.overflow_at = overflow_at
    def drain_once(self):
        self.calls += 1
        return self.per_call
    def overflow(self):
        return self.overflow_at is not None and self.calls >= self.overflow_at


def test_rate_and_overflow():
    # fake clock: 0.0, then 0.5s per drain_once, stop once elapsed >= 1.0s
    ticks = iter([0.0, 0.5, 1.0, 1.0])
    r = FakeReader(per_call=10, overflow_at=2)
    out = measure_rate(r, seconds=1.0, now=lambda: next(ticks))
    assert out["reads"] == 20            # two drain_once calls * 10
    assert out["elapsed_s"] == 1.0
    assert out["reads_per_s"] == 20.0
    assert out["overflow"] is True
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `deploy\.venv\Scripts\python -m pytest deploy/test_bench_drain.py -v` (or the repo's usual `python -m pytest`)
Expected: FAIL with `ModuleNotFoundError: No module named 'bench_drain'`.

- [ ] **Step 3: Implement the benchmark**

Create `deploy/bench_drain.py`:

```python
#!/usr/bin/env python3
"""Measure the sustained drain ceiling of a readout UIO: how many events per
second the PS can pop, and whether the sticky overflow bit sets under load.

On the board:  sudo python3 bench_drain.py /dev/uio5 --seconds 10   # aclk
The FIFO must be actively fed (run the generator / live line) during the run.
"""
import argparse
import time


def measure_rate(reader, seconds, now=time.monotonic):
    """Drain in a tight loop for `seconds`, return throughput + overflow.

    reader.drain_once() pops all currently-buffered events and returns the count;
    reader.overflow() reports the sticky hardware overflow bit.
    """
    t0 = now()
    reads = 0
    overflow = False
    while True:
        reads += reader.drain_once()
        overflow = overflow or reader.overflow()
        elapsed = now() - t0
        if elapsed >= seconds:
            break
    return {
        "reads": reads,
        "elapsed_s": elapsed,
        "reads_per_s": reads / elapsed if elapsed else 0.0,
        "overflow": overflow,
    }


def _main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dev")
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args(argv)
    from readout_common import RegIO           # board-only import
    io = RegIO(args.dev)

    class _R:
        def drain_once(self):
            n = 0
            while not (io.rd(0x00) & 0x1):      # STATUS bit0 = empty
                io.rd(0x10); io.rd(0x40); io.rd(0x50)
                io.pulse(0x60)                  # single-store POP (see readout_common)
                n += 1
            return n
        def overflow(self):
            return bool(io.rd(0x00) & 0x2)      # STATUS bit1 = sticky overflow

    out = measure_rate(_R(), args.seconds)
    print(f"drain: {out['reads']} events in {out['elapsed_s']:.2f}s = "
          f"{out['reads_per_s']:.0f}/s  overflow={out['overflow']}")


if __name__ == "__main__":
    _main()
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `deploy\.venv\Scripts\python -m pytest deploy/test_bench_drain.py -v`
Expected: PASS.

- [ ] **Step 5: Document the operator procedure**

Append to `deploy/capture.md`:

```markdown
## ACLK drain ceiling (rate-hardening check)

With the ACLK line (or the loopback generator) actively producing events:

    sudo python3 bench_drain.py /dev/uio5 --seconds 10

Report is `<events> in <s>s = <rate>/s overflow=<bool>`. Interpretation:
- overflow=False and rate >= your target sustained ACLK rate: the path keeps up.
- overflow=True: events are being dropped; the 2048-deep FIFO absorbed the burst
  but the sustained rate exceeds the drain ceiling. The lever is drain speed
  (software), not more FIFO depth.
```

- [ ] **Step 6: Commit**

```bash
git add deploy/bench_drain.py deploy/test_bench_drain.py deploy/capture.md
git commit -m "feat(deploy): ACLK drain-ceiling benchmark + operator procedure"
```

---

## Task 3: Parameterize the serdec by oversample ratio OSR (Part A)

Rewrite `rtl/aclk_bridge/serdec4_9MHz.v` so every TCLK-bit-cell-referenced constant derives from `OSR` (CLK_80M samples per 100 ns cell). Default `OSR=8` reproduces the original taps exactly (bit-identical decode). The decode FSM and data/SCLK/SDATA logic are unchanged.

**Files:**
- Modify: `rtl/aclk_bridge/serdec4_9MHz.v` (full parameterization)

**Interfaces:**
- Produces: `serdec4_9MHz #(parameter int OSR = 8) (...)` - same ports as today.
- Consumed by: `TCLK_RCV` (Task 4).

- [ ] **Step 1: Confirm the OSR=8 regression baseline is green first**

Run: `.\sim.ps1 run -Module tclk_rcv` then `.\sim.ps1 run -Module tclk_readout`
Expected: PASS (this is the pre-change baseline; both must be green before touching the file).

- [ ] **Step 2: Rewrite the serdec parameterized by OSR**

Replace `rtl/aclk_bridge/serdec4_9MHz.v` in full:

```verilog
// ------------------------------------------------------------
// serdec4_9MHz.v  (parameterized by OSR = CLK_80M / 10 MHz oversample ratio)
// Default OSR=8 reproduces the original 80 MHz taps bit-for-bit on decode.
// OSR=40 supports the 400 MHz oversample of the 5 ns TCLK build.
// ------------------------------------------------------------

module serdec4_9MHz #(
    parameter int OSR = 8               // CLK_80M samples per 100 ns TCLK bit-cell
) (
    input  wire RESETn,
    input  wire CLK_80M,
    input  wire TCLK,
    input  wire RATE,

    output wire SCLK,
    output wire SDATA,
    output wire TCLK_CAR,
    output wire SIG_ERR
);
    // Widest referenced sample is ~1.5 cells back (12 at OSR=8 -> 13-bit shifter).
    localparam int DELW = (3*OSR)/2 + 1;

    reg  [7:0]  crnt_st_decode, next_st_decode;
    reg  [7:0]  crnt_st_data,   next_st_data;

    reg  [DELW-1:0] TCLK_del;

    wire TCLK_posedge;
    wire TCLK_negedge;
    reg  TCLK_del_posedge;
    reg  TCLK_del_negedge;

    reg  one_detect, zero_detect;

    reg  SCLK_int,  SCLK_set,  SCLK_clr;
    reg  SDATA_int, SDATA_set, SDATA_clr;

    reg  tclk_gate, tclk_gate_cap;

    reg        sig_err_detect;
    reg  [2:0] sig_err_stretch;

    // ---- TCLK delay shift register ----
    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)
            TCLK_del <= '0;
        else
            TCLK_del <= {TCLK_del[DELW-2:0], TCLK};
    end

    // Immediate edge (transition "now", a fixed 1-sample detection pipeline). NOTE:
    // its physical glitch-rejection window shrinks ~5x at 400 MHz; see the spec's
    // "real-line edge-noise" risk. Kept at [1]/[2] for the clean-decode build; a
    // parameterized debounce is a follow-up if bring-up shows line-noise sensitivity.
    assign TCLK_posedge =  TCLK_del[1] & ~TCLK_del[2];
    assign TCLK_negedge =  TCLK_del[2] & ~TCLK_del[1];

    // Delayed edge (~1 bit-cell back). RATE=1 (10 MHz): one cell = OSR samples.
    always @(*) begin
        if (RATE) begin // 10 MHz
            TCLK_del_posedge =  TCLK_del[OSR-1] & ~TCLK_del[OSR];
            TCLK_del_negedge =  TCLK_del[OSR]   & ~TCLK_del[OSR-1];
        end else begin
            TCLK_del_posedge =  TCLK_del[OSR]   & ~TCLK_del[OSR+1];
            TCLK_del_negedge =  TCLK_del[OSR+1] & ~TCLK_del[OSR];
        end
    end

    // ---- Decode FSM (registered state) - UNCHANGED ----
    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn) crnt_st_decode <= 8'h00;
        else         crnt_st_decode <= next_st_decode;
    end

    // ---- Decode FSM (combinational) - UNCHANGED ----
    always @(*) begin
        one_detect  = 1'b0;
        zero_detect = 1'b0;
        next_st_decode = crnt_st_decode;
        case (crnt_st_decode)
            8'h00: if (TCLK_del_posedge) next_st_decode = 8'h10;
            8'h10: begin
                if (TCLK_posedge)      begin one_detect  = 1'b1; next_st_decode = 8'h00; end
                else if (TCLK_negedge) begin zero_detect = 1'b1; next_st_decode = 8'h20; end
            end
            8'h20: if (TCLK_del_negedge) next_st_decode = 8'h30;
            8'h30: begin
                if (TCLK_negedge)      begin one_detect  = 1'b1; next_st_decode = 8'h20; end
                else if (TCLK_posedge) begin zero_detect = 1'b1; next_st_decode = 8'h00; end
            end
            default: next_st_decode = 8'h00;
        endcase
    end

    // ---- Data FSM (registered state) - UNCHANGED ----
    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn) crnt_st_data <= 8'h00;
        else         crnt_st_data <= next_st_data;
    end

    // ---- Data FSM (combinational) - UNCHANGED ----
    always @(*) begin
        SCLK_set = 1'b0; SCLK_clr = 1'b0;
        SDATA_set = 1'b0; SDATA_clr = 1'b0;
        tclk_gate_cap = 1'b0;
        next_st_data = crnt_st_data;
        case (crnt_st_data)
            8'h00: begin
                SCLK_clr = 1'b1;
                if (one_detect)      begin SDATA_set = 1'b1; next_st_data = 8'h10; end
                else if (zero_detect) begin SDATA_clr = 1'b1; next_st_data = 8'h10; end
            end
            8'h10: begin SCLK_set = 1'b1; next_st_data = 8'h11; end
            8'h11: next_st_data = 8'h12;
            8'h12: begin tclk_gate_cap = 1'b1; next_st_data = 8'h13; end
            8'h13: next_st_data = 8'h00;
            default: next_st_data = 8'h00;
        endcase
    end

    // ---- SCLK / SDATA / carrier - UNCHANGED ----
    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)        SCLK_int <= 1'b0;
        else if (SCLK_clr)  SCLK_int <= 1'b0;
        else if (SCLK_set)  SCLK_int <= 1'b1;
    end
    assign SCLK = SCLK_int;

    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)         SDATA_int <= 1'b1;
        else if (SDATA_clr)  SDATA_int <= 1'b0;
        else if (SDATA_set)  SDATA_int <= 1'b1;
    end
    assign SDATA = SDATA_int;

    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)            tclk_gate <= 1'b0;
        else if (tclk_gate_cap) tclk_gate <= TCLK;
    end
    assign TCLK_CAR = TCLK ^ tclk_gate;

    // ---- Signal-error: illegal biphase run lengths, parameterized by OSR ----
    // Detects a run of exactly L samples of one level bounded by the opposite level,
    // using window TCLK_del[L+2 : 1]. Illegal lengths for RATE=1 are 1, 2, OSR-2 and
    // 1.5*OSR-2 (matching the original 1,2,6,10 at OSR=8); 3 and OSR-1 add for RATE=0.
    localparam int LA = 1;
    localparam int LB = 2;
    localparam int LC = OSR - 2;
    localparam int LD = (3*OSR)/2 - 2;
    localparam int LE = 3;              // RATE=0 only
    localparam int LF = OSR - 1;        // RATE=0 only

    // run of L zeros:  TCLK_del[L+2]=1, TCLK_del[L+1:2]=0, TCLK_del[1]=1
    // run of L ones:   TCLK_del[L+2]=0, TCLK_del[L+1:2]=1, TCLK_del[1]=0
    // Constant-width comparisons (LA..LF are localparams, so +: widths are constant).
    wire err_a = ( TCLK_del[LA+2] & ~(|TCLK_del[2 +: LA]) & TCLK_del[1])
               | (~TCLK_del[LA+2] &  (&TCLK_del[2 +: LA]) & ~TCLK_del[1]);
    wire err_b = ( TCLK_del[LB+2] & ~(|TCLK_del[2 +: LB]) & TCLK_del[1])
               | (~TCLK_del[LB+2] &  (&TCLK_del[2 +: LB]) & ~TCLK_del[1]);
    wire err_c = ( TCLK_del[LC+2] & ~(|TCLK_del[2 +: LC]) & TCLK_del[1])
               | (~TCLK_del[LC+2] &  (&TCLK_del[2 +: LC]) & ~TCLK_del[1]);
    wire err_d = ( TCLK_del[LD+2] & ~(|TCLK_del[2 +: LD]) & TCLK_del[1])
               | (~TCLK_del[LD+2] &  (&TCLK_del[2 +: LD]) & ~TCLK_del[1]);
    wire err_e = ( TCLK_del[LE+2] & ~(|TCLK_del[2 +: LE]) & TCLK_del[1])
               | (~TCLK_del[LE+2] &  (&TCLK_del[2 +: LE]) & ~TCLK_del[1]);
    wire err_f = ( TCLK_del[LF+2] & ~(|TCLK_del[2 +: LF]) & TCLK_del[1])
               | (~TCLK_del[LF+2] &  (&TCLK_del[2 +: LF]) & ~TCLK_del[1]);

    always @(*) begin
        sig_err_detect = err_a | err_b | err_c | err_d | ((err_e | err_f) & ~RATE);
    end

    // ---- Error stretch counter - UNCHANGED ----
    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)
            sig_err_stretch <= 3'b011;
        else if ((sig_err_stretch == 3'b011) && !sig_err_detect)
            sig_err_stretch <= sig_err_stretch;
        else
            sig_err_stretch <= sig_err_stretch + 3'b001;
    end
    assign SIG_ERR = sig_err_stretch[2];

endmodule
```

- [ ] **Step 3: Run the OSR=8 regression**

Run: `.\sim.ps1 run -Module tclk_rcv` then `.\sim.ps1 run -Module tclk_readout`
Expected: PASS - identical to the Step 1 baseline. Decode, parity, timestamps, counts all match. (`OSR` defaults to 8 and `TCLK_RCV` does not override it yet.)

- [ ] **Step 4: Commit**

```bash
git add rtl/aclk_bridge/serdec4_9MHz.v
git commit -m "refactor(tclk): parameterize serdec by oversample ratio OSR (default 8)"
```

---

## Task 4: Thread OSR through TCLK_RCV and tclk_readout_top (Part A)

**Files:**
- Modify: `rtl/aclk_bridge/TCLK_RCV.v` (add `OSR` param, forward to serdec)
- Modify: `rtl/aclk_lite/tclk_readout_top.sv` (add `OSR` param, forward to `TCLK_RCV`)

**Interfaces:**
- Consumes: `serdec4_9MHz #(OSR)`.
- Produces: `TCLK_RCV #(parameter OSR=8)` and `tclk_readout_top #(..., parameter int OSR = 8)`, both forwarding `OSR` down.

- [ ] **Step 1: Add OSR to TCLK_RCV and forward it**

In `rtl/aclk_bridge/TCLK_RCV.v`, change the module header:

```verilog
module TCLK_RCV #(
    parameter int OSR = 8
) (
    input  wire        RESETn,
```

and the serdec instantiation (`uTCLK_DECODER`):

```verilog
    serdec4_9MHz #(.OSR(OSR)) uTCLK_DECODER (
        .RESETn    (RESETn),
```

- [ ] **Step 2: Add OSR to tclk_readout_top and forward it**

In `rtl/aclk_lite/tclk_readout_top.sv`, add `OSR` to the parameter list:

```verilog
module tclk_readout_top #(
    parameter int ADDR_WIDTH = 6,
    parameter int AXI_ADDR_W = 8,
    parameter bit USE_EXT_TS = 1'b0,
    parameter int OSR        = 8
) (
```

and forward it in the `TCLK_RCV` instantiation (`u_rcv`):

```verilog
    TCLK_RCV #(.OSR(OSR)) u_rcv (
        .RESETn      (rstn),
```

- [ ] **Step 3: Run the OSR=8 regression through the chain**

Run: `.\sim.ps1 run -Module tclk_rcv` then `.\sim.ps1 run -Module tclk_readout`
Expected: PASS - still bit-identical (OSR defaults to 8 everywhere; nothing overrides it yet).

- [ ] **Step 4: Commit**

```bash
git add rtl/aclk_bridge/TCLK_RCV.v rtl/aclk_lite/tclk_readout_top.sv
git commit -m "feat(tclk): thread OSR through TCLK_RCV and tclk_readout_top (default 8)"
```

---

## Task 5: Parameterize the cocotb harness by TCLK_OSR (Part A)

Make the TX model and the two TCLK tests derive their oversample count and clock periods from `TCLK_OSR` (default 8), and have the runners pass the same `OSR` to the RTL. This lets one command set run either rate.

**Files:**
- Modify: `tb/tclk_tx_model.py` (`SAMPLES_PER_CELL` from env)
- Modify: `tb/tclk_rcv/test_tclk_rcv.py` (derive clock periods)
- Modify: `tb/tclk_readout/test_tclk_readout.py` (derive clock periods)
- Modify: `tb/tclk_rcv/runner.py` and `tb/tclk_readout/runner.py` (pass `OSR` param)

**Interfaces:**
- Consumes: `TCLK_OSR` env var (default "8").
- Produces: model constant `SAMPLES_PER_CELL`, test constants `CLK80_PERIOD_PS = 100_000 // SAMPLES_PER_CELL`, `CLK40_PERIOD_PS = 200_000 // SAMPLES_PER_CELL`.

- [ ] **Step 1: Make the TX model oversample env-driven**

In `tb/tclk_tx_model.py`, replace the two module constants (lines ~19-20):

```python
import os
SAMPLES_PER_CELL = int(os.getenv("TCLK_OSR", "8"))
HALF = SAMPLES_PER_CELL // 2
```

- [ ] **Step 2: Derive clock periods in test_tclk_rcv.py**

In `tb/tclk_rcv/test_tclk_rcv.py`, import the model's cell count and derive periods; replace the hardcoded `_start_clocks` body:

```python
from tclk_tx_model import stream_samples, drive_samples, SAMPLES_PER_CELL

CLK80_PERIOD_PS = 100_000 // SAMPLES_PER_CELL     # OSR*10 MHz  (12500 at OSR=8)
CLK40_PERIOD_PS = 200_000 // SAMPLES_PER_CELL     # half that   (25000 at OSR=8)


def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.CLK_80M, CLK80_PERIOD_PS, unit="ps").start())
    cocotb.start_soon(Clock(dut.CLK_40M, CLK40_PERIOD_PS, unit="ps").start())
```

- [ ] **Step 3: Derive clock periods in test_tclk_readout.py**

In `tb/tclk_readout/test_tclk_readout.py`, replace the two hardcoded period constants (lines ~33-34):

```python
from tclk_tx_model import biphase_samples, event_bits, drive_samples, SAMPLES_PER_CELL

CLK80_PERIOD_PS = 100_000 // SAMPLES_PER_CELL   # 80 MHz serdec oversample at OSR=8
CLK40_PERIOD_PS = 200_000 // SAMPLES_PER_CELL   # 40 MHz deserializer/readout at OSR=8
```

(`AXI_PERIOD_NS = 14` stays - the AXI clock is independent of OSR.)

- [ ] **Step 4: Pass OSR to the RTL from both runners**

In `tb/tclk_rcv/runner.py`, change the call:

```python
import os
run_cocotb(
    "tclk_rcv",
    sources=[
        "rtl/aclk_bridge/serdec4_9MHz.v",
        "rtl/aclk_bridge/TCLK_DESERIALIZER2.v",
        "rtl/aclk_bridge/TCLK_RCV.v",
    ],
    hdl_toplevel="TCLK_RCV",
    parameters={"OSR": int(os.getenv("TCLK_OSR", "8"))},
)
```

In `tb/tclk_readout/runner.py`, add the same `parameters=` to its `run_cocotb(...)` (top is `tclk_readout_top`):

```python
import os
# ... existing sources list unchanged ...
    hdl_toplevel="tclk_readout_top",
    parameters={"OSR": int(os.getenv("TCLK_OSR", "8"))},
```

- [ ] **Step 5: Run the OSR=8 regression**

Run: `.\sim.ps1 run -Module tclk_rcv` then `.\sim.ps1 run -Module tclk_readout`
Expected: PASS - unchanged behavior (TCLK_OSR unset -> 8; periods resolve to 12500/25000; RTL OSR=8).

- [ ] **Step 6: Commit**

```bash
git add tb/tclk_tx_model.py tb/tclk_rcv/test_tclk_rcv.py tb/tclk_readout/test_tclk_readout.py tb/tclk_rcv/runner.py tb/tclk_readout/runner.py
git commit -m "test(tclk): parameterize TCLK sims by TCLK_OSR (default 8)"
```

---

## Task 6: OSR=40 decode proof (Part A, the arbiter)

Run the full TCLK decode chain at OSR=40 (400/200 MHz). This is where the serdec's OSR taps are proven correct at the target rate. If decode fails, the fix is in `rtl/aclk_bridge/serdec4_9MHz.v` (Task 3's taps), guided by the waveform, until the tests pass.

**Files:**
- Modify (only if the proof fails): `rtl/aclk_bridge/serdec4_9MHz.v`

**Interfaces:**
- Consumes: everything from Tasks 3-5.

- [ ] **Step 1: Run the receiver decode at OSR=40**

Run: `$env:TCLK_OSR=40; .\sim.ps1 run -Module tclk_rcv; Remove-Item Env:\TCLK_OSR`
Expected: PASS - `test_decode_known_events` decodes `[0x9D,0xD2,0x00,0x07,0x0F,0xA5,0x29]` in order with no PERR; `test_parity_error` raises PERR on the bad frame and keeps the good ones.
If FAIL: open `sim_build/tclk_rcv/*.fst` (`.\sim.ps1 wave -Module tclk_rcv`), inspect `TCLK_del`, `TCLK_del_posedge/negedge`, `SCLK`, `SDATA`, and the decode FSM state, and correct the delayed-edge tap or `DELW` in the serdec. Re-run until green.

- [ ] **Step 2: Run the full AXI chain at OSR=40**

Run: `$env:TCLK_OSR=40; .\sim.ps1 run -Module tclk_readout; Remove-Item Env:\TCLK_OSR`
Expected: PASS - all events read over AXI in order, `is_tclk=1`/`has_data=0`, 0xFF kept, `NULL_COUNT` delta 0, `ERROR_COUNT` delta 0 on clean frames, timestamps strictly increasing, no overflow; the parity test raises exactly one `ERROR_COUNT`.

- [ ] **Step 3: Re-confirm the OSR=8 regression still passes**

Run: `.\sim.ps1 run -Module tclk_rcv` then `.\sim.ps1 run -Module tclk_readout`
Expected: PASS - any serdec taps you touched must not regress OSR=8 (both rates share the same parameterized code).

- [ ] **Step 4: Commit (only if the serdec was corrected)**

```bash
git add rtl/aclk_bridge/serdec4_9MHz.v
git commit -m "fix(tclk): correct serdec OSR taps so OSR=40 decode passes"
```

If no serdec change was needed, record in the ledger that the OSR=40 proof passed with Task 3's taps and skip the commit.

---

## Task 7: TCLK wr_timebase constants for 200 MHz (Part A)

Set the TCLK `wr_timebase` constants for a 200 MHz clock, and validate the two sim-testable ones (interpolation at 5 ns, the 10 MHz watchdog window) with a dedicated small sim. `PPS_TIMEOUT` is validated by arithmetic (220e6 * 5 ns = 1.1 s) and at board bring-up.

**Files:**
- Create: `tb/wr_timebase_200/tb_wr_timebase_200_top.sv`
- Create: `tb/wr_timebase_200/test_wr_timebase_200.py`
- Create: `tb/wr_timebase_200/runner.py`
- Modify: `rtl/aclk_pipeline_bd_top.v` (`u_tb_tclk` constants near lines 407-411)

**Interfaces:**
- Consumes: `wr_timebase` (`CLK_PERIOD_DS`, `CLK10_TIMEOUT`, `PPS_TIMEOUT`) and `tb/wr_model.py`'s `WrGen(clk10, pps)`.

- [ ] **Step 1: Write the 200 MHz timebase testbench top**

Create `tb/wr_timebase_200/tb_wr_timebase_200_top.sv`:

```verilog
`timescale 1ns / 1ps
// One wr_timebase replica at the pipeline's 200 MHz TCLK constants. PPS_TIMEOUT is
// a sim-scaled value (1200 clk = 6 us > one 5 us sim-second) so PPS-loss is testable;
// the real build uses 220_000_000 (1.1 s), which is arithmetic + board only.
module tb_wr_timebase_200_top (
    input  logic        clk,
    input  logic        rstn,
    input  logic        wr_clk10,
    input  logic        wr_pps,
    input  logic        cfg_clk,
    input  logic        cfg_rstn,
    input  logic        cfg_valid,
    input  logic        cfg_disarm,
    input  logic [31:0] cfg_sec,
    output logic [63:0] ts,
    output logic        locked,
    output logic        clk10_alive,
    output logic        pps_alive,
    output logic [31:0] cells_last
);
    wr_timebase #(
        .CLK_PERIOD_DS (50),        // 5.0 ns at 200 MHz  (the real value)
        .CLK10_TIMEOUT (80),        // 400 ns window       (the real value)
        .PPS_TIMEOUT   (1200)       // 6 us  (SIM-SCALED; real build = 220_000_000)
    ) u_tb (
        .clk(clk), .rstn(rstn),
        .wr_clk10(wr_clk10), .wr_pps(wr_pps),
        .cfg_clk(cfg_clk), .cfg_rstn(cfg_rstn),
        .cfg_valid(cfg_valid), .cfg_disarm(cfg_disarm), .cfg_sec(cfg_sec),
        .ts(ts), .locked(locked), .arm_pending(),
        .pps_alive(pps_alive), .clk10_alive(clk10_alive),
        .pps_edge(), .cells_last(cells_last)
    );
endmodule
```

- [ ] **Step 2: Write the runner**

Create `tb/wr_timebase_200/runner.py`:

```python
"""Cocotb 2.0 runner for the 200 MHz TCLK wr_timebase constants."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_wr_timebase_200():
    run_cocotb(
        "wr_timebase_200",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/cdc_word_pulse.sv",
            "rtl/wr_timebase.sv",
            "tb/wr_timebase_200/tb_wr_timebase_200_top.sv",
        ],
        hdl_toplevel="tb_wr_timebase_200_top",
    )


if __name__ == "__main__":
    test_wr_timebase_200()
```

- [ ] **Step 3: Write the failing test**

Create `tb/wr_timebase_200/test_wr_timebase_200.py`:

```python
"""The 200 MHz TCLK wr_timebase constants: strict-zero before arm, lock at PPS,
ns interpolates on a 5 ns clock, and 10 MHz loss unlocks within CLK10_TIMEOUT.
Sim second = 5000 ns (50 cells * 100 ns), per tb/wr_model.py."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer
from cocotb.utils import get_sim_time

from cocotb_helpers import _b
from wr_model import WrGen

SEC0 = 1_751_800_000
SIM_NS_PER_SEC = 5000


def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.clk, 5000, unit="ps").start())     # 200 MHz
    cocotb.start_soon(Clock(dut.cfg_clk, 10, unit="ns").start())


async def _reset(dut):
    dut.rstn.value = 0; dut.cfg_rstn.value = 0
    dut.cfg_valid.value = 0; dut.cfg_disarm.value = 0; dut.cfg_sec.value = 0
    dut.wr_clk10.value = 0; dut.wr_pps.value = 0
    await ClockCycles(dut.cfg_clk, 10); await Timer(1, unit="ns")
    dut.rstn.value = 1; dut.cfg_rstn.value = 1
    await ClockCycles(dut.cfg_clk, 10)


async def _arm(dut, sec):
    await RisingEdge(dut.cfg_clk)
    dut.cfg_sec.value = sec; dut.cfg_disarm.value = 0; dut.cfg_valid.value = 1
    await RisingEdge(dut.cfg_clk)
    dut.cfg_valid.value = 0


async def _wait_locked(dut, timeout_ns=3 * SIM_NS_PER_SEC):
    for _ in range(timeout_ns // 5):
        await RisingEdge(dut.clk); await Timer(1, unit="ns")
        if _b(dut.locked) == 1:
            return
    raise AssertionError("timebase never locked after arm")


@cocotb.test()
async def test_lock_and_track_200mhz(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps); gen.start()

    # strict zero before arm
    for _ in range(20):
        await ClockCycles(dut.clk, 40); await Timer(1, unit="ns")
        assert _b(dut.ts) == 0 and _b(dut.locked) == 0, "ts/locked nonzero before arm"
    assert _b(dut.cells_last) == 50, f"cells_last {_b(dut.cells_last)} != 50 (10 MHz miscount)"

    # arm and lock
    await _arm(dut, SEC0); await _wait_locked(dut)
    sec = (_b(dut.ts) >> 32) & 0xFFFFFFFF
    ns = _b(dut.ts) & 0xFFFFFFFF
    assert sec == SEC0, f"sec {sec} != {SEC0}"
    assert ns < SIM_NS_PER_SEC, f"ns {ns} out of range"

    # ns interpolates and tracks wall time within a sync/2-FF margin
    for i in range(120):
        await ClockCycles(dut.clk, 13 + (i % 7)); await Timer(1, unit="ns")
        expected = get_sim_time(unit="ns") - gen.pps_times_ns[-1]
        ns = _b(dut.ts) & 0xFFFFFFFF
        if expected > 200:
            assert ns <= expected + 50,  f"ns {ns} leads wall {expected}"
            assert ns >= expected - 250, f"ns {ns} lags wall {expected}"
    gen.stop()


@cocotb.test()
async def test_clk10_loss_unlocks_200mhz(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps); gen.start()
    await ClockCycles(dut.clk, 1500)
    await _arm(dut, SEC0); await _wait_locked(dut)

    # 10 MHz dies: CLK10_TIMEOUT=80 clk = 400 ns; unlock well within 200 clk
    gen.clk10_on = False; dut.wr_clk10.value = 0
    await ClockCycles(dut.clk, 200); await Timer(1, unit="ns")
    assert _b(dut.locked) == 0 and _b(dut.ts) == 0, "clk10 loss did not unlock"
    gen.stop()
```

- [ ] **Step 4: Run it and confirm PASS**

Run: `.\sim.ps1 run -Module wr_timebase_200`
Expected: PASS - locks at 200 MHz, ns interpolates within the sync margin (proves `CLK_PERIOD_DS=50`), and 10 MHz loss unlocks within the 400 ns window (proves `CLK10_TIMEOUT=80` is not too short vs the 100 ns cell).

- [ ] **Step 5: Set the constants in the pipeline top**

In `rtl/aclk_pipeline_bd_top.v`, the `u_tb_tclk` instance, change the three constants:

```verilog
    wr_timebase #(
        .CLK_PERIOD_DS (50),           // clk_40m: 5.0 ns at 200 MHz
        .CLK10_TIMEOUT (80),           // 400 ns at 200 MHz
        .PPS_TIMEOUT   (220_000_000)   // 1.1 s at 200 MHz
    ) u_tb_tclk (
```

Leave `u_tb_aclk` (`CLK_PERIOD_DS (160)`, the rx_usrclk2 replica) unchanged.

- [ ] **Step 6: Commit**

```bash
git add tb/wr_timebase_200/ rtl/aclk_pipeline_bd_top.v
git commit -m "feat(pipeline): TCLK wr_timebase constants for 200 MHz + sim proof"
```

---

## Task 8: Integration - clk_wiz 400/200, serdec OSR=40, build, verify WNS (Part A)

Set the event-domain clocks to 400/200, instantiate the pipeline's TCLK readout at `OSR=40`, build, and confirm timing closes. The build TCL already carries a post-route WNS report block from the clock-push probes.

**Files:**
- Modify: `vivado/build_aclk_pipeline.tcl` (clk_wiz frequencies + the `u_pipeline` reference does not carry OSR; OSR is set on the RTL instance in the BD top - see Step 2)
- Modify: `rtl/aclk_pipeline_bd_top.v` (`tclk_readout_top` instantiation gets `.OSR(40)`)

**Interfaces:**
- Consumes: everything from Tasks 3-7.
- Produces: `build/kria/aclk_pipeline/.../uart_echo_bd_wrapper.bit.bin`.

- [ ] **Step 1: Set the clk_wiz outputs to 400/200**

In `vivado/build_aclk_pipeline.tcl`, in the `clk_wiz_0` config, set (the working tree may currently hold the 500/250 probe values - set them exactly to 400/200):

```tcl
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {400.000} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {200.000} \
```

Update the nearby comment to describe the deployable 5 ns build (not a probe).

- [ ] **Step 2: Instantiate the pipeline TCLK readout at OSR=40**

In `rtl/aclk_pipeline_bd_top.v`, the `tclk_readout_top` instantiation:

```verilog
    tclk_readout_top #(
        .ADDR_WIDTH (9),
        .AXI_ADDR_W (8),
        .USE_EXT_TS (1'b1),
        .OSR        (40)
    ) ...
```

- [ ] **Step 3: Build the bitstream**

Run (background; ~30-45 min): `.\hw.ps1 build -Tcl vivado\build_aclk_pipeline.tcl -Name aclk_pipeline`
Expected: `Build complete.` with a `uart_echo_bd_wrapper.bit.bin`. The Defender exclusion is already in place, so the IPI flake should not recur; if a stray flake occurs the 12x retry loop absorbs it.

- [ ] **Step 4: Verify timing closed**

In the build log (`build/kria/build.log`), find the `CLOCK-PUSH TIMING (impl_1, post-route)` block.
Expected: `STATS.WNS >= 0`, `STATS.FAILED_NETS = 0`, `STATS.WHS >= 0`.
If `WNS < 0` and the worst path is in the `clk_80m` serdec `sig_err` cloud: apply the contingency - pipeline `sig_err_detect` in `rtl/aclk_bridge/serdec4_9MHz.v` by registering it one `CLK_80M` stage before it feeds `sig_err_stretch` (it is diagnostic, so one cycle of added latency is harmless), re-run the OSR=8 + OSR=40 proofs (Tasks 3/6), and rebuild. Repeat until WNS >= 0.

- [ ] **Step 5: Commit**

```bash
git add vivado/build_aclk_pipeline.tcl rtl/aclk_pipeline_bd_top.v
git commit -m "feat(pipeline): clk_wiz 400/200 + serdec OSR=40 for the deployable 5 ns TCLK build"
```

- [ ] **Step 6: Record the deliverable and operator bring-up steps**

Append the build's MD5 + WNS to the commit message body or a short note in `deploy/capture.md`, and record the board bring-up follow-up (operator-run, not automated):

```markdown
## 5 ns TCLK build bring-up (operator)

1. scp the new uart_echo_bd_wrapper.bit.bin; md5sum must match the build.
2. sudo xmutil unloadapp; sudo fpgautil -b ~/<bin> -o aclk_pipeline.dtbo
3. Arm WR: sudo python3 wr_time.py /dev/uio6 arm ; confirm locked_tclk=1.
4. Confirm live TCLK still decodes: EVENT_COUNT climbs, ERROR_COUNT flat, and
   spot-check event codes vs the 25 ns build. Watch for real-line PERR/SIG_ERR
   rate (the immediate-edge glitch-rejection risk) vs the old build.
```

---

## Self-Review

Run after drafting; fix inline.

**Spec coverage:** Part A clk_wiz (Task 8), serdec OSR (Tasks 3/4/6), wr_timebase constants (Task 7), OSR=8 default isolation (Tasks 3-5 regressions), timing contingency (Task 8 Step 4). Part B FIFO deepen (Task 1), drain confirmation (Task 2). Validation: OSR=8 regression + OSR=40 proof + ACLK depth (Task 1) + overflow (async_fifo test) + real-line note (Task 8 Step 6). Every spec section maps to a task.

**Placeholder scan:** the only soft spot is Task 6 (serdec taps may need waveform-guided correction) - this is explicit TDD with the test as arbiter, not a placeholder; the concrete taps are provided in Task 3. (The dead `run_hit` stub flagged on first pass has been removed from Task 3.)

**Type consistency:** `OSR` (int, default 8) is consistent across serdec/TCLK_RCV/tclk_readout_top and the `parameters={"OSR": ...}` runner calls. `TCLK_OSR` env (str->int) drives `SAMPLES_PER_CELL` and the runner OSR together. `FIFO_ADDR_WIDTH` env drives the async_fifo test `DEPTH` and its runner `ADDR_WIDTH` together. Constants `CLK_PERIOD_DS=50`, `CLK10_TIMEOUT=80`, `PPS_TIMEOUT=220_000_000` match the spec and Task 7.
