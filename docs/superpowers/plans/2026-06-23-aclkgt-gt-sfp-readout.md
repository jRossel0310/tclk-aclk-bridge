# Gigabit ACLK over GT/SFP: two-board readout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Receive the gigabit ACLK 8b/10b serial link on the KR260's GTH/SFP, decode it with the inherited `ACLK_RCV`, and stream events to the PS over AXI4-Lite — verified board-to-board with a two-KR260 generator/receiver pair after a single-board GT loopback milestone.

**Architecture:** A purely-RTL, Icarus-simmable readout brain (`ACLK_RCV` → adapter → shared `aclk_readout_axi`) is built and sim-validated first (Phase A). Then it is wrapped with a regenerated GTH GT Wizard core into three Vivado build targets — loopback (M0), receiver (M1), generator (M2) — each a thin BD-top mirroring the existing `aclk`/`tclk` builds (Phase B, hardware-verified).

**Tech Stack:** SystemVerilog/Verilog RTL; cocotb 2.0.1 on Icarus (`sim.ps1`); Vivado 2024.2 block-design tcl (`hw.ps1` + bootgen); AMD GTH UltraScale+ Transceiver Wizard IP; Python PS reader over UIO.

## Global Constraints

- Target part: `xck26-sfvc784-2LV-c` (KR260 / K26 SOM). Board part filter `*kr260*`.
- BD design name stays `uart_echo_bd` for every target (bitstream `uart_echo_bd_wrapper.bit.bin`, overlay-name compatibility). Build targets differ only by `-Name`.
- AXI transport: `M_AXI_HPM0_LPD` @ base `0x8000_0000` via manual SmartConnect (NOT `apply_bd_automation`); reset `dcm_locked` tied high with an `xlconstant` (gating on MMCM lock wedges the LPD bus).
- Readout register map is 16-byte-spaced (KR260 LPD quirk): `STATUS 0x00, EVENT 0x10, DATA_HI 0x20, DATA_LO 0x30, TS_HI 0x40, TS_LO 0x50, POP 0x60, EVENT_COUNT 0x70, NULL_COUNT 0x80, ERROR_COUNT 0x90, DEBUG 0xA0, HEARTBEAT 0xB0, LOCK 0xC0, FILTER_CFG 0xD0, FILTERED_COUNT 0xE0`.
- Naming: gigabit-ACLK-over-GT artifacts use the `aclkgt` prefix to avoid collision with the ACLK-Lite-over-pin `aclk`/`aclkgen` builds.
- Event semantics (gigabit ACLK): every aligned 96-bit packet carries EVENT[15:0] + DATA[63:0]; `flags = 16'h0001` (has_data=1, is_tclk=0); `DROP_NULL=1` (drop+count `0xFF`-low-byte nulls); CRC-8 poly `0x2F`, decode gates on CRC==0.
- cocotb tests pinned to Icarus; every test emits a matplotlib plot to `sim_build/<module>/plots/`. The GT Wizard IP is a Vivado primitive and does NOT simulate in Icarus — GT integration is verified on hardware only.
- Style: no em dashes in code comments, docs, or commit messages.

---

## Phase A — Sim-validated RTL (executable now, Icarus)

### Task 1: Research — pin the GT hardware facts

This task produces the concrete values every Phase B task consumes. It is investigation, not code; its deliverable is a committed notes file with exact values (or, where a value is still unknown, the seed value to build against plus the open question).

**Files:**
- Create: `docs/aclkgt-hardware-facts.md`

**Interfaces:**
- Produces (named values consumed by Tasks 6, 9, 10): `GT_QUAD` (e.g. `X0Y1`), `GT_LANE` (e.g. `X0Y6`), `SFP_RX_P/SFP_RX_N`, `SFP_TX_P/SFP_TX_N` package pins, `REFCLK_P/REFCLK_N` package pins, `REFCLK_FREQ_MHZ`, `REFCLK_SOURCE` (onboard programmable / fixed / external), `LINE_RATE_GBPS` (real gigabit-ACLK rate), `USERCLK_MHZ` (= LINE_RATE_GBPS*1e3/20 with 16-bit + 8b10b).

- [ ] **Step 1: Record what is already known (seed values).** Write `docs/aclkgt-hardware-facts.md` capturing the Aurora-on-KR260 reference seed (verify-before-trust): `GT_QUAD=X0Y1`, `GT_LANE=X0Y6`, `SFP_RX = T2/T1`, `SFP_TX = R4/R3`, `REFCLK = Y6/Y5`, `REFCLK_FREQ_MHZ=156.25`. Note the inferred `LINE_RATE_GBPS≈1.2` (from Evan's 60 MHz x 16-bit x 10/8) and that decision #4 of the spec wants the *real* rate, confirmed.

- [ ] **Step 2: Resolve the three open facts.** From the KR260 carrier-card schematic / UG1091 / board XDC: confirm the SFP GTH quad/lane + the four differential pin pairs, and the refclk net source + frequency (is it a programmable Si5332 or a fixed oscillator?). From Fermilab/Evan (or the original `gtwizard_ultrascale_0` `.xci` if recoverable): confirm `LINE_RATE_GBPS`. Record each as resolved or still-open with its fallback.

- [ ] **Step 3: Decide the build-against values.** Pick the values Phase B builds against now: if the refclk cannot divide cleanly to the exact real rate, record the chosen near-rate for the two-board milestone AND the real-rate target for the eventual live-fiber bitstream. Write a one-line "build-against" summary table at the top of the file.

- [ ] **Step 4: Commit.**
```bash
git add docs/aclkgt-hardware-facts.md
git commit -m "docs(aclkgt): record GT/SFP hardware facts and build-against values"
```

> Phase B Vivado tasks are GATED on this file having resolved (or explicitly seed-defaulted) values. Phase A (Tasks 2-5) does NOT depend on it and can proceed immediately.

---

### Task 2: `aclk_gt_readout_top.sv` — the RX readout brain (ACLK_RCV + adapter + readout)

The Icarus-simmable core: it presents the GT's 16-bit + K interface as input ports (the GT itself is added later in Phase B), wires `ACLK_RCV` through the trivial adapter to the shared `aclk_readout_axi`. Directly mirrors `rtl/aclk_lite/aclk_lite_readout_top.sv`.

**Files:**
- Create: `rtl/aclk_gt/aclk_gt_readout_top.sv`
- Create: `tb/aclkgt_readout/tb_aclkgt_readout_top.sv`
- Create: `tb/aclkgt_readout/test_aclkgt_readout.py`
- Create: `tb/aclkgt_readout/runner.py`

**Interfaces:**
- Consumes: `ACLK_RCV` (`rtl/aclk_bridge/ACLK_REV.v`: `RESETn, CLK1, DATA_FROM_XCVR[15:0], K_FROM_XCVR[1:0] -> ACLK_EVENT[15:0], ACLK_DATA[63:0], ACLK_VALID, ACLK_ERROR, RX_ALIGNED_OUT, DIAG[3:0]`); `aclk_readout_axi` (params `ADDR_WIDTH, AXI_ADDR_W, DROP_NULL`; event ports `rx_clk, rx_rstn, pps, aclk_valid, aclk_event[15:0], aclk_data[63:0], flags[15:0], aclk_error, dropped_null, dbg_word[31:0], mmcm_locked, dbg_hb` + AXI slave); shared models `tb/aclk_tx_model.py` (`build_frame`, `frame_to_words`, `stream_frames`), `tb/axi_lite_bfm.py` (`axi_read`, `axi_write`).
- Produces: module `aclk_gt_readout_top` with ports `rx_clk, rx_rstn, pps, data_from_xcvr[15:0], k_from_xcvr[1:0], mmcm_locked, rx_aligned (output), dbg_hb, dropped_null` + the AXI4-Lite slave (`AXI_ADDR_W=8`). Consumed by Tasks 6/9 (the GT tops).

- [ ] **Step 1: Write the failing full-chain test.**

Create `tb/aclkgt_readout/test_aclkgt_readout.py`:
```python
"""Full-chain sim for aclk_gt_readout_top: drive ACLK_RCV's 16-bit + K xcvr
interface with the gigabit-ACLK TX model, read decoded events back over AXI-Lite,
and check event/data/flags/timestamps/counts plus the null-drop and bad-CRC paths.
The GT transceiver is NOT in this DUT (it has no Icarus model); the tb feeds the
xcvr word stream the GT would otherwise recover."""

import sys, warnings
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # shared tb/*.py
from aclk_tx_model import build_frame, frame_to_words, stream_frames, crc8  # noqa: E402
from axi_lite_bfm import axi_read, axi_write, _b                            # noqa: E402

RX_PERIOD_NS, AXI_PERIOD_NS = 16, 10
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP = 0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60
EVENT_COUNT, NULL_COUNT, ERROR_COUNT = 0x70, 0x80, 0x90
MASK64 = (1 << 64) - 1
NULL_EVENT = (0xFFFF, MASK64)
FLAG_HAS_DATA, FLAG_IS_TCLK = 0x1, 0x2

async def _reset(dut):
    dut.DATA_FROM_XCVR.value = 0
    dut.K_FROM_XCVR.value = 0
    dut.rx_rstn.value = 0
    dut.s_axi_aresetn.value = 0
    dut.pps.value = 0
    dut.mmcm_locked.value = 1
    for s in ("s_axi_arvalid", "s_axi_rready", "s_axi_awvalid", "s_axi_wvalid", "s_axi_bready"):
        getattr(dut, s).value = 0
    await ClockCycles(dut.CLK1, 5)
    await ClockCycles(dut.s_axi_aclk, 5)
    await Timer(1, unit="ns")
    dut.rx_rstn.value = 1
    dut.s_axi_aresetn.value = 1
    await RisingEdge(dut.CLK1)

async def _idle_carrier(dut, stop):
    while not stop.get("done"):
        await stream_frames(dut, [NULL_EVENT], repeat=1)

async def axi_read_event(dut):
    ev_reg = await axi_read(dut, EVENT)
    event, flags = ev_reg & 0xFFFF, (ev_reg >> 16) & 0xFFFF
    dhi, dlo = await axi_read(dut, DATA_HI), await axi_read(dut, DATA_LO)
    thi, tlo = await axi_read(dut, TS_HI), await axi_read(dut, TS_LO)
    await axi_write(dut, POP)
    return event, flags, (dhi << 32) | dlo, (thi << 32) | tlo

@cocotb.test()
async def test_gt_readout_chain(dut):
    cocotb.start_soon(Clock(dut.CLK1, RX_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, AXI_PERIOD_NS, unit="ns").start())
    await _reset(dut)

    real = [
        (0x0001, 0x1111222233334444),
        (0x00A5, 0xAAAABBBBCCCCDDDD),
        (0x1000, 0x0123456789ABCDEF),
        (0x3C00, 0xFEDCBA9876543210),
    ]
    seq = [real[0], NULL_EVENT, real[1], real[2], NULL_EVENT, real[3]]
    nonnull = [f for f in seq if (f[0] & 0xFF) != 0xFF]

    await stream_frames(dut, seq, repeat=10)
    stop = {"done": False}
    carrier = cocotb.start_soon(_idle_carrier(dut, stop))
    await ClockCycles(dut.CLK1, 8)
    await ClockCycles(dut.s_axi_aclk, 6)

    assert int(dut.rx_aligned.value) == 1, "RX never aligned"
    assert (await axi_read(dut, STATUS)) >> 1 & 1 == 0, "overflow: events dropped"

    collected = []
    while True:
        if (await axi_read(dut, STATUS)) & 0x1:
            break
        collected.append(await axi_read_event(dut))
    stop["done"] = True

    assert collected, "no events read over AXI"
    ed = [(ev, da) for (ev, fl, da, ts) in collected]
    for ev, fl, da, ts in collected:
        assert (ev & 0xFF) != 0xFF, f"null leaked: 0x{ev:04X}"
        assert fl & FLAG_HAS_DATA, f"has_data not set for 0x{ev:04X}"
        assert not (fl & FLAG_IS_TCLK), f"is_tclk wrongly set for 0x{ev:04X}"
    start = nonnull.index(ed[0])
    for i, got in enumerate(ed):
        exp = nonnull[(start + i) % len(nonnull)]
        assert got == exp, f"order/data break #{i}: {got} != {exp}"
    tss = [ts for (ev, fl, da, ts) in collected]
    for i in range(1, len(tss)):
        assert tss[i] > tss[i - 1], f"timestamp not monotonic at #{i}"

    assert await axi_read(dut, EVENT_COUNT) == len(collected), "EVENT_COUNT mismatch"
    assert await axi_read(dut, NULL_COUNT) > 0, "NULL_COUNT did not register dropped nulls"
    assert await axi_read(dut, ERROR_COUNT) == 0, "ERROR_COUNT on a clean stream"
    dut._log.info(f"gt readout OK: {len(collected)} events in order, flags+ts correct")
```

- [ ] **Step 2: Write the tb top that exposes the names the model drives.**

Create `tb/aclkgt_readout/tb_aclkgt_readout_top.sv` (presents `DATA_FROM_XCVR`/`K_FROM_XCVR` for `stream_frames`, `CLK1`/`rx_aligned` for the harness, plus the AXI bus). It instantiates the DUT and connects the DUT's lowercase xcvr ports:
```systemverilog
`timescale 1ns/1ps
module tb_aclkgt_readout_top (
    input  wire        CLK1,
    input  wire        rx_rstn,
    input  wire        pps,
    input  wire [15:0] DATA_FROM_XCVR,
    input  wire [1:0]  K_FROM_XCVR,
    input  wire        mmcm_locked,
    output wire        rx_aligned,
    output wire        dropped_null,
    input  wire        s_axi_aclk,
    input  wire        s_axi_aresetn,
    input  wire [7:0]  s_axi_awaddr,  input wire s_axi_awvalid, output wire s_axi_awready,
    input  wire [31:0] s_axi_wdata,   input wire [3:0] s_axi_wstrb, input wire s_axi_wvalid, output wire s_axi_wready,
    output wire [1:0]  s_axi_bresp,   output wire s_axi_bvalid,  input wire s_axi_bready,
    input  wire [7:0]  s_axi_araddr,  input wire s_axi_arvalid,  output wire s_axi_arready,
    output wire [31:0] s_axi_rdata,   output wire [1:0] s_axi_rresp, output wire s_axi_rvalid, input wire s_axi_rready
);
    aclk_gt_readout_top #(.ADDR_WIDTH(6), .AXI_ADDR_W(8)) dut (
        .rx_clk(CLK1), .rx_rstn(rx_rstn), .pps(pps),
        .data_from_xcvr(DATA_FROM_XCVR), .k_from_xcvr(K_FROM_XCVR),
        .mmcm_locked(mmcm_locked), .rx_aligned(rx_aligned), .dbg_hb(), .dropped_null(dropped_null),
        .s_axi_aclk(s_axi_aclk), .s_axi_aresetn(s_axi_aresetn),
        .s_axi_awaddr(s_axi_awaddr), .s_axi_awvalid(s_axi_awvalid), .s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata), .s_axi_wstrb(s_axi_wstrb), .s_axi_wvalid(s_axi_wvalid), .s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp), .s_axi_bvalid(s_axi_bvalid), .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr), .s_axi_arvalid(s_axi_arvalid), .s_axi_arready(s_axi_arready),
        .s_axi_rdata(s_axi_rdata), .s_axi_rresp(s_axi_rresp), .s_axi_rvalid(s_axi_rvalid), .s_axi_rready(s_axi_rready)
    );
endmodule
```

- [ ] **Step 3: Write the runner.**

Create `tb/aclkgt_readout/runner.py`:
```python
import os, sys
from pathlib import Path
from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")
TB_DIR = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR = PROJ_DIR / "rtl"
BUILD = PROJ_DIR / "sim_build" / "aclkgt_readout"
sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))
_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")

def test_aclkgt_readout():
    runner = get_runner(SIM)
    runner.build(
        sources=[
            RTL_DIR / "aclk_bridge" / "crc8_calc.v",
            RTL_DIR / "aclk_bridge" / "GEARBOX_16_TO_96.v",
            RTL_DIR / "aclk_bridge" / "ACLK_REV.v",
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "async_fifo.sv",
            RTL_DIR / "cdc_gray_count.sv",
            RTL_DIR / "aclk_readout" / "aclk_readout_core.sv",
            RTL_DIR / "aclk_readout" / "aclk_readout_axi.sv",
            RTL_DIR / "aclk_gt" / "aclk_gt_readout_top.sv",
            TB_DIR / "tb_aclkgt_readout_top.sv",
        ],
        hdl_toplevel="tb_aclkgt_readout_top",
        build_dir=BUILD, timescale=("1ns", "1ps"), waves=True, always=True,
    )
    runner.test(hdl_toplevel="tb_aclkgt_readout_top",
                test_module="test_aclkgt_readout", build_dir=BUILD, waves=True)

if __name__ == "__main__":
    test_aclkgt_readout()
```

- [ ] **Step 4: Run the test to verify it fails (DUT missing).**

Run: `.\sim.ps1 run -Module aclkgt_readout`
Expected: FAIL — elaboration error, `aclk_gt_readout_top` not found.

- [ ] **Step 5: Implement `aclk_gt_readout_top.sv`.**

Create `rtl/aclk_gt/aclk_gt_readout_top.sv`:
```systemverilog
// rtl/aclk_gt/aclk_gt_readout_top.sv
//
// Gigabit-ACLK RX readout brain: the inherited ACLK_RCV decoder (fed by a GT
// transceiver's 16-bit + K word stream on the recovered RX clock) through a
// trivial adapter into the shared decoder-agnostic aclk_readout_axi. Mirrors
// rtl/aclk_lite/aclk_lite_readout_top.sv. The GT transceiver itself lives in the
// Phase B integration top (aclk_gt_*_top); this module is pure RTL so it sims in
// Icarus exactly as the ACLK-Lite readout top does.
//
// Adapter: every aligned 96-bit packet carries EVENT[15:0] + DATA[63:0], so
// flags = has_data=1, is_tclk=0; DROP_NULL=1 drops the 0xFF-low-byte nulls.
// ACLK_VALID / ACLK_ERROR are one-cycle pulses (CRC-gated), so ACLK_ERROR feeds
// the readout error counter directly (no sticky edge-detect, unlike TCLK PERR).

`timescale 1ns / 1ps

module aclk_gt_readout_top #(
    parameter int ADDR_WIDTH = 6,
    parameter int AXI_ADDR_W = 8
) (
    // ---- recovered-RX (GT user) domain ----
    input  logic        rx_clk,
    input  logic        rx_rstn,
    input  logic        pps,
    input  logic [15:0] data_from_xcvr,
    input  logic [1:0]  k_from_xcvr,
    input  logic        mmcm_locked,       // GT/MMCM locked (async) -> AXI 0xC0 LOCK
    output logic        rx_aligned,         // ACLK_RCV comma alignment (debug/bring-up)
    output logic        dbg_hb,
    output logic        dropped_null,

    // ---- AXI4-Lite slave (PS clock) ----
    input  logic                   s_axi_aclk,
    input  logic                   s_axi_aresetn,
    input  logic [AXI_ADDR_W-1:0]  s_axi_awaddr,
    input  logic                   s_axi_awvalid,
    output logic                   s_axi_awready,
    input  logic [31:0]            s_axi_wdata,
    input  logic [3:0]             s_axi_wstrb,
    input  logic                   s_axi_wvalid,
    output logic                   s_axi_wready,
    output logic [1:0]             s_axi_bresp,
    output logic                   s_axi_bvalid,
    input  logic                   s_axi_bready,
    input  logic [AXI_ADDR_W-1:0]  s_axi_araddr,
    input  logic                   s_axi_arvalid,
    output logic                   s_axi_arready,
    output logic [31:0]            s_axi_rdata,
    output logic [1:0]             s_axi_rresp,
    output logic                   s_axi_rvalid,
    input  logic                   s_axi_rready
);
    // ---- inherited gigabit-ACLK decoder ----
    wire [15:0] aclk_event;
    wire [63:0] aclk_data;
    wire        aclk_valid;
    wire        aclk_error;
    wire [3:0]  diag;

    ACLK_RCV u_rcv (
        .RESETn         (rx_rstn),
        .CLK1           (rx_clk),
        .DATA_FROM_XCVR (data_from_xcvr),
        .K_FROM_XCVR    (k_from_xcvr),
        .ACLK_EVENT     (aclk_event),
        .ACLK_DATA      (aclk_data),
        .ACLK_VALID     (aclk_valid),
        .ACLK_ERROR     (aclk_error),
        .RX_ALIGNED_OUT (rx_aligned),
        .DIAG           (diag)
    );

    // ---- GT/decoder link-health diagnostic word (-> AXI 0xA0 DEBUG) ----
    // {is_aligned, comma(diag[1]), 0, frame_count[28:0]} synced into the AXI domain.
    logic [28:0] frame_count;
    always_ff @(posedge rx_clk or negedge rx_rstn) begin
        if (!rx_rstn) frame_count <= '0;
        else if (aclk_valid) frame_count <= frame_count + 1'b1;
    end
    wire [29:0] frame_count_dst;
    cdc_gray_count #(.W(30)) u_cnt_frames (
        .src_clk(rx_clk), .src_rstn(rx_rstn), .incr(aclk_valid),
        .dst_clk(s_axi_aclk), .count_dst(frame_count_dst));
    logic algn_m, algn_s;
    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin algn_m <= 1'b0; algn_s <= 1'b0; end
        else begin algn_m <= rx_aligned; algn_s <= algn_m; end
    end
    wire [31:0] aclkgt_dbg_word = {algn_s, 1'b0, frame_count_dst};

    // ---- adapter: every packet carries 64-bit data ----
    wire [15:0] adapt_flags = 16'h0001;   // bit0 has_data=1, bit1 is_tclk=0

    aclk_readout_axi #(
        .ADDR_WIDTH (ADDR_WIDTH),
        .AXI_ADDR_W (AXI_ADDR_W),
        .DROP_NULL  (1'b1)
    ) u_axi (
        .rx_clk        (rx_clk),
        .rx_rstn       (rx_rstn),
        .pps           (pps),
        .aclk_valid    (aclk_valid),
        .aclk_event    (aclk_event),
        .aclk_data     (aclk_data),
        .flags         (adapt_flags),
        .aclk_error    (aclk_error),
        .dropped_null  (dropped_null),
        .dbg_word      (aclkgt_dbg_word),
        .mmcm_locked   (mmcm_locked),
        .dbg_hb        (dbg_hb),
        .s_axi_aclk    (s_axi_aclk),
        .s_axi_aresetn (s_axi_aresetn),
        .s_axi_awaddr  (s_axi_awaddr),
        .s_axi_awvalid (s_axi_awvalid),
        .s_axi_awready (s_axi_awready),
        .s_axi_wdata   (s_axi_wdata),
        .s_axi_wstrb   (s_axi_wstrb),
        .s_axi_wvalid  (s_axi_wvalid),
        .s_axi_wready  (s_axi_wready),
        .s_axi_bresp   (s_axi_bresp),
        .s_axi_bvalid  (s_axi_bvalid),
        .s_axi_bready  (s_axi_bready),
        .s_axi_araddr  (s_axi_araddr),
        .s_axi_arvalid (s_axi_arvalid),
        .s_axi_arready (s_axi_arready),
        .s_axi_rdata   (s_axi_rdata),
        .s_axi_rresp   (s_axi_rresp),
        .s_axi_rvalid  (s_axi_rvalid),
        .s_axi_rready  (s_axi_rready)
    );
endmodule
```

- [ ] **Step 6: Run the test to verify it passes.**

Run: `.\sim.ps1 run -Module aclkgt_readout`
Expected: PASS — `gt readout OK: N events in order, flags+ts correct`. A plot is written under `sim_build/aclkgt_readout/plots/` (add the same `_save_plot` helper used by `tb/aclk_readout_axi/test_aclk_readout_axi.py` if a plot file is required by review; copy that function verbatim and call it before the final log line).

- [ ] **Step 7: Commit.**
```bash
git add rtl/aclk_gt/aclk_gt_readout_top.sv tb/aclkgt_readout/
git commit -m "feat(aclkgt): RX readout top (ACLK_RCV + adapter + AXI) + full-chain sim"
```

---

### Task 3: bad-CRC error path test

Adds the error-path coverage to the same testbench (the readout's `ERROR_COUNT` must register exactly one bad-CRC frame, and the bad frame must not be read out as an event).

**Files:**
- Modify: `tb/aclkgt_readout/test_aclkgt_readout.py` (add one test)

**Interfaces:**
- Consumes: `stream_frames(dut, frames, repeat, corrupt_at=N)` from `tb/aclk_tx_model.py` (flips one payload bit on global frame index N).

- [ ] **Step 1: Write the failing test.**

Append to `tb/aclkgt_readout/test_aclkgt_readout.py`:
```python
@cocotb.test()
async def test_gt_readout_bad_crc(dut):
    """A single corrupted frame raises exactly one ERROR_COUNT and is never read
    out as an event; clean frames around it still decode."""
    cocotb.start_soon(Clock(dut.CLK1, RX_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, AXI_PERIOD_NS, unit="ns").start())
    await _reset(dut)

    good = (0x0042, 0xDEADBEEFCAFEF00D)
    # Align first with clean frames, baseline ERROR_COUNT, then inject one corrupt frame.
    await stream_frames(dut, [good], repeat=20)
    await ClockCycles(dut.CLK1, 8)
    await ClockCycles(dut.s_axi_aclk, 6)
    assert int(dut.rx_aligned.value) == 1, "RX never aligned"
    err0 = await axi_read(dut, ERROR_COUNT)

    # corrupt_at counts frames from the start of THIS stream_frames call.
    await stream_frames(dut, [good], repeat=4, corrupt_at=1)
    stop = {"done": False}
    cocotb.start_soon(_idle_carrier(dut, stop))
    await ClockCycles(dut.CLK1, 8)
    await ClockCycles(dut.s_axi_aclk, 6)

    # drain
    collected = []
    while True:
        if (await axi_read(dut, STATUS)) & 0x1:
            break
        collected.append(await axi_read_event(dut))
    stop["done"] = True

    err1 = await axi_read(dut, ERROR_COUNT)
    assert err1 - err0 == 1, f"expected exactly 1 bad-CRC error, got {err1 - err0}"
    for ev, fl, da, ts in collected:
        assert (ev, da) == good, f"corrupt frame leaked as event: 0x{ev:04X}"
    dut._log.info(f"bad-CRC path OK: ERROR_COUNT delta=1, {len(collected)} clean events read")
```

- [ ] **Step 2: Run to verify it passes.**

Run: `.\sim.ps1 run -Module aclkgt_readout`
Expected: PASS for both tests (TESTS=2 PASS=2).

- [ ] **Step 3: Commit.**
```bash
git add tb/aclkgt_readout/test_aclkgt_readout.py
git commit -m "test(aclkgt): bad-CRC error-path coverage for the GT readout chain"
```

---

### Task 4: `aclk_gt_frame_gen.v` — on-board frame generator (replaces aclk_data_source)

A small, Icarus-simmable, IP-free transmitter: it cycles a compiled-in event/data timeline, builds the 96-bit frame `{0xBC, EVENT[15:0], DATA[63:0], CRC8}` (CRC via the inherited `crc8_calc`), and feeds `gearbox_96_to_16` to emit the 16-bit + K word stream a GT TX consumes. This stands in for `aclk_data_source`, which is rejected here because it pulls in a `blk_mem_gen_0` BRAM IP and a `TCLK_RCV` (not Icarus-simmable, not a clean generator).

**Files:**
- Create: `rtl/aclk_gt/aclk_gt_frame_gen.v`
- Create: `tb/aclkgt_gen/test_aclkgt_gen.py`
- Create: `tb/aclkgt_gen/runner.py`

**Interfaces:**
- Consumes: `CRC8_CALC` (`CLK, RESETn, CALC, DATA[87:0] -> CRC[7:0], CRC_VALID`); `GEARBOX_96_TO_16` (`CLK1, RESETn, DATA96[95:0], K_IN[11:0], DATA96_VALID -> DATA16[15:0], K_OUT[1:0]`). Python `crc8`, `build_frame` from `tb/aclk_tx_model.py` as the golden reference.
- Produces: module `aclk_gt_frame_gen #(parameter N_EVENTS) (input CLK1, RESETn, output [15:0] DATA16, output [1:0] K_OUT, output MARKER)`. The compiled-in timeline is `N_EVENTS` (event,data) pairs cycled continuously, one 96-bit frame at a time, gearboxed to 6 words each. Consumed by Tasks 6/10 (loopback + generator tops).

- [ ] **Step 1: Write the failing test (gen output decodes via the model).**

Create `tb/aclkgt_gen/test_aclkgt_gen.py`:
```python
"""Capture aclk_gt_frame_gen's 16-bit + K word stream and confirm each group of
6 words reassembles (via the gigabit-ACLK model's inverse) into the compiled-in
events with a correct CRC. Proves the RTL generator and the Python golden model
agree on framing + CRC before any hardware."""
import sys
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aclk_tx_model import build_frame, frame_to_words  # noqa: E402

# Must match the RTL generator's compiled-in timeline (Step 3).
TIMELINE = [(0x0001, 0x1111222233334444), (0x00A5, 0xAAAABBBBCCCCDDDD),
            (0x1000, 0x0123456789ABCDEF)]
EXPECTED_WORDS = []
for ev, da in TIMELINE:
    EXPECTED_WORDS += frame_to_words(build_frame(ev, da))   # list of (word16, k2)

@cocotb.test()
async def test_frame_gen_matches_model(dut):
    cocotb.start_soon(Clock(dut.CLK1, 16, unit="ns").start())
    dut.RESETn.value = 0
    await ClockCycles(dut.CLK1, 5)
    await Timer(1, unit="ns")
    dut.RESETn.value = 1

    # Capture enough words to cover two full passes of the timeline, then phase-align.
    need = len(EXPECTED_WORDS) * 2
    got = []
    while len(got) < need + len(EXPECTED_WORDS):
        await RisingEdge(dut.CLK1)
        await Timer(1, unit="ns")
        got.append((int(dut.DATA16.value), int(dut.K_OUT.value)))

    # Find the comma word (K=0b01, low byte 0xBC) to phase-align, then compare a cycle.
    starts = [i for i, (w, k) in enumerate(got) if k == 0b01 and (w & 0xFF) == 0xBC]
    assert starts, "no comma word (K=01, low byte 0xBC) ever emitted"
    s = starts[0]
    window = got[s:s + len(EXPECTED_WORDS)]
    # EXPECTED_WORDS starts at the comma word of TIMELINE[0].
    assert window == EXPECTED_WORDS, (
        f"gen word stream != model:\n got={['(0x%04X,%d)'%(w,k) for w,k in window]}\n"
        f" exp={['(0x%04X,%d)'%(w,k) for w,k in EXPECTED_WORDS]}")
    dut._log.info(f"frame_gen matches model over {len(EXPECTED_WORDS)} words")
```

- [ ] **Step 2: Write the runner.**

Create `tb/aclkgt_gen/runner.py` (same shape as Task 2's runner; sources below, toplevel `aclk_gt_frame_gen`):
```python
import os, sys
from pathlib import Path
from cocotb_tools.runner import get_runner
SIM = os.getenv("SIM", "icarus")
TB_DIR = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR = PROJ_DIR / "rtl"
BUILD = PROJ_DIR / "sim_build" / "aclkgt_gen"
sys.path.insert(0, str(TB_DIR)); sys.path.insert(0, str(TB_DIR.parent))
_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")
def test_aclkgt_gen():
    runner = get_runner(SIM)
    runner.build(sources=[
        RTL_DIR / "aclk_bridge" / "crc8_calc.v",
        RTL_DIR / "aclk_bridge" / "gearbox_96_to_16.v",
        RTL_DIR / "aclk_gt" / "aclk_gt_frame_gen.v",
    ], hdl_toplevel="aclk_gt_frame_gen", build_dir=BUILD,
       timescale=("1ns", "1ps"), waves=True, always=True)
    runner.test(hdl_toplevel="aclk_gt_frame_gen",
                test_module="test_aclkgt_gen", build_dir=BUILD, waves=True)
if __name__ == "__main__":
    test_aclkgt_gen()
```

- [ ] **Step 3: Run to verify it fails (gen missing).**

Run: `.\sim.ps1 run -Module aclkgt_gen`
Expected: FAIL — `aclk_gt_frame_gen` not found.

- [ ] **Step 4: Implement `aclk_gt_frame_gen.v`.**

Create `rtl/aclk_gt/aclk_gt_frame_gen.v`. Build the 96-bit frame in two CRC passes per the inherited `crc8_calc` (CRC computed over `{packet80, 8'h00}`, then appended), present `DATA96` with `DATA96_VALID` to the gearbox, advance the timeline when the gearbox has consumed all 6 words. Match `frame_to_words`' K pattern: only word0 carries K (the comma low byte), via `K_IN = 12'b1000_0000_0000` into `GEARBOX_96_TO_16`.
```verilog
// rtl/aclk_gt/aclk_gt_frame_gen.v
//
// Compiled-in gigabit-ACLK frame generator (IP-free, Icarus-simmable). Cycles a
// fixed event/data timeline, builds {0xBC, EVENT[15:0], DATA[63:0], CRC8} using
// the inherited CRC8_CALC, and feeds GEARBOX_96_TO_16 to emit the 16-bit + K word
// stream a GT TX serializes. Replaces aclk_data_source (which needs a BRAM IP and
// a TCLK source). CRC8 is computed over {packet80, 8'h00} and appended, matching
// crc8_calc.v / tb/aclk_tx_model.build_frame.
module aclk_gt_frame_gen #(
    parameter integer N_EVENTS = 3
) (
    input  wire        CLK1,
    input  wire        RESETn,
    output wire [15:0] DATA16,
    output wire [1:0]  K_OUT,
    output reg         MARKER          // pulses at the start of each frame (debug)
);
    // ---- compiled-in timeline (MUST match tb/aclkgt_gen TIMELINE) ----
    reg [15:0] ev_rom [0:N_EVENTS-1];
    reg [63:0] da_rom [0:N_EVENTS-1];
    initial begin
        ev_rom[0] = 16'h0001; da_rom[0] = 64'h1111222233334444;
        ev_rom[1] = 16'h00A5; da_rom[1] = 64'hAAAABBBBCCCCDDDD;
        ev_rom[2] = 16'h1000; da_rom[2] = 64'h0123456789ABCDEF;
    end

    localparam COMMA = 8'hBC;

    // FSM: build packet80 -> run CRC -> latch DATA96 + pulse VALID -> wait 6 words.
    localparam S_LOAD=0, S_CRC=1, S_EMIT=2, S_HOLD=3;
    reg [1:0]  st;
    reg [$clog2(N_EVENTS)-1:0] idx;
    reg [79:0] packet80;
    reg        crc_calc;
    wire [7:0] crc_q;
    wire       crc_valid;
    reg [95:0] data96;
    reg        data96_valid;
    reg [3:0]  emit_ctr;

    CRC8_CALC u_crc (
        .CLK(CLK1), .RESETn(RESETn), .CALC(crc_calc),
        .DATA({packet80, 8'h00}), .CRC(crc_q), .CRC_VALID(crc_valid));

    GEARBOX_96_TO_16 u_gb (
        .CLK1(CLK1), .RESETn(RESETn),
        .DATA96(data96), .K_IN(12'b1000_0000_0000),
        .DATA96_VALID(data96_valid), .DATA16(DATA16), .K_OUT(K_OUT));

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            st <= S_LOAD; idx <= 0; crc_calc <= 1'b0;
            data96 <= 96'd0; data96_valid <= 1'b0; emit_ctr <= 4'd0; MARKER <= 1'b0;
        end else begin
            crc_calc <= 1'b0; data96_valid <= 1'b0; MARKER <= 1'b0;
            case (st)
                S_LOAD: begin
                    packet80 <= {ev_rom[idx], da_rom[idx]};
                    crc_calc <= 1'b1;              // kick CRC over {packet80,0x00}
                    st <= S_CRC;
                end
                S_CRC: if (crc_valid) begin
                    data96       <= {COMMA, packet80, crc_q};
                    data96_valid <= 1'b1;          // one-cycle load into the gearbox
                    MARKER       <= 1'b1;
                    emit_ctr     <= 4'd0;
                    st <= S_EMIT;
                end
                S_EMIT: begin                      // gearbox streams 6 words
                    emit_ctr <= emit_ctr + 4'd1;
                    if (emit_ctr == 4'd5) begin
                        idx <= (idx == N_EVENTS-1) ? 0 : idx + 1'b1;
                        st  <= S_LOAD;
                    end
                end
                default: st <= S_LOAD;
            endcase
        end
    end
endmodule
```

> Implementer note: confirm `GEARBOX_96_TO_16`'s exact `DATA96_VALID` -> 6-word timing against `rtl/aclk_bridge/gearbox_96_to_16.v` and adjust `emit_ctr`'s terminal count if the gearbox registers `DATA96` an extra cycle. The Step-6 test phase-aligns on the comma word, so an off-by-one in the inter-frame gap will still pass as long as the 6 words per frame are contiguous and correct; a wrong gap only matters if it injects a stray word, which the equality check catches.

- [ ] **Step 5: Run to verify it passes.**

Run: `.\sim.ps1 run -Module aclkgt_gen`
Expected: PASS — `frame_gen matches model over N words`. If it fails on phase/gap, adjust `emit_ctr` terminal count per the implementer note and re-run.

- [ ] **Step 6: Commit.**
```bash
git add rtl/aclk_gt/aclk_gt_frame_gen.v tb/aclkgt_gen/
git commit -m "feat(aclkgt): IP-free frame generator + model-agreement sim"
```

---

### Task 5: generator <-> receiver loopback sim (the agreement proof)

Wires `aclk_gt_frame_gen` -> `GEARBOX_16_TO_96` -> `ACLK_RCV` in one Icarus sim and asserts the decoded events equal the generator's compiled-in timeline. This proves TX and RX agree end-to-end (minus the GT) before any board.

**Files:**
- Create: `tb/aclkgt_gen_loop/tb_aclkgt_gen_loop_top.sv`
- Create: `tb/aclkgt_gen_loop/test_aclkgt_gen_loop.py`
- Create: `tb/aclkgt_gen_loop/runner.py`

**Interfaces:**
- Consumes: `aclk_gt_frame_gen` (Task 4), `GEARBOX_16_TO_96`, `ACLK_RCV`.
- Produces: nothing downstream (terminal verification).

- [ ] **Step 1: Write the tb top.**

Create `tb/aclkgt_gen_loop/tb_aclkgt_gen_loop_top.sv` (gen output 16-bit+K straight into the RX gearbox feeding ACLK_RCV; expose decoder outputs):
```systemverilog
`timescale 1ns/1ps
module tb_aclkgt_gen_loop_top (
    input  wire        CLK1,
    input  wire        RESETn,
    output wire [15:0] ACLK_EVENT,
    output wire [63:0] ACLK_DATA,
    output wire        ACLK_VALID,
    output wire        ACLK_ERROR,
    output wire        RX_ALIGNED_OUT
);
    wire [15:0] w_data16;
    wire [1:0]  w_k;
    aclk_gt_frame_gen #(.N_EVENTS(3)) u_gen (
        .CLK1(CLK1), .RESETn(RESETn), .DATA16(w_data16), .K_OUT(w_k), .MARKER());
    ACLK_RCV u_rcv (
        .RESETn(RESETn), .CLK1(CLK1),
        .DATA_FROM_XCVR(w_data16), .K_FROM_XCVR(w_k),
        .ACLK_EVENT(ACLK_EVENT), .ACLK_DATA(ACLK_DATA),
        .ACLK_VALID(ACLK_VALID), .ACLK_ERROR(ACLK_ERROR),
        .RX_ALIGNED_OUT(RX_ALIGNED_OUT), .DIAG());
endmodule
```

- [ ] **Step 2: Write the test.**

Create `tb/aclkgt_gen_loop/test_aclkgt_gen_loop.py`:
```python
"""Generator -> RX gearbox -> ACLK_RCV in one sim: the decoded events must equal
the generator's compiled-in timeline, in order, with no CRC errors."""
import sys
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TIMELINE = [(0x0001, 0x1111222233334444), (0x00A5, 0xAAAABBBBCCCCDDDD),
            (0x1000, 0x0123456789ABCDEF)]

@cocotb.test()
async def test_gen_to_rcv(dut):
    cocotb.start_soon(Clock(dut.CLK1, 16, unit="ns").start())
    dut.RESETn.value = 0
    await ClockCycles(dut.CLK1, 5)
    await Timer(1, unit="ns")
    dut.RESETn.value = 1

    captured, errors = [], 0
    # Run long enough for several timeline passes after alignment.
    for _ in range(6 * len(TIMELINE) * 8 + 200):
        await RisingEdge(dut.CLK1)
        await Timer(1, unit="ns")
        if int(dut.ACLK_VALID.value) == 1:
            captured.append((int(dut.ACLK_EVENT.value), int(dut.ACLK_DATA.value)))
        if int(dut.ACLK_ERROR.value) == 1:
            errors += 1

    assert int(dut.RX_ALIGNED_OUT.value) == 1, "RX never aligned to the generator"
    assert errors == 0, f"unexpected ACLK_ERROR on a clean generator: {errors}"
    assert captured, "no events decoded from the generator"
    # Every captured event must be one of the timeline entries, in cyclic order.
    start = TIMELINE.index(captured[0])
    for i, got in enumerate(captured):
        exp = TIMELINE[(start + i) % len(TIMELINE)]
        assert got == exp, f"#{i} decoded {got} != timeline {exp}"
    dut._log.info(f"gen<->rcv agree: {len(captured)} events decoded in order, 0 errors")
```

- [ ] **Step 3: Write the runner.**

Create `tb/aclkgt_gen_loop/runner.py` (toplevel `tb_aclkgt_gen_loop_top`; sources: `crc8_calc.v`, `gearbox_96_to_16.v`, `GEARBOX_16_TO_96.v`, `ACLK_REV.v`, `aclk_gt_frame_gen.v`, and the tb top — same runner skeleton as Task 2/4).

- [ ] **Step 4: Run; expect PASS.**

Run: `.\sim.ps1 run -Module aclkgt_gen_loop`
Expected: PASS — `gen<->rcv agree: N events decoded in order, 0 errors`.

- [ ] **Step 5: Commit.**
```bash
git add tb/aclkgt_gen_loop/
git commit -m "test(aclkgt): generator<->ACLK_RCV loopback agreement sim"
```

---

## Phase B — Board integration (Vivado + hardware; gated on Task 1)

> No Icarus model exists for the GT Wizard. These tasks are verified by a clean Vivado build (timing met, bitstream produced) and on-hardware event readout, not by cocotb. Each Vivado build runs via `hw.ps1`. All three targets keep `design_name = uart_echo_bd`.

### Task 6: Milestone 0 — single-board GT loopback build

GTH GT Wizard in near-end PMA loopback, fed by `aclk_gt_frame_gen`, decoded by `aclk_gt_readout_top`, read by the PS. Proves the GT IP config + the whole PL chain on one board with no optics.

**Files:**
- Create: `rtl/aclk_gt_loop_bd_top.v` (BD-top: GT Wizard + frame_gen + readout top; AXI X_INTERFACE; GT refclk diff port; freerun clock; SFP TX pins driven but RX internally looped)
- Create: `vivado/build_aclkgt_loop.tcl`
- Create: `constraints/kr260_aclkgt.xdc`
- Reference (port from): `rtl/Li_Files/top_module.v` (Evan's GTY loopback — adapt to GTH), `rtl/aclk_readout_bd_top.v` (X_INTERFACE + readout instantiation pattern), `vivado/build_aclk.tcl` (BD/PS/SmartConnect/MMCM/reset skeleton).

**Interfaces:**
- Consumes Task 1 values: `GT_QUAD, GT_LANE, REFCLK_FREQ_MHZ, LINE_RATE_GBPS, USERCLK_MHZ`, SFP+refclk package pins.
- Consumes Tasks 2+4: `aclk_gt_readout_top`, `aclk_gt_frame_gen`.

- [ ] **Step 1: Generate the GTH GT Wizard IP in the build tcl.** In `vivado/build_aclkgt_loop.tcl`, after `create_project`, create and configure the transceiver IP (values from Task 1):
```tcl
create_ip -name gtwizard_ultrascale -vendor xilinx.com -library ip \
    -module_name aclkgt_gt -dir [file join $build_dir ip]
set_property -dict [list \
    CONFIG.preset {GTH-Aurora_8B10B} \
    CONFIG.CHANNEL_ENABLE {<GT_QUAD>/<GT_LANE>} \
    CONFIG.TX_LINE_RATE {<LINE_RATE_GBPS>} \
    CONFIG.RX_LINE_RATE {<LINE_RATE_GBPS>} \
    CONFIG.TX_REFCLK_FREQUENCY {<REFCLK_FREQ_MHZ>} \
    CONFIG.RX_REFCLK_FREQUENCY {<REFCLK_FREQ_MHZ>} \
    CONFIG.TX_DATA_ENCODING {8B10B} \
    CONFIG.RX_DATA_DECODING {8B10B} \
    CONFIG.TX_USER_DATA_WIDTH {16} \
    CONFIG.RX_USER_DATA_WIDTH {16} \
    CONFIG.RX_COMMA_ALIGN_WORD {1} \
    CONFIG.RX_COMMA_P_ENABLE {true} \
    CONFIG.RX_COMMA_M_ENABLE {true} \
    CONFIG.RX_COMMA_P_VAL {0101111100} \
    CONFIG.RX_COMMA_M_VAL {1010000011} \
    CONFIG.RX_SLIDE_MODE {OFF} \
] [get_ips aclkgt_gt]
generate_target all [get_ips aclkgt_gt]
```
Substitute the `<...>` from `docs/aclkgt-hardware-facts.md`. The exact CONFIG keys/values are tuned at bring-up against the generated `aclkgt_gt` instance template (the GT Wizard's `*_in`/`*_out` port names land in `aclkgt_gt.veo` — read that to wire Step 2).

- [ ] **Step 2: Write `rtl/aclk_gt_loop_bd_top.v`.** Port Evan's `top_module.v` GT usage to GTH: `IBUFDS_GTE4` on the refclk diff port; instantiate the `aclkgt_gt` GT Wizard with `loopback_in = 3'b010`; drive its TX user data from `aclk_gt_frame_gen`; feed its RX user data (`gtwiz_userdata_rx_out[15:0]` + `rxctrl2_out[1:0]`) and `rxusrclk2` into `aclk_gt_readout_top`; expose the AXI4-Lite slave with the same `X_INTERFACE` attributes as `rtl/aclk_readout_bd_top.v`; expose `gtrefclk_p/n`, `gtytxp_out/gtytxn_out` (or GTH equivalents) as top ports; tie `gtwiz_reset_clk_freerun_in` to the BD freerun clock; expose `mmcm_locked` (drive from the GT's `gtpowergood`/`rx_reset_done` AND the BD MMCM lock). Use the GT Wizard's `gtwiz_userclk_rx_usrclk2_out` to clock `aclk_gt_readout_top.rx_clk`. Read `aclkgt_gt.veo` for exact port names; keep the readout instantiation identical to `aclk_readout_bd_top.v`'s `u_aclk` block but with `aclk_gt_readout_top` and the xcvr ports wired to the GT.

- [ ] **Step 3: Write `vivado/build_aclkgt_loop.tcl`.** Clone `vivado/build_aclk.tcl` and change: `set proj_name aclkgt_loop`; the IP creation from Step 1; `add_files` lists `rtl/aclk_bridge/{crc8_calc.v,GEARBOX_16_TO_96.v,gearbox_96_to_16.v,ACLK_REV.v}`, `rtl/{synchronizer.sv,async_fifo.sv,cdc_gray_count.sv}`, `rtl/aclk_readout/{aclk_readout_core.sv,aclk_readout_axi.sv}`, `rtl/aclk_gt/{aclk_gt_readout_top.sv,aclk_gt_frame_gen.v}`, `rtl/aclk_gt_loop_bd_top.v`; `set xdc_file [file join $root_dir constraints kr260_aclkgt.xdc]`; BD `create_bd_cell -type module -reference aclk_gt_loop_bd_top u_aclkgt`; add the GT refclk + SFP TX as `create_bd_port`; provide a freerun clock to the GT (pl_clk0 directly is fine, ~100 MHz). Keep PS/SmartConnect/`dcm_locked` xlconstant exactly as the aclk build.

- [ ] **Step 4: Write `constraints/kr260_aclkgt.xdc`.** SFP TX/RX + GT refclk pins from Task 1, plus the refclk `create_clock` and the async clock-group idiom. Seed:
```xdc
## constraints/kr260_aclkgt.xdc - gigabit ACLK over GTH/SFP
## Pins from docs/aclkgt-hardware-facts.md (VERIFY against the KR260 carrier schematic).
set_property PACKAGE_PIN <REFCLK_P> [get_ports gtrefclk_p]
set_property PACKAGE_PIN <REFCLK_N> [get_ports gtrefclk_n]
set_property PACKAGE_PIN <SFP_TX_P> [get_ports gt_txp]
set_property PACKAGE_PIN <SFP_TX_N> [get_ports gt_txn]
set_property PACKAGE_PIN <SFP_RX_P> [get_ports gt_rxp]
set_property PACKAGE_PIN <SFP_RX_N> [get_ports gt_rxn]
create_clock -name gt_refclk -period [expr 1000.0/<REFCLK_FREQ_MHZ>] [get_ports gtrefclk_p]
set_clock_groups -name async_ps_vs_gt -asynchronous \
    -group [get_clocks -filter {NAME =~ "clk_pl_*"}] \
    -group [get_clocks -include_generated_clocks gt_refclk]
```
(M0 loops RX internally so `gt_rxp/n` may be tied off in the BD-top; still constrain them for parity with M1.)

- [ ] **Step 5: Build.**

Run: `.\hw.ps1 build -Tcl vivado\build_aclkgt_loop.tcl -Name aclkgt_loop`
Expected: synth + impl + `write_bitstream` complete; timing met (WNS >= 0); `build/kria/aclkgt_loop/aclkgt_loop.runs/impl_1/uart_echo_bd_wrapper.bit.bin` produced. If timing fails on GT clock paths, confirm the async clock-group covers the GT-recovered clock vs `clk_pl_*`; if the GT IP errors on the refclk/rate, reconcile against Task 1 and the `.veo` template.

- [ ] **Step 6: Commit.**
```bash
git add rtl/aclk_gt_loop_bd_top.v vivado/build_aclkgt_loop.tcl constraints/kr260_aclkgt.xdc
git commit -m "feat(aclkgt): M0 single-board GT loopback build (GTH + gen + readout)"
```

---

### Task 7: PS reader `deploy/aclkgt_read.py`

**Files:**
- Create: `deploy/aclkgt_read.py`
- Reference: `deploy/aclk_read.py` (verbatim structure), `deploy/tclk_filter.py` (drop-mask helpers).

**Interfaces:**
- Consumes: the shared 16-byte register map; `tclk_filter.parse_drop_codes, filter_cfg_word`.

- [ ] **Step 1: Clone `deploy/aclk_read.py`.** Copy it to `deploy/aclkgt_read.py`. Change only: the header comment to "gigabit ACLK over GTH/SFP"; `TICK_NS = 1000.0 / <USERCLK_MHZ>` (the recovered RX user clock from Task 1, e.g. `1000.0/60.0` for 60 MHz) with a comment that the timestamp ticks at the GT user clock, not 120 MHz; keep the EVENT/DATA/flags decode identical (has_data is always 1 here). Keep the UIO device/offset resolution and the poll/read/POP loop unchanged.

- [ ] **Step 2: Sanity-check syntax.**

Run: `python -c "import ast; ast.parse(open('deploy/aclkgt_read.py').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit.**
```bash
git add deploy/aclkgt_read.py
git commit -m "feat(aclkgt): PS UIO reader for the gigabit ACLK readout"
```

---

### Task 8: Hardware bring-up M0 (loopback)

**Files:** none (hardware verification; record results in `docs/aclkgt-hardware-facts.md`).

- [ ] **Step 1: Load the M0 bitstream** onto one KR260 (same overlay/UIO load path as the `aclk`/`tclk` builds; the `.bit.bin` name is unchanged).

- [ ] **Step 2: Run the reader.** `python3 deploy/aclkgt_read.py` on the board. Confirm the decoded events cycle through `aclk_gt_frame_gen`'s compiled-in timeline `(0x0001,...), (0x00A5,...), (0x1000,...)`, `EVENT_COUNT` climbs, `ERROR_COUNT` stays flat, `LOCK` bit0 = 1, and the DEBUG `frame_count` climbs.

- [ ] **Step 3: Record the result** (line rate achieved, lock status, any GT tuning applied) in `docs/aclkgt-hardware-facts.md` and commit. If events do not decode: check `LOCK`/`rx_aligned` via DEBUG, then the GT comma value/polarity, then the refclk presence (the GT `*reset_done` outputs) before suspecting the PL chain (which Phase A already proved).

---

### Task 9: Milestone 1 — receiver build (real SFP RX)

**Files:**
- Create: `rtl/aclk_gt_rx_bd_top.v` (M0's BD-top minus the frame_gen and loopback: GT Wizard RX-only, `loopback_in=3'b000`, real `gt_rxp/n`)
- Create: `vivado/build_aclkgt_rx.tcl`
- Reuse: `constraints/kr260_aclkgt.xdc` (already has RX pins).

- [ ] **Step 1: Write `rtl/aclk_gt_rx_bd_top.v`** — identical to `aclk_gt_loop_bd_top.v` but: remove the `aclk_gt_frame_gen` instance and the TX-data wiring (tie GT TX user data to idle/comma or leave the TX path unused per the GT Wizard requirements); set `loopback_in=3'b000`; wire the GT RX user data from the real `gt_rxp/n`. Keep `aclk_gt_readout_top` + the AXI X_INTERFACE identical.

- [ ] **Step 2: Write `vivado/build_aclkgt_rx.tcl`** — clone `build_aclkgt_loop.tcl`; `set proj_name aclkgt_rx`; drop `aclk_gt_frame_gen.v` from `add_files`; reference module `aclk_gt_rx_bd_top`.

- [ ] **Step 3: Build.**

Run: `.\hw.ps1 build -Tcl vivado\build_aclkgt_rx.tcl -Name aclkgt_rx`
Expected: clean build, timing met, `.bit.bin` produced.

- [ ] **Step 4: Commit.**
```bash
git add rtl/aclk_gt_rx_bd_top.v vivado/build_aclkgt_rx.tcl
git commit -m "feat(aclkgt): M1 receiver build (GTH RX-only -> readout)"
```

---

### Task 10: Milestone 2 — generator build (real SFP TX)

**Files:**
- Create: `rtl/aclk_gt_gen_bd_top.v` (`aclk_gt_frame_gen` -> GT Wizard TX -> `gt_txp/n`; no readout, no AXI; a `MARKER`/heartbeat debug pin)
- Create: `vivado/build_aclkgt_gen.tcl`
- Reuse: `constraints/kr260_aclkgt.xdc` (TX + refclk pins).

- [ ] **Step 1: Write `rtl/aclk_gt_gen_bd_top.v`** — `IBUFDS_GTE4` refclk; GT Wizard TX path; TX user data from `aclk_gt_frame_gen`; `loopback_in=3'b000`; expose `gt_txp/n`, `gtrefclk_p/n`, the freerun clock, and a `MARKER` debug pin. No PS/AXI needed (free-running transmitter); still instantiate via a BD that supplies pl_clk0 as the freerun clock and the reset.

- [ ] **Step 2: Write `vivado/build_aclkgt_gen.tcl`** — clone `build_aclkgt_loop.tcl`; `set proj_name aclkgt_gen`; `add_files` = `crc8_calc.v, gearbox_96_to_16.v, aclk_gt_frame_gen.v, aclk_gt_gen_bd_top.v` (+ the GT IP); reference module `aclk_gt_gen_bd_top`; no readout sources, no SmartConnect/readout AXI (PS only for pl_clk0/reset).

- [ ] **Step 3: Build.**

Run: `.\hw.ps1 build -Tcl vivado\build_aclkgt_gen.tcl -Name aclkgt_gen`
Expected: clean build, timing met, `.bit.bin` produced.

- [ ] **Step 4: Commit.**
```bash
git add rtl/aclk_gt_gen_bd_top.v vivado/build_aclkgt_gen.tcl
git commit -m "feat(aclkgt): M2 generator build (frame_gen -> GTH TX -> SFP)"
```

---

### Task 11: Hardware bring-up M1 + M2 (two-board link)

**Files:** none (hardware verification; record results in `docs/aclkgt-hardware-facts.md`).

- [ ] **Step 1:** Flash `build_aclkgt_gen` on board B, `build_aclkgt_rx` on board A. Connect board B's SFP TX to board A's SFP RX with fiber (or a copper SFP loopback / matched optics from Task 1).

- [ ] **Step 2:** Run `python3 deploy/aclkgt_read.py` on board A (receiver). Confirm the events read out match board B's `aclk_gt_frame_gen` timeline, in order, with `ERROR_COUNT` flat and `LOCK`/`rx_aligned` set.

- [ ] **Step 3:** Record the board-to-board result (achieved line rate, BER/error counts over a sustained run, any GT tuning) in `docs/aclkgt-hardware-facts.md`. If RX never aligns: check fiber TX/RX orientation, optics wavelength match, then GT polarity (`txpolarity`/`rxpolarity`), then refclk on both boards.

- [ ] **Step 4: Commit the results notes.**
```bash
git add docs/aclkgt-hardware-facts.md
git commit -m "docs(aclkgt): two-board GT/SFP bring-up results"
```

---

## Self-Review

**Spec coverage:** Three milestones (loopback/rx/gen) -> Tasks 6/9/10; reuse map (`ACLK_RCV`/readout reused, frame_gen substituted for `aclk_data_source` with rationale) -> Tasks 2/4; adapter (`flags=0x0001`, `DROP_NULL=1`) -> Task 2; GT clocking -> Task 6 Step 2; testing (RTL-chain cocotb + on-hardware GT) -> Tasks 2/3/4/5 + 8/11; research dependencies (refclk/rate/optics) -> Task 1; risks (loopback-first, GT-only-on-hw, refclk-vs-rate, 16-byte spacing) -> Tasks 1/6/8. All spec sections map to tasks.

**Deviation from spec (intentional, recorded):** the spec named `aclk_data_source` as the generator; planning revealed its `blk_mem_gen_0` IP + `TCLK_RCV` coupling makes it un-simmable and not a clean transmitter, so Task 4 substitutes a purpose-built `aclk_gt_frame_gen.v` (the same choice the ACLK-Lite path made with its encoder). Net behavior is unchanged; reuse is higher (`crc8_calc` + `gearbox_96_to_16` still reused).

**Placeholder scan:** the only `<...>` tokens are GT IP CONFIG values and XDC pins explicitly sourced from Task 1's `docs/aclkgt-hardware-facts.md` (a named prior-task deliverable), not unfilled blanks. Phase A tasks contain complete code.

**Type consistency:** `aclk_gt_readout_top` port names (`data_from_xcvr`, `k_from_xcvr`, `rx_aligned`) match the tb top (Task 2) and the GT-top consumers (Tasks 6/9). `aclk_gt_frame_gen` ports (`CLK1, RESETn, DATA16, K_OUT, MARKER`) match the gen sim (Task 4) and the loopback sim (Task 5). Register offsets match the Global Constraints map and `deploy/aclk_read.py`.
