# TCLK -> ACLK -> ACLK-Lite Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On one KR260, decode TCLK on H12 and publish it over UIO, re-encode it as gigabit ACLK over the SFP fiber loop, decode the returned ACLK and publish it over a second UIO node against a shared timebase, and mirror the decoded-back ACLK as ACLK-Lite on a Pmod pin.

**Architecture:** Reuse the shipped decoders/readout/encoder blocks unchanged; add one new live TCLK->ACLK encoder (a simmable refactor of `rtl/Li_Files/ACLK_DATA_SOURCE.v`), a shared `pl_clk0` timebase distributed to both readouts, and an ACLK-back->ACLK-Lite bridge. Verify the entire pure-RTL chain in Icarus (the GT transceiver is fed in/out directly, exactly as `tb/aclkgt_gen_loop` does), then assemble one integrated block-design top and bring it up on hardware incrementally.

**Tech Stack:** Verilog/SystemVerilog (Icarus for sim), cocotb 2.0.1 + pytest, Vivado 2024.2 (GTHE4 + block design), KR260 (xck26-sfvc784-2LV-c), Python 3 stdlib on the board.

## Global Constraints

- Style: **NO em dashes** anywhere (code, comments, docs, commit messages).
- Sim is **Icarus**; no Vivado IP in any simulated module (inferred RAM only, no `blk_mem_gen`). The GT IP is never simulated; sim feeds `ACLK_RCV` directly from the encoder word stream.
- ACLK frame format is fixed: 96-bit `{0xBC, EVENT[15:0], DATA[63:0], CRC8}`, CRC-8 poly `0x2F` over `{packet80, 8'h00}`, K = `12'b1000_0000_0000`, 96->16 via `GEARBOX_96_TO_16`. Reuse the existing `CRC8_CALC` and `GEARBOX_96_TO_16` modules (uppercase names, in `rtl/aclk_bridge/`).
- TCLK->ACLK payload (follow `ACLK_DATA_SOURCE.v`): `EVENT = {8'h00, tclk_event}`, `DATA = {32'h0, count}`, where `count` is the post-increment per-event-code occurrence number (1 for the first occurrence). Idle/null frames carry payload `80'hFF...FF` and are dropped at readout (`DROP_NULL=1`).
- AXI register block: 16-byte register spacing (LPD aliasing workaround), unchanged. Two readouts at bases `0x8000_0000` (TCLK / uio4) and `0x8001_0000` (ACLK / uio5).
- cocotb tests emit a matplotlib plot on completion (project convention; `tb/plot_util.py`).
- Reuse the endurance-proven GT config and SFP wiring from `rtl/aclk_gt_selftest_bd_top.v` (drive `sfp_tx_disable=0`, the self-healing RX recovery FSM, `aclkgt_gt` IP).
- Branch: `tclk-aclk-pipeline` (already created; spec committed at `f3e0b1b`).

## Reused, unchanged
`TCLK_RCV` (+ `serdec4_9MHz`, `TCLK_DESERIALIZER2`), `ACLK_RCV` (`ACLK_REV.v`), `aclk_readout_axi`, `aclk_lite_encoder`, `aclkgt_gt` IP, `CRC8_CALC`, `GEARBOX_96_TO_16`, `GEARBOX_16_TO_96`, `cdc_gray_count`, `async_fifo`, `synchronizer`. Test models: `tb/aclk_tx_model.py` (`build_frame`, `crc8`, `frame_to_words`, `stream_frames`), `tb/tclk_tx_model.py` (`stream_samples`, `drive_samples`), `tb/clk_tx_model.py` (`frame_bits`), `tb/axi_lite_bfm.py` (`axi_read`, `axi_write`), `tb/plot_util.py`.

## File structure

| File | New/Mod | Responsibility |
|---|---|---|
| `rtl/aclk_gt/aclk_tclk_encoder.v` | New | Live TCLK event stream -> gigabit ACLK 16b+K word stream (count RAM, null-fill, CRC, gearbox) |
| `rtl/global_timebase.v` | New | One `pl_clk0` free-running 64-bit counter distributed (CDC) to two event domains |
| `rtl/aclk_lite_bridge.v` | New | Decoded-back ACLK real events -> drive `aclk_lite_encoder` (rx_usrclk2 -> enc clock CDC) |
| `rtl/aclk_readout/aclk_readout_core.sv` | Mod | Add `USE_EXT_TS` param + `ts_ext` input (external shared timestamp) |
| `rtl/aclk_readout/aclk_readout_axi.sv` | Mod | Thread `USE_EXT_TS` + `ts_ext` to the core |
| `rtl/aclk_lite/tclk_readout_top.sv` | Mod | Thread `USE_EXT_TS` + `ts_ext`; set `USE_EXT_TS=1` in the pipeline |
| `rtl/aclk_gt/aclk_gt_readout_top.sv` | Mod | Thread `USE_EXT_TS` + `ts_ext`; set `USE_EXT_TS=1` in the pipeline |
| `rtl/aclk_pipeline_bd_top.v` | New | Integrated BD top: 1 TCLK_RCV, 2 readouts, encoder, GT duplex, ACLK_RCV, bridge+ACLK-Lite encoder, timebase |
| `vivado/build_aclk_pipeline.tcl` | New | BD build: 2-slave SmartConnect, address map, MMCM, GT IP |
| `constraints/kr260_aclk_pipeline.xdc` | New | Pin LOCs + async clock groups |
| `deploy/aclk_pipeline.dts` | New | Device-tree overlay with two `generic-uio` nodes |
| `tb/aclk_tclk_encoder_loop/` | New | A1 sim: encoder -> ACLK_RCV event/count agreement |
| `tb/global_timebase/` | New | A2 sim: shared monotonic timebase across domains |
| `tb/aclk_readout_ext_ts/` | New | A3 sim: external timestamp appears in the TS register |
| `tb/aclk_lite_bridge/` | New | A4 sim: decoded ACLK event -> bridge -> encoder -> clk_rcv decode |
| `tb/aclk_pipeline_chain/` | New | A5 sim: full pure-RTL chain, both readouts, shared timestamps |

---

## Task A1: `aclk_tclk_encoder` + encoder->ACLK_RCV loop test

**Files:**
- Create: `rtl/aclk_gt/aclk_tclk_encoder.v`
- Create: `tb/aclk_tclk_encoder_loop/tb_aclk_tclk_encoder_loop_top.sv`
- Create: `tb/aclk_tclk_encoder_loop/test_aclk_tclk_encoder_loop.py`
- Create: `tb/aclk_tclk_encoder_loop/runner.py`
- Reference to adapt: `rtl/Li_Files/ACLK_DATA_SOURCE.v`, `rtl/aclk_gt/aclk_gt_frame_gen.v`

**Interfaces:**
- Produces: `module aclk_tclk_encoder(input clk_tx, input rstn_tx, input [7:0] tclk_data, input tclk_davn, output [15:0] data16, output [1:0] k_out, output marker)`. `tclk_data`/`tclk_davn` are in a slower (clk_40m) domain; the encoder CDCs them in. `data16`/`k_out` feed a GT TX (or `ACLK_RCV.DATA_FROM_XCVR`/`K_FROM_XCVR` in sim). Emits a frame every 6 `clk_tx` cycles: a real frame (`{0x00,event}`/`{0,count}`) when a TCLK event is pending, else a null (`0xFF...FF`).
- Consumes: `CRC8_CALC`, `GEARBOX_96_TO_16` (from `rtl/aclk_bridge/`).

- [ ] **Step 1: Write the failing test (tb top)**

`tb/aclk_tclk_encoder_loop/tb_aclk_tclk_encoder_loop_top.sv`:
```systemverilog
`timescale 1ns / 1ps
// Drives the encoder from a clk_40m-domain TCLK event stream and pipes its
// 16b+K output straight into ACLK_RCV (no GT, exactly like tb_aclkgt_gen_loop).
module tb_aclk_tclk_encoder_loop_top (
    input  wire        clk_tx,        // ~62.5 MHz encoder + RX clock
    input  wire        clk_40m,       // event-input domain
    input  wire        rstn,
    input  wire [7:0]  tclk_data,
    input  wire        tclk_davn,
    output wire [15:0] ACLK_EVENT,
    output wire [63:0] ACLK_DATA,
    output wire        ACLK_VALID,
    output wire        ACLK_ERROR,
    output wire        RX_ALIGNED_OUT
);
    wire [15:0] w_data16;
    wire [1:0]  w_k;

    aclk_tclk_encoder u_enc (
        .clk_tx   (clk_tx),
        .rstn_tx  (rstn),
        .tclk_data(tclk_data),
        .tclk_davn(tclk_davn),
        .data16   (w_data16),
        .k_out    (w_k),
        .marker   ()
    );

    ACLK_RCV u_rcv (
        .RESETn         (rstn),
        .CLK1           (clk_tx),
        .DATA_FROM_XCVR (w_data16),
        .K_FROM_XCVR    (w_k),
        .ACLK_EVENT     (ACLK_EVENT),
        .ACLK_DATA      (ACLK_DATA),
        .ACLK_VALID     (ACLK_VALID),
        .ACLK_ERROR     (ACLK_ERROR),
        .RX_ALIGNED_OUT (RX_ALIGNED_OUT),
        .DIAG           ()
    );
endmodule
```

`tb/aclk_tclk_encoder_loop/test_aclk_tclk_encoder_loop.py`:
```python
"""Encoder -> ACLK_RCV: each injected TCLK event must decode as EVENT={0x00,ev},
DATA={32'h0,count} where count is the per-event-code occurrence number (1-based),
nulls (event[7:0]==0xFF) excluded, in order, with no CRC errors."""
import sys
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from plot_util import save_cumulative_plot  # noqa: E402

# (event_byte) sequence to inject on TCLK; repeats exercise the count RAM.
EVENTS = [0x02, 0x07, 0x02, 0x42, 0x07, 0x02]


async def drive_event(dut, byte):
    """One DAVn strobe (active-low) in the clk_40m domain with the data byte."""
    await RisingEdge(dut.clk_40m)
    dut.tclk_data.value = byte
    dut.tclk_davn.value = 0
    await RisingEdge(dut.clk_40m)
    dut.tclk_davn.value = 1
    # space events out so each is framed before the next arrives
    await ClockCycles(dut.clk_40m, 60)


@cocotb.test()
async def test_encoder_to_rcv(dut):
    cocotb.start_soon(Clock(dut.clk_tx, 16, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_40m, 25, unit="ns").start())
    dut.tclk_davn.value = 1
    dut.tclk_data.value = 0
    dut.rstn.value = 0
    await ClockCycles(dut.clk_tx, 10)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    await ClockCycles(dut.clk_tx, 40)   # let the count RAM zeroing sweep finish

    captured = []
    errors = 0

    async def collector():
        nonlocal errors
        while True:
            await RisingEdge(dut.clk_tx)
            await Timer(1, unit="ns")
            if int(dut.ACLK_VALID.value) == 1:
                ev = int(dut.ACLK_EVENT.value)
                da = int(dut.ACLK_DATA.value)
                if (ev & 0xFF) != 0xFF:        # skip nulls
                    captured.append((ev, da))
            if int(dut.ACLK_ERROR.value) == 1:
                errors += 1

    cocotb.start_soon(collector())

    for b in EVENTS:
        await drive_event(dut, b)
    await ClockCycles(dut.clk_tx, 400)

    assert int(dut.RX_ALIGNED_OUT.value) == 1, "RX never aligned"
    assert errors == 0, f"unexpected CRC errors: {errors}"
    assert captured, "no real events decoded"

    # Expected per-event-code occurrence count (1-based), in injection order.
    counts = {}
    expected = []
    for b in EVENTS:
        counts[b] = counts.get(b, 0) + 1
        expected.append((0x0000 | b, counts[b]))

    got = [(ev & 0xFFFF, da & 0xFFFFFFFF) for (ev, da) in captured]
    assert got == expected, f"decoded {got} != expected {expected}"
    dut._log.info(f"encoder<->rcv agree: {got}")

    out = (Path(__file__).resolve().parents[2] / "sim_build"
           / "aclk_tclk_encoder_loop" / "plots" / "encoder_events.png")
    save_cumulative_plot(list(range(len(got))), list(range(1, len(got) + 1)), None, out)
```

`tb/aclk_tclk_encoder_loop/runner.py` (mirror `tb/aclkgt_gen_loop/runner.py`):
```python
import os, sys
from pathlib import Path
from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")
TB_DIR = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR = PROJ_DIR / "rtl"
BUILD = PROJ_DIR / "sim_build" / "aclk_tclk_encoder_loop"
sys.path.insert(0, str(TB_DIR)); sys.path.insert(0, str(TB_DIR.parent))
_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_tclk_encoder_loop():
    runner = get_runner(SIM)
    runner.build(
        sources=[
            RTL_DIR / "aclk_bridge" / "crc8_calc.v",   # file is lowercase; module is CRC8_CALC
            RTL_DIR / "aclk_bridge" / "gearbox_96_to_16.v",
            RTL_DIR / "aclk_bridge" / "GEARBOX_16_TO_96.v",
            RTL_DIR / "aclk_bridge" / "ACLK_REV.v",
            RTL_DIR / "aclk_gt" / "aclk_tclk_encoder.v",
            TB_DIR / "tb_aclk_tclk_encoder_loop_top.sv",
        ],
        hdl_toplevel="tb_aclk_tclk_encoder_loop_top",
        build_dir=BUILD, timescale=("1ns", "1ps"), waves=True, always=True,
    )
    runner.test(hdl_toplevel="tb_aclk_tclk_encoder_loop_top",
                test_module="test_aclk_tclk_encoder_loop", build_dir=BUILD, waves=True)


if __name__ == "__main__":
    test_aclk_tclk_encoder_loop()
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `python -m pytest tb/aclk_tclk_encoder_loop/runner.py -v`
Expected: FAIL at build (`aclk_tclk_encoder.v` does not exist / cannot find module).

- [ ] **Step 3: Implement `aclk_tclk_encoder.v`**

Adapt `rtl/Li_Files/ACLK_DATA_SOURCE.v` (the active, non-commented top half) with these exact changes:
1. New port list (above): remove `CLK_80MHZ`/`CLK_40MHZ`/`ERROR_INPUTn`/`TCLK` and all `*_debug` ports; rename `CLK1`->`clk_tx`, `RESETn`->`rstn_tx`; add `tclk_data[7:0]`/`tclk_davn` inputs; outputs `data16`/`k_out`/`marker`.
2. Delete the internal `TCLK_RCV uTCLK_RCV` instance. The `DAVn_toggle`/`TCLK_DATA_cdc` capture block (its lines 85-93) now samples the **input** `tclk_davn`/`tclk_data` (clocked by an input you must add for that domain). Simplify: drive the toggle in the `clk_tx` domain off a 2-FF synchronized `tclk_davn` edge. Replace lines 85-107 with:
```verilog
    // bring the slow-domain DAVn strobe into clk_tx via a toggle + edge detect
    reg davn_toggle;
    reg [7:0] tclk_data_hold;
    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx) begin davn_toggle <= 1'b0; tclk_data_hold <= 8'h00; end
        else if (!tclk_davn) begin davn_toggle <= ~davn_toggle; tclk_data_hold <= tclk_data; end
    end
    reg t1, t2, t3;
    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx) begin t1 <= 1'b0; t2 <= 1'b0; t3 <= 1'b0; end
        else begin t1 <= davn_toggle; t2 <= t1; t3 <= t2; end
    end
    wire DAVn_in_CLK1 = t2 ^ t3;
    wire [7:0] TCLK_DATA_cdc = tclk_data_hold;
```
   (Note: this collapses the original two-clock toggle into a single `clk_tx` toggle gated by the asynchronous active-low `tclk_davn`. It is metastability-safe for the sparse TCLK event rate. Keep `tclk_event_reg` capturing `TCLK_DATA_cdc` on `DAVn_in_CLK1` exactly as the original.)
3. Replace the `blk_mem_gen_0 uTCLK_EVENT_COUNT_RAM` instance (lines 312-325) with an inferred 256x32 dual-port RAM:
```verilog
    reg [31:0] count_ram [0:255];
    reg [31:0] tclk_count_q_r;
    always @(posedge clk_tx) begin
        if (tclk_count_store) count_ram[tclk_count_addr] <= tclk_count_d;
        tclk_count_q_r <= count_ram[tclk_count_addr];
    end
    wire [31:0] tclk_count_q = tclk_count_q_r;
```
4. Delete the `LFSR80 uPRPG_TX` instance, `PR_PATT_80`, `prpg_tx_output`, `prpg_tx_biterrs`, and the `ERROR_INPUTn*` logic. Replace the gearbox feed (lines 343-346) so no error injection occurs:
```verilog
    assign ACLK_TX_TO_GEARBOX = aclk_tx_noerr;   // {0xBC, aclk_packet, CRC8}, no error inject
```
5. Keep verbatim: `lfsr_adv_ctr`/`lfsr_adv0`/`lfsr_adv1` cadence; the `crnt_st_tclk_rcv` FSM; `tclk_count_zero_ctr` zeroing sweep; `tclk_or_null`; `tclk_packet <= {8'h00, tclk_count_addr, 32'h00000000, tclk_count_d}`; `aclk_packet <= tclk_or_null ? tclk_packet : 80'hFF..FF`; `CRC8_CALC uCRC8_CALC_TX` over `{aclk_packet, 8'h00}`; `aclk_tx_noerr = {8'hBC, aclk_packet, ACLK_TX_CRC}`; `GEARBOX_96_TO_16 uTX_GEARBOX`.
6. Wire outputs: `assign data16 = ACLK_DATA_OUT; assign k_out = ACLK_K_TO_XCVR; assign marker = ACLK_TX_TO_GEARBOX_VALID;`. Delete the `ACLK_DATA_OUT_bitreverse` block.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `python -m pytest tb/aclk_tclk_encoder_loop/runner.py -v`
Expected: PASS. Log shows `encoder<->rcv agree: [(2,1),(7,1),(2,2),(0x42,1),(7,2),(2,3)]`.

- [ ] **Step 5: Commit**

```bash
git add rtl/aclk_gt/aclk_tclk_encoder.v tb/aclk_tclk_encoder_loop/
git commit -m "feat(pipeline): live TCLK->ACLK encoder (simmable ACLK_DATA_SOURCE) + loop test"
```

---

## Task A2: `global_timebase` shared timestamp

**Files:**
- Create: `rtl/global_timebase.v`
- Create: `tb/global_timebase/test_global_timebase.py`
- Create: `tb/global_timebase/runner.py`

**Interfaces:**
- Produces: `module global_timebase(input ref_clk, input ref_rstn, input dst_clk_a, output [63:0] ts_a, input dst_clk_b, output [63:0] ts_b)`. `ts_a`/`ts_b` are the same free-running `ref_clk` tick count, each synchronized into its destination domain. Used by the integration top to give both readouts one timebase.
- Consumes: `cdc_gray_count` (`rtl/cdc_gray_count.sv`).

- [ ] **Step 1: Write the failing test**

`tb/global_timebase/test_global_timebase.py`:
```python
"""Two destination domains observe the same free-running ref_clk tick count:
each output is monotonic and they agree within a couple of sync cycles."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer


@cocotb.test()
async def test_shared_monotonic(dut):
    cocotb.start_soon(Clock(dut.ref_clk, 10, unit="ns").start())
    cocotb.start_soon(Clock(dut.dst_clk_a, 25, unit="ns").start())
    cocotb.start_soon(Clock(dut.dst_clk_b, 16, unit="ns").start())
    dut.ref_rstn.value = 0
    await ClockCycles(dut.ref_clk, 5)
    await Timer(1, unit="ns")
    dut.ref_rstn.value = 1

    prev_a = prev_b = -1
    for _ in range(400):
        await RisingEdge(dut.ref_clk)
        await Timer(1, unit="ns")
        a = int(dut.ts_a.value)
        b = int(dut.ts_b.value)
        assert a >= prev_a, f"ts_a went backwards: {a} < {prev_a}"
        assert b >= prev_b, f"ts_b went backwards: {b} < {prev_b}"
        prev_a, prev_b = a, b

    # after running, both should be close (within a small sync/decode margin)
    assert abs(int(dut.ts_a.value) - int(dut.ts_b.value)) <= 6, "domains diverged"
    dut._log.info(f"timebase a={int(dut.ts_a.value)} b={int(dut.ts_b.value)}")
```

`tb/global_timebase/runner.py` (mirror the A1 runner; sources = `rtl/synchronizer.sv`, `rtl/cdc_gray_count.sv`, `rtl/global_timebase.v`; `hdl_toplevel="global_timebase"`, `build_dir=.../global_timebase`, `test_module="test_global_timebase"`).

- [ ] **Step 2: Run to confirm it fails**

Run: `python -m pytest tb/global_timebase/runner.py -v`
Expected: FAIL at build (`global_timebase` not found).

- [ ] **Step 3: Implement `rtl/global_timebase.v`**

```verilog
// rtl/global_timebase.v
// One free-running 64-bit tick counter in ref_clk, distributed to two event
// domains via the project's gray-code CDC. Both cdc_gray_count instances share
// ref_clk and ref_rstn with incr=1, so their source counters are bit-identical:
// ts_a and ts_b are the same timebase, each safely sampled into its domain.
`timescale 1ns / 1ps
module global_timebase (
    input  wire        ref_clk,
    input  wire        ref_rstn,
    input  wire        dst_clk_a,
    output wire [63:0] ts_a,
    input  wire        dst_clk_b,
    output wire [63:0] ts_b
);
    cdc_gray_count #(.W(64)) u_a (
        .src_clk(ref_clk), .src_rstn(ref_rstn), .incr(1'b1),
        .dst_clk(dst_clk_a), .count_dst(ts_a));
    cdc_gray_count #(.W(64)) u_b (
        .src_clk(ref_clk), .src_rstn(ref_rstn), .incr(1'b1),
        .dst_clk(dst_clk_b), .count_dst(ts_b));
endmodule
```

- [ ] **Step 4: Run to confirm it passes**

Run: `python -m pytest tb/global_timebase/runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rtl/global_timebase.v tb/global_timebase/
git commit -m "feat(pipeline): shared pl_clk0 timebase distributed to two domains"
```

---

## Task A3: external-timestamp option in the readout

**Files:**
- Modify: `rtl/aclk_readout/aclk_readout_core.sv`
- Modify: `rtl/aclk_readout/aclk_readout_axi.sv`
- Modify: `rtl/aclk_lite/tclk_readout_top.sv`
- Modify: `rtl/aclk_gt/aclk_gt_readout_top.sv`
- Create: `tb/aclk_readout_ext_ts/tb_ext_ts_top.sv`
- Create: `tb/aclk_readout_ext_ts/test_ext_ts.py`
- Create: `tb/aclk_readout_ext_ts/runner.py`

**Interfaces:**
- Produces: `aclk_readout_core` and `aclk_readout_axi` gain `parameter bit USE_EXT_TS = 1'b0` and `input logic [63:0] ts_ext`. When `USE_EXT_TS=1`, the packed timestamp is `ts_ext` (sampled at the event cycle) instead of the internal counter. `tclk_readout_top` and `aclk_gt_readout_top` gain a `ts_ext` input and instantiate their AXI with `USE_EXT_TS(1'b1)`. Default behavior (param 0) is unchanged for existing builds.

- [ ] **Step 1: Write the failing test**

`tb/aclk_readout_ext_ts/tb_ext_ts_top.sv`: instantiate `aclk_readout_axi #(.USE_EXT_TS(1'b1), .DROP_NULL(1'b0))` with `rx_clk`, `s_axi_*` exposed, a driven `ts_ext`, and direct `aclk_valid`/`aclk_event`/`aclk_data`/`flags` inputs. (Model on `tb/aclk_readout_axi/tb_aclk_readout_axi_top.sv`, adding `ts_ext` and the param.)

`tb/aclk_readout_ext_ts/test_ext_ts.py`:
```python
"""With USE_EXT_TS=1, the TS_HI/TS_LO register must reflect the driven ts_ext
value captured at the event's VALID cycle, not an internal counter."""
import sys
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from axi_lite_bfm import axi_read  # noqa: E402

STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP = 0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60


@cocotb.test()
async def test_ext_ts_in_register(dut):
    cocotb.start_soon(Clock(dut.rx_clk, 16, unit="ns").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, 10, unit="ns").start())
    dut.rx_rstn.value = 0; dut.s_axi_aresetn.value = 0
    dut.aclk_valid.value = 0; dut.ts_ext.value = 0
    await ClockCycles(dut.s_axi_aclk, 5)
    await Timer(1, unit="ns")
    dut.rx_rstn.value = 1; dut.s_axi_aresetn.value = 1
    await ClockCycles(dut.rx_clk, 5)

    # drive a known ts_ext and a one-cycle event at that instant
    await RisingEdge(dut.rx_clk)
    dut.ts_ext.value = 0x00000000_DEADBEEF
    dut.aclk_event.value = 0x0042
    dut.aclk_data.value = 0
    dut.flags.value = 0x0002
    dut.aclk_valid.value = 1
    await RisingEdge(dut.rx_clk)
    dut.aclk_valid.value = 0
    await ClockCycles(dut.s_axi_aclk, 20)

    assert (await axi_read(dut, STATUS)) & 0x1 == 0, "FIFO unexpectedly empty"
    ts = (await axi_read(dut, TS_HI) << 32) | (await axi_read(dut, TS_LO))
    assert ts == 0xDEADBEEF, f"TS={ts:#x} did not capture ts_ext"
    dut._log.info("ext ts captured into TS register")
```

`tb/aclk_readout_ext_ts/runner.py`: mirror the A1 runner; sources = `rtl/synchronizer.sv`, `rtl/async_fifo.sv`, `rtl/cdc_gray_count.sv`, `rtl/aclk_readout/aclk_readout_core.sv`, `rtl/aclk_readout/aclk_readout_axi.sv`, `tb/aclk_readout_ext_ts/tb_ext_ts_top.sv`; `hdl_toplevel="tb_ext_ts_top"`.

- [ ] **Step 2: Run to confirm it fails**

Run: `python -m pytest tb/aclk_readout_ext_ts/runner.py -v`
Expected: FAIL at build (`USE_EXT_TS`/`ts_ext` unknown).

- [ ] **Step 3: Implement the core change**

In `rtl/aclk_readout/aclk_readout_core.sv`: add to the parameter list `parameter bit USE_EXT_TS = 1'b0`, add to the port list `input logic [63:0] ts_ext`, and change the packed word to use the selected timestamp:
```verilog
    wire [63:0] ts_used = USE_EXT_TS ? ts_ext : ts;
    wire [159:0] packed_word = {flags, ts_used, aclk_event, aclk_data};
```
(Keep the internal `ts` counter so `USE_EXT_TS=0` is byte-identical to today.)

- [ ] **Step 4: Thread the param/port up**

In `rtl/aclk_readout/aclk_readout_axi.sv`: add `parameter bit USE_EXT_TS = 1'b0` and `input logic [63:0] ts_ext`; pass `.USE_EXT_TS(USE_EXT_TS)` and `.ts_ext(ts_ext)` to the `aclk_readout_core` instance.

In `rtl/aclk_lite/tclk_readout_top.sv`: add `input logic [63:0] ts_ext` to the port list and pass `.USE_EXT_TS(1'b1)`, `.ts_ext(ts_ext)` to its `aclk_readout_axi` instance.

In `rtl/aclk_gt/aclk_gt_readout_top.sv`: add `input logic [63:0] ts_ext` to the port list and pass `.USE_EXT_TS(1'b1)`, `.ts_ext(ts_ext)` to its `aclk_readout_axi` instance.

- [ ] **Step 5: Run to confirm it passes**

Run: `python -m pytest tb/aclk_readout_ext_ts/runner.py -v`
Expected: PASS.

- [ ] **Step 6: Run the existing readout tests to confirm no regression**

Run: `python -m pytest tb/aclk_readout_axi/runner.py tb/tclk_readout/runner.py tb/aclkgt_readout/runner.py -v`
Expected: PASS (default `USE_EXT_TS=0` path unchanged).

- [ ] **Step 7: Commit**

```bash
git add rtl/aclk_readout/aclk_readout_core.sv rtl/aclk_readout/aclk_readout_axi.sv \
        rtl/aclk_lite/tclk_readout_top.sv rtl/aclk_gt/aclk_gt_readout_top.sv \
        tb/aclk_readout_ext_ts/
git commit -m "feat(pipeline): external shared-timebase option in the readout core"
```

---

## Task A4: `aclk_lite_bridge` (decoded ACLK -> ACLK-Lite)

**Files:**
- Create: `rtl/aclk_lite_bridge.v`
- Create: `tb/aclk_lite_bridge/tb_aclk_lite_bridge_top.sv`
- Create: `tb/aclk_lite_bridge/test_aclk_lite_bridge.py`
- Create: `tb/aclk_lite_bridge/runner.py`

**Interfaces:**
- Produces: `module aclk_lite_bridge(input rx_clk, input rx_rstn, input aclk_valid, input [15:0] aclk_event, input [63:0] aclk_data, input enc_clk, input enc_rstn, output [15:0] enc_event_id, output [63:0] enc_data, output [1:0] enc_frame_type, output enc_start, input enc_busy, output [15:0] dropped_count)`. Filters real events (`aclk_valid && aclk_event[7:0]!=0xFF`), CDCs them `rx_clk->enc_clk` through an `async_fifo`, and drives `aclk_lite_encoder` with `frame_type=2` (`enc_frame_type=2'd2`), pulsing `enc_start` when `!enc_busy`. FIFO-full increments `dropped_count`.
- Consumes: `async_fifo`, `aclk_lite_encoder` (encoder is instantiated by the testbench / integration top, not by the bridge).

- [ ] **Step 1: Write the failing test (tb top)**

`tb/aclk_lite_bridge/tb_aclk_lite_bridge_top.sv`: wire `aclk_lite_bridge` -> `aclk_lite_encoder` (SAMPLES_PER_CELL=8) -> `clk_rcv` decoder; expose `rx_clk`, `enc_clk`, the `aclk_valid`/`aclk_event`/`aclk_data` stimulus inputs, and the `clk_rcv` decoded outputs (`event_valid`, `event_id`, `data_valid`, `data`). (Model the clk_rcv side on `tb/clk_rcv/` / `tb/clk_readout/`; `enc_clk` = clk_80m equivalent at ~80 MHz; `clk_rcv` runs on the same oversample/byte clocks it expects.)

`tb/aclk_lite_bridge/test_aclk_lite_bridge.py`: drive one decoded ACLK event `(event=0x1234, data=0xDEADBEEFCAFE0001)` on the `rx_clk` side; assert the bridge issues `enc_start`, and that `clk_rcv` recovers `event_id==0x1234` and `data==0xDEADBEEFCAFE0001` within a frame time; assert a null event `(event=0x00FF)` produces no `enc_start`. (Use the `tb/clk_rcv` decode-collection pattern; emit a line plot via `_save_line_plot` style helper.)

`tb/aclk_lite_bridge/runner.py`: sources = `rtl/synchronizer.sv`, `rtl/async_fifo.sv`, `rtl/aclk_lite_bridge.v`, `rtl/aclk_lite/aclk_lite_encoder.sv`, the `clk_rcv` chain (`rtl/aclk_bridge/serdec4_9MHz.v`, `rtl/aclk_lite/clk_byte_framer.sv`, `rtl/aclk_lite/clk_rcv.sv`), and the tb top.

- [ ] **Step 2: Run to confirm it fails**

Run: `python -m pytest tb/aclk_lite_bridge/runner.py -v`
Expected: FAIL at build (`aclk_lite_bridge` not found).

- [ ] **Step 3: Implement `rtl/aclk_lite_bridge.v`**

```verilog
// rtl/aclk_lite_bridge.v
// Decoded-back ACLK real events -> drive aclk_lite_encoder (full 12-byte frames).
// Real events (event[7:0]!=0xFF) cross rx_clk->enc_clk via async_fifo; on the enc
// side, when the encoder is idle, pop one and pulse start. FIFO-full drops + counts.
`timescale 1ns / 1ps
module aclk_lite_bridge (
    input  wire        rx_clk,
    input  wire        rx_rstn,
    input  wire        aclk_valid,
    input  wire [15:0] aclk_event,
    input  wire [63:0] aclk_data,
    input  wire        enc_clk,
    input  wire        enc_rstn,
    output reg  [15:0] enc_event_id,
    output reg  [63:0] enc_data,
    output wire [1:0]  enc_frame_type,
    output reg         enc_start,
    input  wire        enc_busy,
    output reg  [15:0] dropped_count
);
    assign enc_frame_type = 2'd2;          // full 12-byte packet

    wire is_real = aclk_valid && (aclk_event[7:0] != 8'hFF);

    wire        full, empty;
    wire [79:0] rd_data;
    reg         rd_en;

    async_fifo #(.WIDTH(80), .ADDR_WIDTH(4)) u_fifo (
        .wr_clk(rx_clk), .wr_rstn(rx_rstn), .wr_en(is_real && !full),
        .wr_data({aclk_event, aclk_data}), .full(full), .overflow(),
        .rd_clk(enc_clk), .rd_rstn(enc_rstn), .rd_en(rd_en),
        .rd_data(rd_data), .empty(empty)
    );

    // drop accounting (rx_clk domain)
    always @(posedge rx_clk or negedge rx_rstn) begin
        if (!rx_rstn) dropped_count <= 16'd0;
        else if (is_real && full) dropped_count <= dropped_count + 16'd1;
    end

    // enc_clk dispatch FSM: pop -> latch -> start -> wait busy high then low
    localparam [1:0] S_IDLE=2'd0, S_LATCH=2'd1, S_START=2'd2, S_WAIT=2'd3;
    reg [1:0] st;
    always @(posedge enc_clk or negedge enc_rstn) begin
        if (!enc_rstn) begin
            st <= S_IDLE; rd_en <= 1'b0; enc_start <= 1'b0;
            enc_event_id <= 16'd0; enc_data <= 64'd0;
        end else begin
            rd_en <= 1'b0; enc_start <= 1'b0;
            case (st)
                S_IDLE:  if (!empty && !enc_busy) begin rd_en <= 1'b1; st <= S_LATCH; end
                S_LATCH: begin enc_event_id <= rd_data[79:64]; enc_data <= rd_data[63:0]; st <= S_START; end
                S_START: begin enc_start <= 1'b1; st <= S_WAIT; end
                S_WAIT:  if (enc_busy == 1'b0 && enc_start == 1'b0) st <= S_IDLE;
            endcase
        end
    end
endmodule
```
(The `S_WAIT` exit waits for the encoder to finish the frame; because `enc_start` is registered, the `start==0` guard avoids re-popping on the same cycle. If the encoder asserts `busy` only after a cell boundary, add a 1-cycle delay before sampling `enc_busy` in `S_WAIT`; verify against the encoder timing in sim.)

- [ ] **Step 4: Run to confirm it passes**

Run: `python -m pytest tb/aclk_lite_bridge/runner.py -v`
Expected: PASS (event recovered through the Manchester encode/decode; null produced no frame).

- [ ] **Step 5: Commit**

```bash
git add rtl/aclk_lite_bridge.v tb/aclk_lite_bridge/
git commit -m "feat(pipeline): ACLK-back -> ACLK-Lite bridge (full-frame Manchester mirror)"
```

---

## Task A5: full pure-RTL pipeline chain sim

**Files:**
- Create: `tb/aclk_pipeline_chain/tb_aclk_pipeline_chain_top.sv`
- Create: `tb/aclk_pipeline_chain/test_aclk_pipeline_chain.py`
- Create: `tb/aclk_pipeline_chain/runner.py`
- Modify: `tb/axi_lite_bfm.py` (add optional signal-prefix support)

**Interfaces:**
- Consumes: everything from A1-A4 plus `TCLK_RCV`, `tclk_readout_top`, `aclk_gt_readout_top`. No new RTL.

- [ ] **Step 1: Extend the AXI BFM for two slaves**

In `tb/axi_lite_bfm.py`, add an optional `pfx=""` argument to `axi_read`/`axi_write` and resolve signals via `getattr(dut, pfx + "s_axi_araddr")` etc. Default `pfx=""` keeps every existing call working. (Two readouts are exposed as `s_axi_*` and `s2_axi_*` in the tb top; reads use `pfx=""` and `pfx="s2_"`.)

- [ ] **Step 2: Write the failing chain test (tb top)**

`tb/aclk_pipeline_chain/tb_aclk_pipeline_chain_top.sv` wires the **pure-RTL** chain (no GT):
- clocks: `clk_80m`, `clk_40m`, `clk_tx` (=rx for ACLK), `pl_clk0`.
- `global_timebase`: `ref_clk=pl_clk0`, `dst_clk_a=clk_40m -> ts_tclk`, `dst_clk_b=clk_tx -> ts_aclk`.
- `tclk` input -> `TCLK_RCV` (clk_40m/clk_80m) -> `tclk_data`/`davn`.
- `TCLK_RCV` outputs -> `tclk_readout_top` (`ts_ext=ts_tclk`, S_AXI = `s_axi_*`).
- `TCLK_RCV` outputs -> `aclk_tclk_encoder` -> `ACLK_RCV` -> `aclk_gt_readout_top` (`ts_ext=ts_aclk`, S_AXI = `s2_axi_*`).
- both AXI slaves on `pl_clk0`.

`tb/aclk_pipeline_chain/test_aclk_pipeline_chain.py`:
```python
"""Full pure-RTL chain: a TCLK biphase stimulus must appear at BOTH readouts.
Readout #1 (uio4) decodes the raw TCLK byte; readout #2 (uio5) decodes the same
event re-encoded as ACLK; both timestamps come from the shared timebase and the
ACLK-side timestamp is >= the TCLK-side timestamp for a matched event."""
import sys
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from axi_lite_bfm import axi_read           # noqa: E402
from tclk_tx_model import stream_samples, drive_samples  # noqa: E402

STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP = 0x00,0x10,0x20,0x30,0x40,0x50,0x60
EVENTS = [0x02, 0x07, 0x42]


async def pop_all(dut, pfx):
    out = []
    while (await axi_read(dut, STATUS, pfx=pfx)) & 0x1 == 0:
        ev = await axi_read(dut, EVENT, pfx=pfx)
        ts = (await axi_read(dut, TS_HI, pfx=pfx) << 32) | (await axi_read(dut, TS_LO, pfx=pfx))
        await axi_read(dut, DATA_HI, pfx=pfx); await axi_read(dut, DATA_LO, pfx=pfx)
        # POP is a write; use axi_write
        from axi_lite_bfm import axi_write
        await axi_write(dut, POP, 0, pfx=pfx)
        out.append((ev & 0xFFFF, ts))
    return out


@cocotb.test()
async def test_full_chain(dut):
    cocotb.start_soon(Clock(dut.clk_80m, 12.5, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_40m, 25, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_tx, 16, unit="ns").start())
    cocotb.start_soon(Clock(dut.pl_clk0, 10, unit="ns").start())
    dut.rstn.value = 0; dut.s_axi_aresetn.value = 0; dut.s2_axi_aresetn.value = 0
    dut.tclk.value = 1
    await ClockCycles(dut.pl_clk0, 10); await Timer(1, unit="ns")
    dut.rstn.value = 1; dut.s_axi_aresetn.value = 1; dut.s2_axi_aresetn.value = 1
    await ClockCycles(dut.clk_tx, 60)        # count-RAM zeroing + RX align warmup

    samples = stream_samples(EVENTS, warmup_cells=40, gap_cells=200)
    cocotb.start_soon(drive_samples(dut.clk_80m, dut.tclk, samples))
    await ClockCycles(dut.clk_80m, len(samples) + 2000)

    tclk_events = await pop_all(dut, "")
    aclk_events = await pop_all(dut, "s2_")

    got_tclk = [e for (e, _) in tclk_events]
    got_aclk = [e & 0xFF for (e, _) in aclk_events]
    assert got_tclk == EVENTS, f"readout#1 {got_tclk} != {EVENTS}"
    assert got_aclk == EVENTS, f"readout#2 {got_aclk} != {EVENTS}"

    # shared-timebase ordering for the first matched event
    assert aclk_events[0][1] >= tclk_events[0][1], "ACLK ts precedes TCLK ts"
    dut._log.info(f"chain OK: tclk={tclk_events} aclk={aclk_events}")
```

`tb/aclk_pipeline_chain/runner.py`: sources = every RTL module the chain instantiates (synchronizer, async_fifo, cdc_gray_count, global_timebase, serdec4_9MHz, TCLK_DESERIALIZER2, TCLK_RCV, aclk_readout_core, aclk_readout_axi, tclk_readout_top, aclk_tclk_encoder, CRC8_CALC, gearbox_96_to_16, GEARBOX_16_TO_96, ACLK_REV, aclk_gt_readout_top, tb top).

- [ ] **Step 3: Run to confirm it fails**

Run: `python -m pytest tb/aclk_pipeline_chain/runner.py -v`
Expected: FAIL at build (tb top references modules / `pfx` not yet wired) or at the assertions.

- [ ] **Step 4: Make it pass**

Build the tb top and runner as specified. Debug any clock-domain warmup/timing (extend the post-stimulus settle, widen `gap_cells`) until both readouts return the three events in order and the timestamp ordering holds.

- [ ] **Step 5: Run the whole sim suite for regressions**

Run: `python -m pytest tb/ -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tb/axi_lite_bfm.py tb/aclk_pipeline_chain/
git commit -m "test(pipeline): full pure-RTL chain sim (TCLK->ACLK, two readouts, shared timebase)"
```

---

## Task B1: integrated block-design top RTL

**Files:**
- Create: `rtl/aclk_pipeline_bd_top.v`
- Reference: `rtl/aclk_gt_selftest_bd_top.v` (GT + recovery FSM + SFP), `rtl/tclk_readout_bd_top.v` (TCLK MMCM wiring + AXI attributes)

**Interfaces:**
- Produces: `module aclk_pipeline_bd_top` with external ports (`tclk`, `gt_refclk_p/n`, `gt_rxp/n`, `gt_txp/n`, `freerun_50`, `rstn`, `sfp_tx_disable`, `sfp_tx_fault`, `sfp_rx_los`, `sfp_mod_abs`, `aclk_lite_out`, `clk_80m`, `clk_40m`, debug pins) and **two** AXI4-Lite slave interfaces declared via `X_INTERFACE_INFO`: bus `S_AXI` (TCLK readout) and bus `S_AXI2` (ACLK readout), sharing `s_axi_aclk`/`s_axi_aresetn` (`X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI:S_AXI2, ASSOCIATED_RESET s_axi_aresetn"`).

- [ ] **Step 1: Write the structural top**

Assemble (no new logic, all instances exist after A1-A4):
- `IBUFDS_GTE4` refclk buffer + `assign sfp_tx_disable = 1'b0;` (from selftest top).
- `aclkgt_gt u_gt` + the RX recovery FSM + DEBUG-word formation (copy from `aclk_gt_selftest_bd_top.v`), but `gtwiz_userdata_tx_in` is driven by the new encoder, not `aclk_gt_frame_gen`.
- one `TCLK_RCV u_tclk_rcv` on `tclk` (clk_40m/clk_80m), `TCLK_RATE=1'b1`.
- `global_timebase u_tb` (`ref_clk=s_axi_aclk`, `dst_clk_a=clk_40m -> ts_tclk`, `dst_clk_b=rx_usrclk2 -> ts_aclk`).
- `tclk_readout_top u_ro_tclk` (S_AXI, `ts_ext=ts_tclk`, `clk_40m`/`clk_80m`).
- `aclk_tclk_encoder u_enc` (`clk_tx=tx_usrclk2`, `tclk_data`/`tclk_davn` from `u_tclk_rcv`) -> `gen_data16`/`gen_k` -> GT TX.
- `aclk_gt_readout_top u_ro_aclk` (S_AXI2, `ts_ext=ts_aclk`, `rx_clk=rx_usrclk2`, `dec_rstn` from the recovery FSM as in the selftest top).
- `aclk_lite_bridge u_bridge` (`rx_clk=rx_usrclk2` from `ACLK_RCV` taps via `u_ro_aclk` debug outputs or a direct `ACLK_RCV` tap) + `aclk_lite_encoder u_lite` (`clk=clk_80m`) -> `aclk_lite_out`.
  - Note: `aclk_gt_readout_top` already instantiates `ACLK_RCV` internally. Expose its `aclk_event`/`aclk_data`/`aclk_valid` as debug outputs (add three output ports to `aclk_gt_readout_top.sv`) so the bridge can tap them, OR instantiate a second `ACLK_RCV` for the bridge. Prefer exposing the existing decode (one decoder). Add `output [15:0] dbg_aclk_event, output [63:0] dbg_aclk_data, output dbg_aclk_valid` to `aclk_gt_readout_top.sv` and wire the bridge to them.

- [ ] **Step 2: Lint/elaborate locally (no Vivado required for a syntax pass)**

Run: `iverilog -g2012 -t null -I rtl rtl/aclk_pipeline_bd_top.v rtl/aclk_gt/aclk_tclk_encoder.v rtl/global_timebase.v rtl/aclk_lite_bridge.v rtl/aclk_lite/aclk_lite_encoder.sv rtl/aclk_lite/tclk_readout_top.sv rtl/aclk_gt/aclk_gt_readout_top.sv rtl/aclk_readout/*.sv rtl/aclk_bridge/TCLK_RCV.v rtl/aclk_bridge/serdec4_9MHz.v rtl/aclk_bridge/TCLK_DESERIALIZER2.v rtl/aclk_bridge/ACLK_REV.v rtl/aclk_bridge/crc8_calc.v rtl/aclk_bridge/gearbox_96_to_16.v rtl/aclk_bridge/GEARBOX_16_TO_96.v rtl/cdc_gray_count.sv rtl/async_fifo.sv rtl/synchronizer.sv 2>&1 | head`
Expected: no errors (the `aclkgt_gt` GT IP black box will be an undefined module; provide an empty stub or skip the GT instance for the elaboration pass).
Note: the GT IP is not simulatable; for this elaboration pass either comment the GT instance or supply a one-line empty `module aclkgt_gt(...); endmodule` stub. The authoritative elaboration is the Vivado synth in Task B2.

- [ ] **Step 3: Commit**

```bash
git add rtl/aclk_pipeline_bd_top.v rtl/aclk_gt/aclk_gt_readout_top.sv
git commit -m "feat(pipeline): integrated BD top (1 TCLK_RCV, 2 readouts, encoder, GT duplex, ACLK-Lite mirror)"
```

---

## Task B2: build script + constraints (time-clean bitstream)

**Files:**
- Create: `vivado/build_aclk_pipeline.tcl`
- Create: `constraints/kr260_aclk_pipeline.xdc`
- Reference: `vivado/build_aclkgt_selftest.tcl`, `vivado/build_tclk.tcl`, `constraints/kr260_aclkgt.xdc`, `constraints/kr260_tclk.xdc`

- [ ] **Step 1: Write `build_aclk_pipeline.tcl`**

Base it on `build_aclkgt_selftest.tcl` (proj_name `aclk_pipeline`, `design_name uart_echo_bd`). Add the TCLK MMCM (clk_wiz: 80 + 40 MHz from pl_clk0, from `build_tclk.tcl`). RTL source list = the B1 elaboration list plus `tclk_readout_top.sv`, `aclk_tclk_encoder.v`, `aclk_lite_bridge.v`, `aclk_lite_encoder.sv`, `global_timebase.v`, and the `aclkgt_gt` GT IP (`vivado/ip/gen_aclkgt_gt.tcl`). SmartConnect `CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}`; wire `M00_AXI -> u_pipeline/S_AXI`, `M01_AXI -> u_pipeline/S_AXI2`; clocks/resets to both. Address map:
```tcl
assign_bd_address -offset 0x80000000 -range 0x10000 -target_address_space \
    [get_bd_addr_spaces zynq_ultra_ps_e_0/Data] [get_bd_addr_segs u_pipeline/S_AXI/Reg] -force
assign_bd_address -offset 0x80010000 -range 0x10000 -target_address_space \
    [get_bd_addr_spaces zynq_ultra_ps_e_0/Data] [get_bd_addr_segs u_pipeline/S_AXI2/Reg] -force
```
Keep `dcm_locked` tied high (xlconstant), the proven LPD reset workaround.

- [ ] **Step 2: Write `constraints/kr260_aclk_pipeline.xdc`**

Union of the TCLK + GT constraints, plus the ACLK-Lite output and the widened async groups:
```tcl
set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports tclk]
set_property PACKAGE_PIN R4 [get_ports gt_txp]
set_property PACKAGE_PIN R3 [get_ports gt_txn]
set_property PACKAGE_PIN T2 [get_ports gt_rxp]
set_property PACKAGE_PIN T1 [get_ports gt_rxn]
set_property PACKAGE_PIN Y6 [get_ports gt_refclk_p]
set_property PACKAGE_PIN Y5 [get_ports gt_refclk_n]
create_clock -period 6.400 -name gt_refclk [get_ports gt_refclk_p]
set_property -dict {PACKAGE_PIN Y10 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8} [get_ports sfp_tx_disable]
set_property -dict {PACKAGE_PIN A10 IOSTANDARD LVCMOS33} [get_ports sfp_tx_fault]
set_property -dict {PACKAGE_PIN J12 IOSTANDARD LVCMOS33} [get_ports sfp_rx_los]
set_property -dict {PACKAGE_PIN W10 IOSTANDARD LVCMOS33} [get_ports sfp_mod_abs]
set_property -dict {PACKAGE_PIN B10 IOSTANDARD LVCMOS33} [get_ports aclk_lite_out]
# async groups: PS clocks vs MMCM clocks vs GT recovered/refclk (union of the
# existing kr260_tclk and kr260_aclkgt groups; recovered RX clock included).
```
Carry over the exact `set_clock_groups -asynchronous` stanzas from `kr260_tclk.xdc` and `kr260_aclkgt*.xdc`, merged.

- [ ] **Step 3: Build**

Run: `.\hw.ps1 build -Tcl vivado\build_aclk_pipeline.tcl -Name aclk_pipeline`
(If the build dies on a Vivado/Defender flake, the user runs the admin `Set-MpPreference -DisableRealtimeMonitoring $true` per the handoff, or simply retries. Run long builds in the controller session, not a subagent.)

- [ ] **Step 4: Confirm timing is clean**

Run: `grep -l "All user specified timing constraints are met" build/kria/aclk_pipeline/aclk_pipeline.runs/impl_1/*_timing_summary_routed.rpt`
Expected: the routed timing-summary report matches. If not, inspect failing paths (likely a missing async group between a new CDC pair) and fix the XDC.

- [ ] **Step 5: Commit**

```bash
git add vivado/build_aclk_pipeline.tcl constraints/kr260_aclk_pipeline.xdc
git commit -m "build(pipeline): integrated bitstream build + constraints (2-slave AXI, ACLK-Lite on B10)"
```

---

## Task B3: device-tree overlay (two UIO nodes)

**Files:**
- Create: `deploy/aclk_pipeline.dts`
- Reference: `deploy/uart_echo.dts`

- [ ] **Step 1: Write the overlay with two `generic-uio` nodes**

```dts
/dts-v1/;
/plugin/;
/ {
    fragment@0 {
        target = <&fpga_full>;
        __overlay__ {
            firmware-name = "uart_echo_bd_wrapper.bit.bin";
            resets = <&zynqmp_reset 0x74>, <&zynqmp_reset 0x75>,
                     <&zynqmp_reset 0x76>, <&zynqmp_reset 0x77>;
        };
    };
    fragment@1 {
        target = <&amba>;
        __overlay__ {
            tclk_readout_axi: tclk_readout@80000000 {
                compatible = "generic-uio";
                reg = <0x0 0x80000000 0x0 0x10000>;
            };
            aclk_readout_axi: aclk_readout@80010000 {
                compatible = "generic-uio";
                reg = <0x0 0x80010000 0x0 0x10000>;
            };
        };
    };
};
```

- [ ] **Step 2: Compile the overlay**

Run (on the build host or board): `dtc -@ -O dtb -o aclk_pipeline.dtbo deploy/aclk_pipeline.dts`
Expected: produces `aclk_pipeline.dtbo` with no errors.

- [ ] **Step 3: Commit**

```bash
git add deploy/aclk_pipeline.dts
git commit -m "deploy(pipeline): two-UIO device-tree overlay (uio4 TCLK, uio5 ACLK)"
```

---

## Task B4: hardware bring-up (user-run, incremental)

**Files:** none (validation). The user runs each step on the board and pastes output; the assistant interprets `[stats]` / DEBUG words and decides the next step. Each sub-step is a gate.

- [ ] **B4.0 Load** the bitstream: `md5sum` the `.bit.bin`, `sudo xmutil unloadapp`, `sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o aclk_pipeline.dtbo`. Confirm two UIO nodes: `ls -l /dev/uio*` and `cat /sys/class/uio/uio*/name`. Identify which index is `0x8000_0000` (TCLK) vs `0x8001_0000` (ACLK).

- [ ] **B4.1 TCLK readout:** connect the fiber loopback (SFP TX port -> own SFP RX port) and feed real TCLK to H12. Run `sudo python3 deploy/tclk_read.py /dev/uioN` (TCLK node). Expect `lock=1`, EVENT climbing, decoded event codes matching the live TCLK.

- [ ] **B4.2 ACLK round-trip:** run `sudo python3 deploy/aclk_read.py /dev/uioM` (ACLK node, `0x8001_0000`). Expect `rx_los=0`, `rcv_aligned=1`, EVENT climbing, decoded `EVENT={0x00,code}` / `DATA={0,count}` matching the TCLK events, `disperr`/`recover` not climbing.

- [ ] **B4.3 Shared timestamps:** capture matched events from both nodes and confirm `ts(ACLK) - ts(TCLK)` is stable and positive (the loop latency). (Add a tiny PS helper later if needed; for bring-up, eyeball the TS fields.)

- [ ] **B4.4 ACLK-Lite probe:** scope Pmod1 pin **B10** and confirm the ACLK-Lite Manchester waveform of the decoded-back events (10 MHz cells, 12-byte frames). Optionally loop B10 into a second board's `clk_rcv` (`clk_read.py`) to confirm decode.

- [ ] **B4.5 Update the handoff doc** (`docs/aclkgt-handoff.md` or a new `docs/pipeline-handoff.md`) with the bring-up result and commit.

---

## Self-review

Spec coverage:
- TCLK decode + readout #1 -> A3 (ext-ts), A5 (chain), B1/B2 (integration), B4.1.
- TCLK->ACLK encoder (ACLK_DATA_SOURCE format) -> A1, B1.
- SFP duplex + fiber loop -> B1 (reuses selftest GT), B4.2.
- ACLK decode + readout #2 -> A5, B1, B4.2.
- Shared timebase -> A2, A3, A5, B4.3.
- ACLK-back -> ACLK-Lite on Pmod -> A4, B1, B4.4.
- Two UIO nodes -> B2 (address map), B3 (overlay), B4.0.
- Redis -> intentionally out of scope (no task).

Type consistency check: `aclk_tclk_encoder` ports (`clk_tx`, `rstn_tx`, `tclk_data`, `tclk_davn`, `data16`, `k_out`, `marker`) are identical in A1's tb top, B1, and B2's source list. `global_timebase` ports (`ref_clk`, `ref_rstn`, `dst_clk_a`, `ts_a`, `dst_clk_b`, `ts_b`) are identical in A2 and B1. `USE_EXT_TS`/`ts_ext` names are identical across A3's four modified files and B1. `aclk_lite_bridge` ports are identical in A4 and B1. AXI bus names `S_AXI`/`S_AXI2` are consistent between B1 and B2.

Open confirmations folded into tasks: TCLK stimulus = `tb/tclk_tx_model.py` (A5); `CRC8_CALC`/`GEARBOX_96_TO_16` casing confirmed (uppercase); exposing the existing `ACLK_RCV` decode for the bridge (B1 Step 1 note).
