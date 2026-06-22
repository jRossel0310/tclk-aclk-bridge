# Generator real-framing re-alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the ACLK-Lite signal generator to emit the real biphase-mark, byte-oriented framing that the shipped unified decoder (`build_clk`, `serdec4_9MHz` + `clk_byte_framer`) reads, so the two boards talk end to end.

**Architecture:** The generator becomes a free-running biphase-mark cell engine that always emits 100 ns cells (idle = continuous 1-cells so the receiver's serdec keeps lock; a frame = its bytes' cells, then back to idle). A hardcoded timeline feeds it the same test trio. Validation: a per-frame waveform check against the golden `clk_tx_model` and a loopback through the real `clk_rcv` receiver.

**Tech Stack:** SystemVerilog (Vivado 2024.2, KR260 `xck26-sfvc784-2LV-c`), cocotb 2.0 + Icarus for sim, PowerShell `hw.ps1` for the build.

## Global Constraints

- Project style: NEVER use em dashes anywhere (code, comments, commit messages). Use " - " or rephrase.
- Rewrite in place: `rtl/aclk_lite/aclk_lite_encoder.sv`, `aclk_lite_gen_timeline.sv`, `rtl/aclk_gen_bd_top.v`, `vivado/build_aclkgen.tcl`, and the encoder + loopback tests. Do NOT touch the shipped receiver (`clk_rcv.sv`, `clk_byte_framer.sv`, `serdec4_9MHz.v`, `clk_readout_*`, `build_clk.tcl`) or the clean-room receiver (`aclk_lite_decoder.sv`, `manchester_tx_model.py`).
- Real framing (authoritative: `docs/aclk-lite-framing.md`): same Manchester line code as TCLK, 100 ns cells, MSB-first; each byte = start(0) + 8 data + even parity (parity = XOR of the 8 data bits); bytes back-to-back; frame ends on 2 terminal idle-1 cells. Frame types: 1 byte = TCLK event, 2 = ACLK event, 12 = full packet (event bytes 0-1, data bytes 2-9, CRC byte 10 = 0x00, control byte 11 = 0x00). Idle = continuous-1s square wave (NOT DC).
- Biphase-mark cell: a level transition at the cell boundary, plus an extra mid-cell transition iff the cell bit is 1 (none for 0). `SAMPLES_PER_CELL = 8` clk cycles per cell (HALF = 4 per half-cell). Bit-identical to `tb/tclk_tx_model.biphase_samples` driven at 8 samples/cell.
- Generator cell clock = 80 MHz (down from 120). Encoder param is `SAMPLES_PER_CELL` (default 8), replacing the old `OVERSAMPLE`.
- Test trio values (unchanged): TCLK event `0x0055`; ACLK event `0xABCD`; full packet event `0x1234` + data `0xDEADBEEFCAFE0001`.
- CRC byte and control byte are fixed `0x00` placeholders (decoder ignores them).
- Golden TX model = `tb/clk_tx_model.py` (`frame_bits`) + `tb/tclk_tx_model.py` (`biphase_samples`, `event_bits`). Receiver for the loopback = `rtl/aclk_lite/clk_rcv.sv` (ports: `RESETn, CLK_40M, CLK_80M, clkline, event_valid, event_id[15:0], data_valid, data[63:0], parity_error, is_tclk, sig_err`).

---

### Task 1: Rewrite `aclk_lite_encoder` to real biphase-mark byte framing

**Files:**
- Modify (full rewrite): `rtl/aclk_lite/aclk_lite_encoder.sv`
- Modify (full rewrite): `tb/aclk_lite_encoder/test_aclk_lite_encoder.py`
- Modify: `tb/aclk_lite_encoder/runner.py` (golden-model import + sources)

**Interfaces:**
- Consumes: `tb/clk_tx_model.py` `frame_bits(byte_list)` (per-byte start+8+parity bit list), which imports `tb/tclk_tx_model.py` `event_bits`.
- Produces: `aclk_lite_encoder #(parameter int SAMPLES_PER_CELL=8) (input clk, rstn, start, input [15:0] event_id, input [63:0] data, input [1:0] frame_type, output line, output busy)`. `frame_type`: 0=TCLK 1-byte, 1=ACLK event 2-byte, 2=full 12-byte. Task 2's timeline drives this exact interface.

- [ ] **Step 1: Write the failing test**

Replace the entire contents of `tb/aclk_lite_encoder/test_aclk_lite_encoder.py` with:

```python
"""Cocotb test for rtl/aclk_lite/aclk_lite_encoder.sv, the real-framing biphase-mark
encoder. For each frame_type (TCLK 1-byte, ACLK event 2-byte, full 12-byte) the
encoder's emitted line, reduced to its transition pattern, must contain the golden
biphase-mark framing of the assembled bytes (transition at every cell boundary, plus
a mid-cell transition iff the cell bit is 1). The transition view is level- and
phase-independent, so it is robust to exactly when the frame starts.
"""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from clk_tx_model import frame_bits   # real-framing per-byte bit list

SPC = 8        # SAMPLES_PER_CELL
HALF = 4
CLK_NS = 12    # exact period is irrelevant; only sample counts matter


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


def byte_list(event_id, data, frame_type):
    """Mirror the encoder's byte assembly (MSB-first)."""
    if frame_type == 0:
        return [event_id & 0xFF]
    if frame_type == 1:
        return [(event_id >> 8) & 0xFF, event_id & 0xFF]
    bl = [(event_id >> 8) & 0xFF, event_id & 0xFF]
    for shift in range(56, -1, -8):
        bl.append((data >> shift) & 0xFF)
    bl += [0x00, 0x00]            # CRC + control placeholders
    return bl


def golden_transitions(bl):
    """Per cell: a transition at offset 0 (boundary), and at offset HALF iff bit==1."""
    out = []
    for bit in frame_bits(bl):
        cell = [0] * SPC
        cell[0] = 1
        cell[HALF] = bit
        out += cell
    return out


def _contains(big, sub):
    n = len(sub)
    return any(big[i:i + n] == sub for i in range(len(big) - n + 1))


async def reset_dut(dut):
    dut.start.value = 0
    dut.event_id.value = 0
    dut.data.value = 0
    dut.frame_type.value = 0
    dut.rstn.value = 0
    await ClockCycles(dut.clk, 5)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    await ClockCycles(dut.clk, 5)


async def send_and_capture(dut, event_id, data, frame_type, n_cells):
    dut.event_id.value = event_id
    dut.data.value = data
    dut.frame_type.value = frame_type
    await RisingEdge(dut.clk)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    levels = []
    for _ in range(n_cells * SPC):
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        levels.append(_b(dut.line))
    return levels


@cocotb.test()
async def test_encoder_biphase_framing(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    await reset_dut(dut)

    cases = [
        (0x0055, 0, 0),                    # TCLK 1 byte
        (0xABCD, 0, 1),                    # ACLK event 2 bytes
        (0x1234, 0xDEADBEEFCAFE0001, 2),   # full 12 bytes
    ]
    for ev, dat, ft in cases:
        bl = byte_list(ev, dat, ft)
        n_cells = len(bl) * 10 + 30        # frame cells + idle margin
        levels = await send_and_capture(dut, ev, dat, ft, n_cells)
        trans = [levels[i] ^ levels[i - 1] for i in range(1, len(levels))]
        gt = golden_transitions(bl)
        assert _contains(trans, gt), \
            f"frame_type {ft}: biphase framing not found in emitted line"
        await ClockCycles(dut.clk, 20 * SPC)   # drain back to idle

    dut._log.info("encoder biphase framing OK for 1/2/12-byte frames")
```

Update `tb/aclk_lite_encoder/runner.py`: nothing imports `manchester_tx_model` now, but it still needs `tb/` on `sys.path` for `clk_tx_model` / `tclk_tx_model`. The existing runner already does `sys.path.insert(0, str(TB_DIR.parent))`, so leave the path lines; just confirm the `sources=[...]` still lists only `RTL_DIR / "aclk_lite" / "aclk_lite_encoder.sv"` (the encoder is still a leaf module). No change needed to runner.py beyond confirming that.

- [ ] **Step 2: Run the test to verify it fails**

Run: `powershell -ExecutionPolicy Bypass -File ./sim.ps1 run -Module aclk_lite_encoder`
Expected: FAIL - the current encoder has ports `payload`/`length` (not `event_id`/`data`/`frame_type`), so cocotb errors on `dut.event_id` / `dut.frame_type`, or the build fails. Either way it does not pass.

- [ ] **Step 3: Rewrite the encoder**

Replace the entire contents of `rtl/aclk_lite/aclk_lite_encoder.sv` with:

```systemverilog
// rtl/aclk_lite/aclk_lite_encoder.sv
//
// Real-framing ACLK-Lite / TCLK biphase-mark ENCODER. A free-running cell engine:
// it ALWAYS emits 100 ns biphase-mark cells so the receiver's serdec keeps carrier
// lock. Idle = continuous logical-1 cells. On `start` (while idle) it serializes one
// frame - bytes assembled from event_id/data per frame_type, each byte = start(0) +
// 8 data MSB-first + even parity, bytes back-to-back - then returns to idle 1-cells.
// Biphase-mark cell: a transition at the cell boundary, plus an extra mid-cell
// transition iff the cell bit is 1. SAMPLES_PER_CELL clk cycles per cell (HALF each
// half). The emitted line is bit-identical to tb/tclk_tx_model.biphase_samples.
// frame_type: 0 = TCLK (1 byte), 1 = ACLK event (2 bytes), 2 = full packet
// (12 bytes: event 0-1, data 2-9, CRC 10 = 0x00, control 11 = 0x00). A start while
// busy is ignored.

`timescale 1ns / 1ps

module aclk_lite_encoder #(
    parameter int SAMPLES_PER_CELL = 8       // oversampling-clock cycles per 100 ns cell
) (
    input  logic        clk,
    input  logic        rstn,                // async, active-low
    input  logic        start,               // 1-cycle strobe; ignored while busy
    input  logic [15:0] event_id,
    input  logic [63:0] data,
    input  logic [1:0]  frame_type,          // 0=TCLK 1B, 1=ACLK event 2B, 2=full 12B
    output logic        line,                // idle = continuous 1-cells
    output logic        busy
);

    localparam int HALF = SAMPLES_PER_CELL / 2;

    // ---- combinational byte assembly ----
    logic [7:0]  byte_arr [0:11];
    logic [7:0]  nbytes;
    integer      bi;
    always_comb begin
        for (bi = 0; bi < 12; bi = bi + 1) byte_arr[bi] = 8'h00;
        case (frame_type)
            2'd0: begin
                nbytes      = 8'd1;
                byte_arr[0] = event_id[7:0];
            end
            2'd1: begin
                nbytes      = 8'd2;
                byte_arr[0] = event_id[15:8];
                byte_arr[1] = event_id[7:0];
            end
            default: begin                    // 2 = full 12-byte packet
                nbytes       = 8'd12;
                byte_arr[0]  = event_id[15:8];
                byte_arr[1]  = event_id[7:0];
                byte_arr[2]  = data[63:56];
                byte_arr[3]  = data[55:48];
                byte_arr[4]  = data[47:40];
                byte_arr[5]  = data[39:32];
                byte_arr[6]  = data[31:24];
                byte_arr[7]  = data[23:16];
                byte_arr[8]  = data[15:8];
                byte_arr[9]  = data[7:0];
                byte_arr[10] = 8'h00;         // CRC placeholder
                byte_arr[11] = 8'h00;         // control placeholder
            end
        endcase
    end

    // ---- frame-bit vector, MSB-first, left-aligned at [119] ----
    // per byte = start(0) + 8 data (MSB first) + even parity (XOR of data bits).
    logic [119:0] framebits_n;
    logic [7:0]   nbits_n;
    integer       kk;
    always_comb begin
        framebits_n = 120'd0;
        for (kk = 0; kk < 12; kk = kk + 1) begin
            if (kk < nbytes)
                framebits_n[119 - kk*10 -: 10] = {1'b0, byte_arr[kk], ^byte_arr[kk]};
        end
        nbits_n = nbytes * 8'd10;
    end

    // ---- free-running biphase-mark cell engine ----
    logic [119:0] framebits;
    logic [7:0]   nbits;
    logic [7:0]   bit_idx;       // current cell, 0..nbits-1 while busy
    logic [3:0]   cnt;           // sample within the cell, 0..SAMPLES_PER_CELL-1
    logic         level;         // current line level
    logic         pending;       // a start seen while idle; begin at next cell boundary

    // current cell bit: idle -> 1, busy -> the frame bit (MSB-first)
    wire cur_bit = busy ? framebits[8'd119 - bit_idx] : 1'b1;

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            framebits <= 120'd0;
            nbits     <= 8'd0;
            bit_idx   <= 8'd0;
            cnt       <= 4'd0;
            level     <= 1'b1;
            line      <= 1'b1;
            busy      <= 1'b0;
            pending   <= 1'b0;
        end else begin
            // latch a start while idle (consumed at the next cell boundary)
            if (start && !busy) pending <= 1'b1;

            // biphase-mark transitions within the cell
            if (cnt == 4'd0) begin
                level <= ~level;              // boundary transition (every cell)
                line  <= ~level;
            end else if (cnt == HALF[3:0]) begin
                if (cur_bit) begin
                    level <= ~level;          // mid-cell transition for a 1
                    line  <= ~level;
                end
            end

            // advance the cell / sequence at the end of each cell
            if (cnt == SAMPLES_PER_CELL[3:0] - 4'd1) begin
                cnt <= 4'd0;
                if (busy) begin
                    if (bit_idx == nbits - 8'd1) begin
                        busy    <= 1'b0;      // frame done -> back to idle 1-cells
                        bit_idx <= 8'd0;
                    end else begin
                        bit_idx <= bit_idx + 8'd1;
                    end
                end else if (pending) begin
                    busy      <= 1'b1;
                    pending   <= 1'b0;
                    framebits <= framebits_n;
                    nbits     <= nbits_n;
                    bit_idx   <= 8'd0;
                end
            end else begin
                cnt <= cnt + 4'd1;
            end
        end
    end

endmodule
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `powershell -ExecutionPolicy Bypass -File ./sim.ps1 run -Module aclk_lite_encoder`
Expected: PASS - `TESTS=1 PASS=1`, log "encoder biphase framing OK for 1/2/12-byte frames".

- [ ] **Step 5: Commit**

```bash
git add rtl/aclk_lite/aclk_lite_encoder.sv tb/aclk_lite_encoder/test_aclk_lite_encoder.py tb/aclk_lite_encoder/runner.py
git commit -m "feat(gen): rewrite aclk_lite_encoder to real biphase-mark byte framing"
```

---

### Task 2: Rewrite the timeline + loopback through the real receiver

**Files:**
- Modify (full rewrite): `rtl/aclk_lite/aclk_lite_gen_timeline.sv`
- Modify (full rewrite): `tb/aclk_lite_gen_loopback/tb_aclk_gen_loopback.sv`
- Modify (full rewrite): `tb/aclk_lite_gen_loopback/test_aclk_gen_loopback.py`
- Modify: `tb/aclk_lite_gen_loopback/runner.py` (sources: add serdec + framer + clk_rcv)

**Interfaces:**
- Consumes: `aclk_lite_encoder` (Task 1) with ports `clk, rstn, start, event_id[15:0], data[63:0], frame_type[1:0], line, busy`; the shipped `clk_rcv` (ports listed in Global Constraints).
- Produces: `aclk_lite_gen_timeline #(SAMPLES_PER_CELL=8, IDLE_GAP=64, TRIO_GAP=80000) (input clk, rstn, output line, output frame_sync)` - Task 3's BD top instantiates this.

- [ ] **Step 1: Write the failing test (the loopback harness + cocotb test)**

Replace the entire contents of `tb/aclk_lite_gen_loopback/tb_aclk_gen_loopback.sv` with:

```systemverilog
// tb/aclk_lite_gen_loopback/tb_aclk_gen_loopback.sv
//
// Loopback harness: the rewritten timeline's biphase-mark line feeds the REAL unified
// receiver (clk_rcv = serdec4_9MHz + clk_byte_framer). The generator and serdec run on
// clk_80m; the framer runs on clk_40m. IDLE_GAP/TRIO_GAP are shrunk so a few trios run
// quickly. cocotb drives the clocks/reset and watches the decoder.

`timescale 1ns / 1ps

module tb_aclk_gen_loopback #(
    parameter int SAMPLES_PER_CELL = 8,
    parameter int IDLE_GAP = 48,
    parameter int TRIO_GAP = 96
) (
    input  logic        clk_80m,
    input  logic        clk_40m,
    input  logic        rstn,
    output logic        event_valid,
    output logic [15:0] event_id,
    output logic        data_valid,
    output logic [63:0] data,
    output logic        parity_error,
    output logic        is_tclk,
    output logic        frame_sync
);
    logic line;

    aclk_lite_gen_timeline #(
        .SAMPLES_PER_CELL(SAMPLES_PER_CELL), .IDLE_GAP(IDLE_GAP), .TRIO_GAP(TRIO_GAP)
    ) u_gen (
        .clk(clk_80m), .rstn(rstn), .line(line), .frame_sync(frame_sync)
    );

    clk_rcv u_rcv (
        .RESETn      (rstn),
        .CLK_40M     (clk_40m),
        .CLK_80M     (clk_80m),
        .clkline     (line),
        .event_valid (event_valid),
        .event_id    (event_id),
        .data_valid  (data_valid),
        .data        (data),
        .parity_error(parity_error),
        .is_tclk     (is_tclk),
        .sig_err     ()
    );
endmodule
```

Replace the entire contents of `tb/aclk_lite_gen_loopback/test_aclk_gen_loopback.py` with:

```python
"""Loopback integration test: the rewritten aclk_lite_gen_timeline -> the real unified
receiver clk_rcv (serdec4_9MHz + clk_byte_framer). The hardcoded timeline must drive
real-framed frames that the shipped decoder recovers as exactly the injected trio,
repeating, with zero parity errors:
  (0x0055, data=None, is_tclk=1)                 # TCLK event 0x55  (1 byte)
  (0xABCD, data=None, is_tclk=0)                 # ACLK event       (2 bytes)
  (0x1234, data=0xDEADBEEFCAFE0001, is_tclk=0)   # full ACLK packet (12 bytes)

serdec needs a brief carrier warm-up; the timeline idles (1-cells) before the first
frame, so the very first frame may be missed during lock-up. The test asserts on
steady-state trios after warm-up.
"""
import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK80_NS = 12     # ~80 MHz generator + serdec clock
CLK40_NS = 25     # ~40 MHz framer clock
MASK64 = (1 << 64) - 1

TRIO = [
    (0x0055, None, 1),
    (0xABCD, None, 0),
    (0x1234, 0xDEADBEEFCAFE0001, 0),
]


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


async def reset_dut(dut):
    dut.rstn.value = 0
    await ClockCycles(dut.clk_80m, 5)
    await ClockCycles(dut.clk_40m, 5)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    await ClockCycles(dut.clk_80m, 5)
    await ClockCycles(dut.clk_40m, 5)


async def monitor(dut, events, errors):
    while True:
        await RisingEdge(dut.clk_40m)
        await Timer(1, unit="ns")
        if _b(dut.event_valid) == 1:
            dv = _b(dut.data_valid)
            events.append((
                int(dut.event_id.value) & 0xFFFF,
                (int(dut.data.value) & MASK64) if dv == 1 else None,
                _b(dut.is_tclk),
            ))
        if _b(dut.parity_error) == 1:
            errors.append(1)


def _save_plot(n, name, title):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return None
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.bar(["events decoded"], [n], color="tab:blue")
    ax.set_title(title)
    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "aclk_lite_gen_loopback" / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


@cocotb.test()
async def test_loopback_trio_repeats(dut):
    """After serdec warm-up, two consecutive full trios decode in order, no errors."""
    cocotb.start_soon(Clock(dut.clk_80m, CLK80_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_40m, CLK40_NS, unit="ns").start())
    await reset_dut(dut)

    events, errors = [], []
    cocotb.start_soon(monitor(dut, events, errors))

    # Run long enough for serdec lock + several trios at the shrunk gaps.
    await ClockCycles(dut.clk_80m, 40000)

    assert len(events) >= 6, f"expected >= 6 events after warm-up, got {len(events)}: {events}"
    # Find a clean repeating trio anywhere in the stream (skip any warm-up partial).
    found = False
    for i in range(len(events) - 5):
        if events[i:i + 3] == TRIO and events[i + 3:i + 6] == TRIO:
            found = True
            break
    assert found, f"two consecutive correct trios not found in: {events}"
    # serdec emits at most one spurious parity error while first locking to the carrier.
    assert len(errors) <= 1, f"too many parity errors ({len(errors)}); expected <= 1 startup"

    path = _save_plot(len(events), "loopback_events.png",
                      "ACLK-Lite generator -> unified clk_rcv: events decoded")
    if path:
        dut._log.info(f"plot written to {path}")
    dut._log.info(f"loopback OK: {len(events)} events, trio repeats, parity errors={len(errors)}")
```

Update `tb/aclk_lite_gen_loopback/runner.py` `sources=[...]` to build the loopback against the real receiver. It must list (in dependency order): `rtl/aclk_bridge/serdec4_9MHz.v`, `rtl/aclk_lite/clk_byte_framer.sv`, `rtl/aclk_lite/clk_rcv.sv`, `rtl/aclk_lite/aclk_lite_encoder.sv`, `rtl/aclk_lite/aclk_lite_gen_timeline.sv`, and the tb top `tb/aclk_lite_gen_loopback/tb_aclk_gen_loopback.sv`. Keep `hdl_toplevel="tb_aclk_gen_loopback"`, `test_module="test_aclk_gen_loopback"`, and the existing `sys.path` / build_dir lines. Remove any reference to `aclk_lite_decoder.sv` from the sources list.

- [ ] **Step 2: Run the test to verify it fails**

Run: `powershell -ExecutionPolicy Bypass -File ./sim.ps1 run -Module aclk_lite_gen_loopback`
Expected: FAIL - the timeline still has the old `payload/length` encoder interface and `OVERSAMPLE` param, so elaboration fails (port/param mismatch against the new encoder or the tb's `SAMPLES_PER_CELL`), or no events decode.

- [ ] **Step 3: Rewrite the timeline**

Replace the entire contents of `rtl/aclk_lite/aclk_lite_gen_timeline.sv` with:

```systemverilog
// rtl/aclk_lite/aclk_lite_gen_timeline.sv
//
// Hardcoded real-framing ACLK-Lite event source: drives aclk_lite_encoder through a
// fixed trio of frames forever, with idle gaps between them, and pulses frame_sync at
// the start of each trio (a clean scope trigger). No PS/AXI: it boots and transmits.
// The trio exercises all three decoder paths:
//   frame0: TCLK event 0x55          (frame_type 0, 1 byte)
//   frame1: ACLK event 0xABCD        (frame_type 1, 2 bytes)
//   frame2: ACLK event 0x1234 + data 0xDEADBEEFCAFE0001  (frame_type 2, 12 bytes)
//
// The encoder free-runs the carrier, so an "idle gap" is the encoder emitting idle
// 1-cells between frames. IDLE_GAP must exceed the 2 terminal idle cells the framer
// keys on (2 cells = 16 clks at SAMPLES_PER_CELL=8); the default 64 clears it.
// TRIO_GAP defaults to ~1 ms at 80 MHz.

`timescale 1ns / 1ps

module aclk_lite_gen_timeline #(
    parameter int SAMPLES_PER_CELL = 8,
    parameter int IDLE_GAP = 64,           // idle clk cycles between frames
    parameter int TRIO_GAP = 80000         // idle clk cycles before repeating (~1 ms)
) (
    input  logic clk,
    input  logic rstn,
    output logic line,                     // biphase-mark output (idle = 1-cells)
    output logic frame_sync                // 1-cycle pulse at the start of each trio
);

    localparam logic [15:0] EV0 = 16'h0055;  localparam logic [1:0] FT0 = 2'd0;
    localparam logic [15:0] EV1 = 16'hABCD;  localparam logic [1:0] FT1 = 2'd1;
    localparam logic [15:0] EV2 = 16'h1234;  localparam logic [1:0] FT2 = 2'd2;
    localparam logic [63:0] DAT2 = 64'hDEADBEEF_CAFE_0001;

    logic        enc_start;
    logic [15:0] enc_event;
    logic [63:0] enc_data;
    logic [1:0]  enc_ftype;
    logic        enc_busy;

    aclk_lite_encoder #(.SAMPLES_PER_CELL(SAMPLES_PER_CELL)) u_enc (
        .clk(clk), .rstn(rstn),
        .start(enc_start), .event_id(enc_event), .data(enc_data),
        .frame_type(enc_ftype), .line(line), .busy(enc_busy)
    );

    typedef enum logic [2:0] {
        S_SYNC, S_F0, S_W0, S_F1, S_W1, S_F2, S_W2
    } state_t;
    state_t      state;
    logic [31:0] gap_cnt;

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            state     <= S_SYNC;
            gap_cnt   <= 32'd0;
            enc_start <= 1'b0;
            enc_event <= 16'd0;
            enc_data  <= 64'd0;
            enc_ftype <= 2'd0;
            frame_sync<= 1'b0;
        end else begin
            enc_start  <= 1'b0;          // default: 1-cycle strobe
            frame_sync <= 1'b0;
            case (state)
                S_SYNC: begin
                    frame_sync <= 1'b1;
                    enc_event  <= EV0; enc_data <= 64'd0; enc_ftype <= FT0; enc_start <= 1'b1;
                    gap_cnt    <= 32'd0;
                    state      <= S_F0;
                end
                S_F0: if (enc_busy) state <= S_W0;
                S_W0: if (!enc_busy) begin
                          if (gap_cnt == IDLE_GAP - 1) begin
                              gap_cnt <= 32'd0;
                              enc_event <= EV1; enc_data <= 64'd0; enc_ftype <= FT1; enc_start <= 1'b1;
                              state <= S_F1;
                          end else gap_cnt <= gap_cnt + 32'd1;
                      end
                S_F1: if (enc_busy) state <= S_W1;
                S_W1: if (!enc_busy) begin
                          if (gap_cnt == IDLE_GAP - 1) begin
                              gap_cnt <= 32'd0;
                              enc_event <= EV2; enc_data <= DAT2; enc_ftype <= FT2; enc_start <= 1'b1;
                              state <= S_F2;
                          end else gap_cnt <= gap_cnt + 32'd1;
                      end
                S_F2: if (enc_busy) state <= S_W2;
                S_W2: if (!enc_busy) begin
                          if (gap_cnt == TRIO_GAP - 1) begin
                              gap_cnt <= 32'd0;
                              state <= S_SYNC;
                          end else gap_cnt <= gap_cnt + 32'd1;
                      end
                default: state <= S_SYNC;
            endcase
        end
    end

endmodule
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `powershell -ExecutionPolicy Bypass -File ./sim.ps1 run -Module aclk_lite_gen_loopback`
Expected: PASS - `TESTS=1 PASS=1`, log "loopback OK: N events, trio repeats, parity errors=0 (or 1)". This is the key gate: the rewritten generator and the shipped unified decoder agree.

- [ ] **Step 5: Commit**

```bash
git add rtl/aclk_lite/aclk_lite_gen_timeline.sv tb/aclk_lite_gen_loopback/
git commit -m "feat(gen): real-framing timeline + loopback through the unified clk_rcv"
```

---

### Task 3: Retune the BD wrapper + generator build to 80 MHz

**Files:**
- Modify: `rtl/aclk_gen_bd_top.v` (param + comments: 120 -> 80 MHz, OVERSAMPLE -> SAMPLES_PER_CELL)
- Modify: `vivado/build_aclkgen.tcl` (clk_wiz CLKOUT1 120 -> 80, comment)
- Modify: `tb/aclk_gen_bd_top/test_aclk_gen_bd_top.py` and/or its runner if they reference the old param (confirm/adjust)

**Interfaces:**
- Consumes: `aclk_lite_gen_timeline` (Task 2) with `SAMPLES_PER_CELL` param.
- Produces: the generator bitstream (built by `hw.ps1`); no downstream RTL consumer.

- [ ] **Step 1: Retune the BD wrapper**

In `rtl/aclk_gen_bd_top.v`, change the timeline instantiation parameter from the old `OVERSAMPLE(12)` to `SAMPLES_PER_CELL(8)`, and update the `clk_os` comment from 120 MHz to 80 MHz. The instantiation becomes:

```verilog
    aclk_lite_gen_timeline #(.SAMPLES_PER_CELL(8)) u_gen (
        .clk        (clk_os),
        .rstn       (rstn),
        .line       (line),
        .frame_sync (frame_sync)
    );
```

Update the header comment line that says "the 120 MHz oversample clock" to "the 80 MHz cell clock", and the `clk_os` port comment from "120 MHz oversample clock" to "80 MHz cell clock". The `clkos_dbg` divider comment (`~117 kHz`) becomes `~78 kHz` (80 MHz / 1024). Leave the divider logic itself unchanged.

- [ ] **Step 2: Retune the build TCL**

In `vivado/build_aclkgen.tcl`, change `CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {120.000}` to `CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {80.000}`. Update the nearby comment "ONE 120 MHz oversample clock (clk_os)" to "ONE 80 MHz cell clock (clk_os)" and the header comment "deriving a 120 MHz oversample clock" to "deriving an 80 MHz cell clock". Leave all reset/MMCM/port topology unchanged.

- [ ] **Step 3: Confirm the BD-top elaborates and the bd-top tb still matches**

Run an Icarus elaboration of the generator RTL chain (attributes ignored by Icarus):

```bash
iverilog -g2012 -s aclk_gen_bd_top -o /dev/null \
  rtl/aclk_lite/aclk_lite_encoder.sv rtl/aclk_lite/aclk_lite_gen_timeline.sv \
  rtl/aclk_gen_bd_top.v
```

Expected: no output, exit 0. (If `iverilog` is not on PATH, prepend `$OSS_CAD_SUITE/bin`.)

Then run the existing bd-top testbench to confirm it still passes against the new param/clock:

Run: `powershell -ExecutionPolicy Bypass -File ./sim.ps1 run -Module aclk_gen_bd_top`
Expected: PASS. If `tb/aclk_gen_bd_top/test_aclk_gen_bd_top.py` hardcodes the old `OVERSAMPLE`/120 MHz timing or asserts a `clkos_dbg` rate tied to 120 MHz, update those constants to `SAMPLES_PER_CELL=8` / 80 MHz so the test reflects the new clock, then re-run to PASS. If it has no such dependency, no edit is needed.

- [ ] **Step 4: Commit**

```bash
git add rtl/aclk_gen_bd_top.v vivado/build_aclkgen.tcl tb/aclk_gen_bd_top/
git commit -m "feat(gen): retune generator BD + build to 80 MHz cell clock"
```

- [ ] **Step 5: (Heavy, requires Vivado - run by the human) Build the generator bitstream**

Run: `.\hw.ps1 build -Tcl vivado\build_aclkgen.tcl -Name aclkgen`
Expected: synth + impl + write_bitstream + bootgen complete; `hw.ps1` prints BIN + MD5. The bin is `build/kria/aclkgen/aclkgen.runs/impl_1/uart_echo_bd_wrapper.bit.bin`. (Tens of minutes; defer to the human. Then board-to-board: wire the generator's H12 output to the `build_clk` receiver's H12 input and confirm the trio decodes via `clk_read.py`.)

---

## Self-Review

**1. Spec coverage:**
- Biphase-mark encoding + idle 1-cells + 100ns cells + SAMPLES_PER_CELL=8: Task 1 encoder + Global Constraints. Covered.
- Byte-oriented per-byte framing (start+8 MSB-first+even parity), frame types 1/2/12, CRC/control 0x00: Task 1 encoder byte assembly + framebits. Covered.
- event/data/frame_type interface: Task 1 Produces + Task 2 timeline drives it. Covered.
- Same test trio (0x55/0xABCD/0x1234+data): Task 2 timeline localparams + both tests' TRIO. Covered.
- Loopback through the real clk_rcv (serdec + clk_byte_framer): Task 2 tb + test (the key gate). Covered.
- 120 -> 80 MHz generator clock: Task 3 BD top + build. Covered.
- Clean-room receiver + shipped receiver untouched: Global Constraints; Task 2 runner removes aclk_lite_decoder from sources; no task edits clk_rcv/clk_byte_framer/build_clk. Covered.
- HW board-to-board verification: Task 3 Step 5 (deferred to human). Covered.

**2. Placeholder scan:** No TBD/TODO; every code step has complete content; commands have expected output. The CRC/control 0x00 are intentional spec placeholders, not plan placeholders.

**3. Type/name consistency:** Encoder ports `event_id[15:0]/data[63:0]/frame_type[1:0]/start/line/busy` and param `SAMPLES_PER_CELL` are identical in Task 1 (definition), Task 2 (timeline instantiation), and Task 3 (BD top instantiation). The timeline ports `clk/rstn/line/frame_sync` + params `SAMPLES_PER_CELL/IDLE_GAP/TRIO_GAP` match between Task 2 (definition), the loopback tb, and Task 3 (BD top). `clk_rcv` port names in the loopback tb match the shipped `clk_rcv.sv` exactly. The golden helpers `frame_bits` (clk_tx_model) and `event_bits`/`biphase_samples` (tclk_tx_model) match their existing signatures. Consistent.
