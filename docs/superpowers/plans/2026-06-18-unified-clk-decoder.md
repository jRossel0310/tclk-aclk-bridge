# Unified ACLK/TCLK decoder + read-both bitstream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one new KR260 bitstream that decodes BOTH real TCLK and real ACLK-Lite from a single H12 input, by keeping the proven `serdec4_9MHz` front end and replacing the 1-byte `TCLK_DESERIALIZER2` with a length-aware multi-byte framer feeding the shared readout.

**Architecture:** `serdec4_9MHz` (80 MHz biphase recovery, reused) -> new `clk_byte_framer` (40 MHz, byte-oriented length-aware framing) -> adapter -> shared `aclk_readout_axi` -> PS over UIO. Clocking/reset/SmartConnect/16-byte map all reuse the `build_tclk.tcl` skeleton.

**Tech Stack:** SystemVerilog/Verilog (Vivado 2024.2, part `xck26-sfvc784-2LV-c`), cocotb 2.0 + Icarus for sim, Python 3 for the PS reader, PowerShell (`hw.ps1`).

## Global Constraints

- Project style: NEVER use em dashes anywhere (code, comments, docs, commit messages). Use " - " or rephrase.
- On-wire framing is authoritative per `docs/aclk-lite-framing.md`: 100 ns Manchester cells, MSB-first, each byte = start(0) + 8 data + 1 even-parity, bytes back-to-back, frame ends on a terminal idle-1 cell after a byte's parity. Frame length: 1 byte = TCLK event, 2 = ACLK event, 12 = full packet (event 0-1, data 2-9, CRC 10, control 11).
- Do NOT edit any shipped file: `TCLK_DESERIALIZER2.v`, `serdec4_9MHz.v`, `aclk_lite_decoder.sv`, `tclk_readout_top.sv`, `aclk_lite_readout_top.sv`, `tclk_readout_bd_top.v`, `aclk_readout_bd_top.v`, `build_tclk.tcl`, `build_aclk.tcl`, `kr260_tclk.xdc`, `kr260_aclk.xdc`, the shared readout RTL (`aclk_readout_axi.sv`, `aclk_readout_core.sv`, `async_fifo.sv`, `cdc_gray_count.sv`, `synchronizer.sv`), `tclk_tx_model.py`. All new work is additive.
- The decoder output interface is identical to `aclk_lite_decoder`: `event_valid, event_id[15:0], data_valid, data[63:0], parity_error, is_tclk` (all 1-cycle strobes for the valid/error signals).
- `serdec` runs at 80 MHz (`CLK_80M`, period 12500 ps), the framer + readout at 40 MHz (`CLK_40M`, period 25000 ps), `RATE=1` (10 MHz mode).
- Readout uses `DROP_NULL = 0` (no on-wire null code; keep every decoded event).
- Shared AXI register map (16-byte spacing): STATUS 0x00, EVENT 0x10, DATA_HI 0x20, DATA_LO 0x30, TS_HI 0x40, TS_LO 0x50, POP 0x60, EVENT_COUNT 0x70, NULL_COUNT 0x80, ERROR_COUNT 0x90, DEBUG 0xA0, HEARTBEAT 0xB0, LOCK 0xC0, FILTER_CFG 0xD0 (W), FILTERED_COUNT 0xE0 (R).
- External serial line port is named `clkline` everywhere (it is a data line, NOT a clock - do not let it be inferred as a clock).
- Bitstream `design_name = uart_echo_bd`, `proj_name = clk`. Run sims with `.\sim.ps1 run -Module <name>` (Icarus). CRC8 validation is OUT of scope (bytes 10/11 captured but ignored).

---

### Task 1: `clk_byte_framer` + `clk_rcv` + TX model + decoder test

**Files:**
- Create: `rtl/aclk_lite/clk_byte_framer.sv`
- Create: `rtl/aclk_lite/clk_rcv.sv`
- Create: `tb/clk_tx_model.py`
- Create: `tb/clk_rcv/runner.py`
- Create: `tb/clk_rcv/test_clk_rcv.py`

**Interfaces:**
- Consumes: `rtl/aclk_bridge/serdec4_9MHz.v` (ports `RESETn, CLK_80M, TCLK, RATE, SCLK, SDATA, TCLK_CAR, SIG_ERR`); `tb/tclk_tx_model.py` (`event_bits`, `biphase_samples`, `drive_samples`, `SAMPLES_PER_CELL=8`).
- Produces: `clk_byte_framer` (ports `clk, rstn, sclk, sdata, event_valid, event_id[15:0], data_valid, data[63:0], parity_error, is_tclk`); `clk_rcv` (ports `RESETn, CLK_40M, CLK_80M, clkline, event_valid, event_id[15:0], data_valid, data[63:0], parity_error, is_tclk, sig_err`). Tasks 2-4 instantiate `clk_rcv`.

- [ ] **Step 1: Create the TX model helper**

Create `tb/clk_tx_model.py` (extends the proven `tclk_tx_model.py` to multi-byte frames):

```python
"""Real-framing ACLK-Lite / TCLK transmit model for driving rtl/aclk_lite/clk_rcv
in simulation. Builds on the biphase-mark TCLK model (tb/tclk_tx_model.py): a frame
is one or more bytes, each byte = start(0) + 8 data (MSB first) + even parity, sent
back-to-back with NO gap between bytes; the frame ends when idle (logical 1) cells
follow the last byte's parity. Frame length selects the type: 1 byte = TCLK event,
2 = ACLK event, 12 = full ACLK packet (event[0:1] + data[2:9] + CRC[10] + control[11]).
"""
from tclk_tx_model import event_bits, biphase_samples, drive_samples, SAMPLES_PER_CELL, HALF


def frame_bits(byte_list, bad_idx=None):
    """Concatenate per-byte framings (start + 8 + parity) back-to-back. bad_idx flips
    the parity of that byte index (to exercise the error path)."""
    bits = []
    for i, b in enumerate(byte_list):
        bits += event_bits(b, bad_parity=(bad_idx == i))
    return bits


def stream_frames(frames, warmup_cells=40, gap_cells=12, level=1):
    """Idle warm-up, then each frame followed by an idle gap. Each entry in `frames`
    is a list of byte ints, or a (byte_list, bad_idx) tuple for a bad-parity frame."""
    samples, level = biphase_samples([1] * warmup_cells, level)
    for f in frames:
        byte_list, bad_idx = (f if isinstance(f, tuple) else (f, None))
        s, level = biphase_samples(frame_bits(byte_list, bad_idx), level)
        samples += s
        g, level = biphase_samples([1] * gap_cells, level)
        samples += g
    return samples
```

- [ ] **Step 2: Write the failing decoder test**

Create `tb/clk_rcv/runner.py`:

```python
"""Cocotb 2.0 runner for the unified ACLK/TCLK decoder rtl/aclk_lite/clk_rcv
(serdec4_9MHz + clk_byte_framer). Plain Verilog/SV -> Icarus. The line is driven by
the real-framing model in tb/clk_tx_model.py."""
import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "clk_rcv"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))    # shared tb/clk_tx_model.py + tb/tclk_tx_model.py

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_clk_rcv():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "aclk_bridge" / "serdec4_9MHz.v",
            RTL_DIR / "aclk_lite" / "clk_byte_framer.sv",
            RTL_DIR / "aclk_lite" / "clk_rcv.sv",
        ],
        hdl_toplevel="clk_rcv",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(hdl_toplevel="clk_rcv", test_module="test_clk_rcv", build_dir=BUILD, waves=True)


if __name__ == "__main__":
    test_clk_rcv()
```

Create `tb/clk_rcv/test_clk_rcv.py`:

```python
"""Drive real-framing 1/2/12-byte frames at the serial line; check clk_rcv decodes
event_id, data, is_tclk in order, and that a bad-parity frame raises parity_error and
produces no event. serdec needs a warm-up to lock (its transient is driven before
monitoring starts, mirroring tb/tclk_rcv)."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from clk_tx_model import stream_frames, drive_samples, SAMPLES_PER_CELL

WARMUP_CELLS = 40


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


async def reset_dut(dut):
    dut.clkline.value = 1
    dut.RESETn.value = 0
    await ClockCycles(dut.CLK_80M, 10)
    await Timer(1, unit="ns")
    dut.RESETn.value = 1
    await ClockCycles(dut.CLK_80M, 10)


async def monitor(dut, events, perrs):
    while True:
        await RisingEdge(dut.CLK_40M)
        await Timer(1, unit="ns")
        if _b(dut.event_valid) == 1:
            events.append((_b(dut.event_id), _b(dut.is_tclk), _b(dut.data_valid), _b(dut.data)))
        if _b(dut.parity_error) == 1:
            perrs.append(True)


def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.CLK_80M, 12500, unit="ps").start())
    cocotb.start_soon(Clock(dut.CLK_40M, 25000, unit="ps").start())


async def _warmup_then_monitor(dut, frames, events, perrs):
    samples = stream_frames(frames, warmup_cells=WARMUP_CELLS)
    warm_n = WARMUP_CELLS * SAMPLES_PER_CELL
    await drive_samples(dut.CLK_80M, dut.clkline, samples[:warm_n])
    await ClockCycles(dut.CLK_40M, 2)
    cocotb.start_soon(monitor(dut, events, perrs))
    await drive_samples(dut.CLK_80M, dut.clkline, samples[warm_n:])
    await ClockCycles(dut.CLK_40M, 40)


@cocotb.test()
async def test_decode_mixed_frames(dut):
    """A TCLK (1-byte), an ACLK event (2-byte), and a full packet (12-byte) decode in
    order with correct id/data/is_tclk/data_valid."""
    _start_clocks(dut)
    await reset_dut(dut)

    frames = [
        [0x07],                                                            # TCLK event 0x07
        [0x9D, 0xD2],                                                      # ACLK event 0x9DD2
        [0x12, 0x34,                                                       # event 0x1234
         0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE, 0x00, 0x01,                   # data
         0x5A, 0xC3],                                                      # CRC, control (ignored)
    ]
    expected = [
        (0x0007, 1, 0, 0),
        (0x9DD2, 0, 0, 0),
        (0x1234, 0, 1, 0xDEADBEEFCAFE0001),
    ]

    events, perrs = [], []
    await _warmup_then_monitor(dut, frames, events, perrs)

    assert not perrs, f"unexpected parity_error on clean frames: {len(perrs)}"
    assert events == expected, f"decoded {[ (hex(e),t,d,hex(x)) for (e,t,d,x) in events]} != expected"
    dut._log.info(f"unified decode OK: {len(events)} frames (1/2/12-byte) decoded in order")


@cocotb.test()
async def test_parity_error(dut):
    """A 2-byte ACLK frame with a flipped parity bit raises parity_error and yields no
    event; good frames around it still decode."""
    _start_clocks(dut)
    await reset_dut(dut)

    frames = [
        [0x3C],                       # good TCLK
        ([0xAA, 0x55], 1),            # 2-byte frame, byte index 1 has bad parity
        [0x42],                       # good TCLK
    ]
    events, perrs = [], []
    await _warmup_then_monitor(dut, frames, events, perrs)

    ids = [e[0] for e in events]
    assert perrs, "bad-parity frame did not raise parity_error"
    assert 0xAA55 not in ids, f"bad-parity frame leaked an event: {[hex(i) for i in ids]}"
    assert 0x003C in ids and 0x0042 in ids, f"good frames lost: {[hex(i) for i in ids]}"
    dut._log.info("unified parity path OK: parity_error raised, good frames decoded")
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.\sim.ps1 run -Module clk_rcv`
Expected: FAIL - build error, `clk_rcv` / `clk_byte_framer` do not exist yet.

- [ ] **Step 4: Create `clk_byte_framer.sv`**

Create `rtl/aclk_lite/clk_byte_framer.sv`:

```systemverilog
// rtl/aclk_lite/clk_byte_framer.sv
//
// Length-aware byte framer for the unified ACLK/TCLK decoder. Consumes the recovered
// NRZ bit stream from serdec4_9MHz (SCLK strobe + SDATA cell value) and assembles
// real-ISD-framed events: each byte = start(0) + 8 data (MSB first) + even parity,
// bytes back-to-back, a frame ends when the cell after a byte's parity is idle (1).
// Dispatches by accumulated byte count: 1 = TCLK event {0x00,b0}; 2 = ACLK event
// {b0,b1}; 12 = full packet event {b0,b1} + data {b2..b9} (bytes 10/11 = CRC/control,
// captured but ignored). A per-byte parity failure or any other byte count -> a
// one-cycle parity_error with no event. Output interface matches aclk_lite_decoder.
// See docs/aclk-lite-framing.md.

`timescale 1ns / 1ps

module clk_byte_framer (
    input  logic        clk,            // 40 MHz framer clock
    input  logic        rstn,           // async, active-low
    input  logic        sclk,           // recovered bit clock from serdec (one pulse/cell)
    input  logic        sdata,          // recovered NRZ data from serdec
    output logic        event_valid,
    output logic [15:0] event_id,
    output logic        data_valid,
    output logic [63:0] data,
    output logic        parity_error,
    output logic        is_tclk
);
    // Synchronize sclk + sdata into clk and detect sclk rising edge (one pulse/cell).
    logic sclk_cap, sclk_smpl, sclk_edge, sdata_cap, sdata_smpl;
    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            sclk_cap <= 1'b0; sclk_smpl <= 1'b0; sclk_edge <= 1'b0;
            sdata_cap <= 1'b1; sdata_smpl <= 1'b1;
        end else begin
            sclk_cap <= sclk; sclk_smpl <= sclk_cap; sclk_edge <= sclk_smpl;
            sdata_cap <= sdata; sdata_smpl <= sdata_cap;
        end
    end
    wire sclk_pe = sclk_smpl & ~sclk_edge;   // one clk pulse per recovered cell
    wire cell    = sdata_smpl;               // the recovered cell value at sclk_pe

    localparam logic [1:0] ST_IDLE = 2'd0, ST_DATA = 2'd1, ST_PARITY = 2'd2, ST_PEEK = 2'd3;
    logic [1:0]  state;
    logic [2:0]  data_cnt;     // data bits seen in the current byte (0..7)
    logic [7:0]  cur_byte;     // assembling byte (MSB first)
    logic        par_acc;      // running XOR of the current byte's data bits
    logic [3:0]  byte_cnt;     // bytes completed this frame
    logic        frame_bad;    // a per-byte parity error occurred this frame
    logic [95:0] buf;          // up to 12 bytes, first byte ends up in the high slot

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            state <= ST_IDLE;
            data_cnt <= 3'd0; cur_byte <= 8'd0; par_acc <= 1'b0;
            byte_cnt <= 4'd0; frame_bad <= 1'b0; buf <= 96'd0;
            event_valid <= 1'b0; event_id <= 16'd0; data_valid <= 1'b0; data <= 64'd0;
            parity_error <= 1'b0; is_tclk <= 1'b0;
        end else begin
            event_valid  <= 1'b0;          // outputs are 1-cycle strobes
            data_valid   <= 1'b0;
            parity_error <= 1'b0;
            is_tclk      <= 1'b0;
            if (sclk_pe) begin
                case (state)
                    ST_IDLE: begin
                        if (cell == 1'b0) begin            // start cell of byte 0
                            data_cnt  <= 3'd0;
                            par_acc   <= 1'b0;
                            cur_byte  <= 8'd0;
                            byte_cnt  <= 4'd0;
                            frame_bad <= 1'b0;
                            state     <= ST_DATA;
                        end
                    end
                    ST_DATA: begin
                        cur_byte <= {cur_byte[6:0], cell};  // MSB first
                        par_acc  <= par_acc ^ cell;
                        if (data_cnt == 3'd7) state <= ST_PARITY;
                        data_cnt <= data_cnt + 3'd1;
                    end
                    ST_PARITY: begin
                        if (par_acc != cell) frame_bad <= 1'b1;   // even parity: XOR(data)==parity
                        buf      <= {buf[87:0], cur_byte};        // newest byte into low slot
                        byte_cnt <= byte_cnt + 4'd1;
                        state    <= ST_PEEK;
                    end
                    ST_PEEK: begin
                        if (cell == 1'b0) begin            // start of the next byte
                            data_cnt <= 3'd0;
                            par_acc  <= 1'b0;
                            cur_byte <= 8'd0;
                            state    <= ST_DATA;
                        end else begin                     // idle cell -> frame ended
                            state <= ST_IDLE;
                            if (frame_bad) begin
                                parity_error <= 1'b1;
                            end else begin
                                case (byte_cnt)
                                    4'd1: begin
                                        event_id    <= {8'h00, buf[7:0]};
                                        is_tclk     <= 1'b1;
                                        event_valid <= 1'b1;
                                    end
                                    4'd2: begin
                                        event_id    <= buf[15:0];
                                        event_valid <= 1'b1;
                                    end
                                    4'd12: begin
                                        event_id    <= buf[95:80];   // bytes 0,1
                                        data        <= buf[79:16];   // bytes 2..9
                                        event_valid <= 1'b1;
                                        data_valid  <= 1'b1;
                                    end
                                    default: parity_error <= 1'b1;   // malformed length
                                endcase
                            end
                        end
                    end
                    default: state <= ST_IDLE;
                endcase
            end
        end
    end
endmodule
```

- [ ] **Step 5: Create `clk_rcv.sv`**

Create `rtl/aclk_lite/clk_rcv.sv`:

```systemverilog
// rtl/aclk_lite/clk_rcv.sv
//
// Unified ACLK/TCLK receiver: the proven biphase bit recovery (serdec4_9MHz, 80 MHz)
// feeding the length-aware byte framer (clk_byte_framer, 40 MHz). Decodes real TCLK
// (1-byte frames) and real ACLK-Lite (2- and 12-byte frames) from one serial line.
// Counterpart to TCLK_RCV (which pairs serdec with the 1-byte TCLK_DESERIALIZER2).

`timescale 1ns / 1ps

module clk_rcv (
    input  logic        RESETn,
    input  logic        CLK_40M,
    input  logic        CLK_80M,
    input  logic        clkline,        // raw Manchester serial line (ACLK-Lite or TCLK)
    output logic        event_valid,
    output logic [15:0] event_id,
    output logic        data_valid,
    output logic [63:0] data,
    output logic        parity_error,
    output logic        is_tclk,
    output logic        sig_err
);
    wire sclk, sdata;

    serdec4_9MHz u_serdec (
        .RESETn   (RESETn),
        .CLK_80M  (CLK_80M),
        .TCLK     (clkline),
        .RATE     (1'b1),              // 10 MHz mode
        .SCLK     (sclk),
        .SDATA    (sdata),
        .TCLK_CAR (),
        .SIG_ERR  (sig_err)
    );

    clk_byte_framer u_framer (
        .clk          (CLK_40M),
        .rstn         (RESETn),
        .sclk         (sclk),
        .sdata        (sdata),
        .event_valid  (event_valid),
        .event_id     (event_id),
        .data_valid   (data_valid),
        .data         (data),
        .parity_error (parity_error),
        .is_tclk      (is_tclk)
    );
endmodule
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.\sim.ps1 run -Module clk_rcv`
Expected: PASS - `TESTS=2 PASS=2`. Log shows "unified decode OK: 3 frames (1/2/12-byte) decoded in order" and "unified parity path OK".

- [ ] **Step 7: Commit**

```bash
git add rtl/aclk_lite/clk_byte_framer.sv rtl/aclk_lite/clk_rcv.sv tb/clk_tx_model.py tb/clk_rcv/
git commit -m "feat(clk): unified ACLK/TCLK decoder - serdec + length-aware clk_byte_framer"
```

---

### Task 2: `clk_readout_top` + full-chain test

**Files:**
- Create: `rtl/aclk_lite/clk_readout_top.sv`
- Create: `tb/clk_readout/runner.py`
- Create: `tb/clk_readout/test_clk_readout.py`

**Interfaces:**
- Consumes: `clk_rcv` (Task 1); shared `aclk_readout_axi` (params `ADDR_WIDTH`, `AXI_ADDR_W`, `DROP_NULL`; event-side inputs `rx_clk, rx_rstn, pps, aclk_valid, aclk_event[15:0], aclk_data[63:0], flags[15:0], aclk_error, dbg_word[31:0], mmcm_locked`; outputs `dropped_null, dbg_hb`; plus the AXI4-Lite slave); `cdc_gray_count #(.W())`; `tb/clk_tx_model.py`; `tb/axi_lite_bfm.py` (`axi_read`, `axi_write`, `_b`).
- Produces: `clk_readout_top` (params `ADDR_WIDTH=6, AXI_ADDR_W=8`; ports `clk_80m, clk_40m, rstn, pps, clkline, mmcm_locked`, the AXI4-Lite slave, and debug `dbg_event_valid, dbg_event[15:0], dbg_sig_err, dbg_hb, dropped_null`). Task 3 instantiates this.

- [ ] **Step 1: Write the failing full-chain test**

Create `tb/clk_readout/runner.py`:

```python
"""Cocotb 2.0 runner for the full unified PL chain rtl/aclk_lite/clk_readout_top:
serdec + clk_byte_framer -> shared readout -> AXI4-Lite. The serial line is driven by
tb/clk_tx_model.py; AXI is read with tb/axi_lite_bfm.py."""
import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "clk_readout"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))    # shared tb/clk_tx_model.py + tb/axi_lite_bfm.py

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_clk_readout():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "async_fifo.sv",
            RTL_DIR / "cdc_gray_count.sv",
            RTL_DIR / "aclk_readout" / "aclk_readout_core.sv",
            RTL_DIR / "aclk_readout" / "aclk_readout_axi.sv",
            RTL_DIR / "aclk_bridge" / "serdec4_9MHz.v",
            RTL_DIR / "aclk_lite" / "clk_byte_framer.sv",
            RTL_DIR / "aclk_lite" / "clk_rcv.sv",
            RTL_DIR / "aclk_lite" / "clk_readout_top.sv",
        ],
        hdl_toplevel="clk_readout_top",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(hdl_toplevel="clk_readout_top", test_module="test_clk_readout", build_dir=BUILD, waves=True)


if __name__ == "__main__":
    test_clk_readout()
```

Create `tb/clk_readout/test_clk_readout.py`:

```python
"""Full chain: drive real-framing 1/2/12-byte frames at the serial line, then read the
buffered events over AXI4-Lite and check event/data/flags/timestamps/counts. Exercises
serdec -> clk_byte_framer -> timestamp + async FIFO -> AXI, across the rx (40 MHz) and
AXI clock domains."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from clk_tx_model import stream_frames, drive_samples, SAMPLES_PER_CELL
from axi_lite_bfm import axi_read, axi_write

WARMUP_CELLS = 40
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, ERROR_COUNT = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x90)
FLAG_HAS_DATA = 0x1
FLAG_IS_TCLK = 0x2


async def reset_dut(dut):
    dut.clkline.value = 1
    dut.pps.value = 0
    dut.mmcm_locked.value = 1
    dut.rstn.value = 0
    dut.s_axi_aresetn.value = 0
    dut.s_axi_awaddr.value = 0
    dut.s_axi_awvalid.value = 0
    dut.s_axi_wdata.value = 0
    dut.s_axi_wstrb.value = 0
    dut.s_axi_wvalid.value = 0
    dut.s_axi_bready.value = 0
    dut.s_axi_araddr.value = 0
    dut.s_axi_arvalid.value = 0
    dut.s_axi_rready.value = 0
    await ClockCycles(dut.CLK_80M, 10)
    await ClockCycles(dut.s_axi_aclk, 5)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    dut.s_axi_aresetn.value = 1
    await ClockCycles(dut.CLK_80M, 10)
    await ClockCycles(dut.s_axi_aclk, 4)


async def read_one(dut):
    ev_reg = await axi_read(dut, EVENT)
    event = ev_reg & 0xFFFF
    flags = (ev_reg >> 16) & 0xFFFF
    dhi = await axi_read(dut, DATA_HI)
    dlo = await axi_read(dut, DATA_LO)
    thi = await axi_read(dut, TS_HI)
    tlo = await axi_read(dut, TS_LO)
    await axi_write(dut, POP)
    return (event, flags, (dhi << 32) | dlo, (thi << 32) | tlo)


@cocotb.test()
async def test_chain(dut):
    cocotb.start_soon(Clock(dut.CLK_80M, 12500, unit="ps").start())
    cocotb.start_soon(Clock(dut.CLK_40M, 25000, unit="ps").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, 14000, unit="ps").start())
    await reset_dut(dut)

    frames = [
        [0x07],
        [0x9D, 0xD2],
        [0x12, 0x34, 0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE, 0x00, 0x01, 0x5A, 0xC3],
        [0x0F],
    ]
    expected = [
        (0x0007, None, 1),
        (0x9DD2, None, 0),
        (0x1234, 0xDEADBEEFCAFE0001, 0),
        (0x000F, None, 1),
    ]

    samples = stream_frames(frames, warmup_cells=WARMUP_CELLS)
    warm_n = WARMUP_CELLS * SAMPLES_PER_CELL
    await drive_samples(dut.CLK_80M, dut.clkline, samples[:warm_n])
    await drive_samples(dut.CLK_80M, dut.clkline, samples[warm_n:])
    await ClockCycles(dut.CLK_40M, 40)
    await ClockCycles(dut.s_axi_aclk, 8)

    collected = []
    while True:
        if (await axi_read(dut, STATUS)) & 0x1:
            break
        collected.append(await read_one(dut))

    assert (await axi_read(dut, STATUS)) & 0x2 == 0, "overflow set: events dropped"
    assert len(collected) == len(expected), f"read {len(collected)} != {len(expected)}: {collected}"

    last_ts = -1
    for i, ((ev, flags, data, ts), (xev, xdata, xtclk)) in enumerate(zip(collected, expected)):
        has_data = bool(flags & FLAG_HAS_DATA)
        is_tclk = bool(flags & FLAG_IS_TCLK)
        assert ev == xev, f"#{i} event 0x{ev:04X} != 0x{xev:04X}"
        assert is_tclk == bool(xtclk), f"#{i} is_tclk {is_tclk} != {bool(xtclk)}"
        assert has_data == (xdata is not None), f"#{i} has_data {has_data} wrong"
        if xdata is not None:
            assert data == xdata, f"#{i} data 0x{data:016X} != 0x{xdata:016X}"
        assert ts > last_ts, f"#{i} timestamp {ts} not increasing"
        last_ts = ts

    assert (await axi_read(dut, EVENT_COUNT)) == len(expected)
    assert (await axi_read(dut, ERROR_COUNT)) == 0, "ERROR_COUNT nonzero on clean frames"
    dut._log.info(f"full chain OK: {len(collected)} mixed frames decoded + read over AXI")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\sim.ps1 run -Module clk_readout`
Expected: FAIL - `clk_readout_top` does not exist yet.

- [ ] **Step 3: Create `clk_readout_top.sv`**

Create `rtl/aclk_lite/clk_readout_top.sv`:

```systemverilog
// rtl/aclk_lite/clk_readout_top.sv
//
// Unified ACLK/TCLK PL readout: clk_rcv (serdec + clk_byte_framer) -> adapter ->
// shared aclk_readout_axi (timestamp + async FIFO + AXI4-Lite, 16-byte map, filter).
// Reads real TCLK (8-bit events) and real ACLK-Lite (16-bit events + 64-bit data) from
// one serial line on H12. Counterpart to tclk_readout_top / aclk_lite_readout_top.
//
// Adapter: aclk_event = event_id; aclk_data = data when data_valid else 0; flags =
// {.., is_tclk, has_data}; aclk_error = parity_error (already a 1-cycle strobe, so no
// sticky-PERR edge-detect is needed unlike the TCLK_DESERIALIZER2 path). DROP_NULL = 0
// (no on-wire null code). DEBUG word mirrors the TCLK top: serial-line activity.

`timescale 1ns / 1ps

module clk_readout_top #(
    parameter int ADDR_WIDTH = 6,
    parameter int AXI_ADDR_W = 8
) (
    // ---- receive domain ----
    input  logic        clk_80m,
    input  logic        clk_40m,
    input  logic        rstn,
    input  logic        pps,
    input  logic        clkline,        // raw Manchester serial line (LVCMOS33 baseband)
    input  logic        mmcm_locked,

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
    input  logic                   s_axi_rready,

    // ---- debug ----
    output logic        dbg_event_valid,
    output logic [15:0] dbg_event,
    output logic        dbg_sig_err,
    output logic        dbg_hb,
    output logic        dropped_null
);
    // ---- unified decoder ----
    logic        ev_valid, dv, perr, is_tclk, sig_err;
    logic [15:0] ev_id;
    logic [63:0] dat;

    clk_rcv u_rcv (
        .RESETn       (rstn),
        .CLK_40M      (clk_40m),
        .CLK_80M      (clk_80m),
        .clkline      (clkline),
        .event_valid  (ev_valid),
        .event_id     (ev_id),
        .data_valid   (dv),
        .data         (dat),
        .parity_error (perr),
        .is_tclk      (is_tclk),
        .sig_err      (sig_err)
    );

    // ---- adapter ----
    wire [63:0] adapt_data  = dv ? dat : 64'd0;
    wire [15:0] adapt_flags = {14'b0, is_tclk, dv};   // bit0 has_data, bit1 is_tclk

    assign dbg_event_valid = ev_valid;
    assign dbg_event       = ev_id;
    assign dbg_sig_err     = sig_err;

    // ---- serial-line activity diagnostic (-> DEBUG register 0xA0) ----
    // 2FF-sync the raw line into clk_80m, count every transition, cross to the AXI
    // domain with the same Gray counter the readout uses elsewhere.
    logic line_m, line_s, line_s_d;
    always_ff @(posedge clk_80m or negedge rstn) begin
        if (!rstn) begin line_m <= 1'b1; line_s <= 1'b1; line_s_d <= 1'b1; end
        else       begin line_m <= clkline; line_s <= line_m; line_s_d <= line_s; end
    end
    wire line_edge = line_s ^ line_s_d;

    wire [29:0] edge_count;
    cdc_gray_count #(.W(30)) u_cnt_edge (
        .src_clk(clk_80m), .src_rstn(rstn), .incr(line_edge),
        .dst_clk(s_axi_aclk), .count_dst(edge_count));

    logic lvl_m, lvl_s, serr_m, serr_s;
    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin lvl_m <= 1'b0; lvl_s <= 1'b0; serr_m <= 1'b0; serr_s <= 1'b0; end
        else begin lvl_m <= clkline; lvl_s <= lvl_m; serr_m <= sig_err; serr_s <= serr_m; end
    end
    wire [31:0] clk_dbg_word = {serr_s, lvl_s, edge_count};

    // ---- readout + AXI-Lite slave (null-drop disabled) ----
    aclk_readout_axi #(.ADDR_WIDTH(ADDR_WIDTH), .AXI_ADDR_W(AXI_ADDR_W), .DROP_NULL(1'b0)) u_axi (
        .rx_clk        (clk_40m),
        .rx_rstn       (rstn),
        .pps           (pps),
        .aclk_valid    (ev_valid),
        .aclk_event    (ev_id),
        .aclk_data     (adapt_data),
        .flags         (adapt_flags),
        .aclk_error    (perr),
        .dropped_null  (dropped_null),
        .dbg_word      (clk_dbg_word),
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

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\sim.ps1 run -Module clk_readout`
Expected: PASS - `TESTS=1 PASS=1`. Log: "full chain OK: 4 mixed frames decoded + read over AXI".

- [ ] **Step 5: Commit**

```bash
git add rtl/aclk_lite/clk_readout_top.sv tb/clk_readout/
git commit -m "feat(clk): clk_readout_top (unified decoder -> shared readout) + full-chain sim"
```

---

### Task 3: `clk_readout_bd_top.v` (block-design wrapper)

**Files:**
- Create: `rtl/clk_readout_bd_top.v`

**Interfaces:**
- Consumes: `clk_readout_top` (Task 2).
- Produces: module `clk_readout_bd_top` (ports `clk_80m, clk_40m, rstn, clkline, mmcm_locked, clk40_dbg, clk100_dbg, cdc_dbg, dbg_hb`, and the inferred AXI4-Lite slave `S_AXI`). Task 4 instantiates this as module reference `u_clk`.

- [ ] **Step 1: Create the wrapper**

Create `rtl/clk_readout_bd_top.v` (mirrors `rtl/tclk_readout_bd_top.v`; external serial line `clkline`, 80/40 clocks, scope dbg pins):

```verilog
// rtl/clk_readout_bd_top.v
//
// Plain-Verilog block-design wrapper around clk_readout_top (SystemVerilog). The
// X_INTERFACE attributes let Vivado infer the AXI4-Lite slave (S_AXI). pps is tied 0;
// the discrete dbg_* outputs go to Pmod pins for scope bring-up. Counterpart to
// tclk_readout_bd_top.v; decodes both real TCLK and real ACLK-Lite on one line.

`timescale 1ns / 1ps

module clk_readout_bd_top (
    input  wire        clk_80m,
    input  wire        clk_40m,
    input  wire        rstn,
    input  wire        clkline,       // raw Manchester serial line (ACLK-Lite or TCLK)
    input  wire        mmcm_locked,
    output wire        clk40_dbg,     // clk_40m / 1024  -> Pmod pin
    output wire        clk100_dbg,    // s_axi_aclk(pl_clk0) / 1024 -> Pmod pin (control)
    output wire        cdc_dbg,       // a fresh cdc_gray_count's output bit -> Pmod pin
    output wire        dbg_hb,        // deep readout heartbeat[12] -> Pmod pin

    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET s_axi_aresetn" *)
    input  wire        s_axi_aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire        s_axi_aresetn,

    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    input  wire [7:0]  s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input  wire [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output wire [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output wire        s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input  wire [7:0]  s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output wire        s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output wire [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output wire        s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input  wire        s_axi_rready
);

    clk_readout_top #(.ADDR_WIDTH(6), .AXI_ADDR_W(8)) u_clk (
        .clk_80m       (clk_80m),
        .clk_40m       (clk_40m),
        .rstn          (rstn),
        .pps           (1'b0),
        .clkline       (clkline),
        .mmcm_locked   (mmcm_locked),

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
        .s_axi_rready  (s_axi_rready),

        .dbg_event_valid (),
        .dbg_event       (),
        .dbg_sig_err     (),
        .dbg_hb          (dbg_hb),
        .dropped_null    ()
    );

    // ---- clock-alive scope diagnostics (same as tclk_readout_bd_top) ----
    reg [9:0] div40  = 10'd0;
    reg [9:0] div100 = 10'd0;
    always @(posedge clk_40m)    div40  <= div40  + 1'b1;
    always @(posedge s_axi_aclk) div100 <= div100 + 1'b1;
    assign clk40_dbg  = div40[9];
    assign clk100_dbg = div100[9];

    wire [31:0] cdc_test;
    cdc_gray_count #(.W(32)) u_cdc_test (
        .src_clk(clk_40m), .src_rstn(1'b1), .incr(1'b1),
        .dst_clk(s_axi_aclk), .count_dst(cdc_test)
    );
    assign cdc_dbg = cdc_test[12];

endmodule
```

- [ ] **Step 2: Elaborate with Icarus to catch port/syntax errors**

Run (from the repo root; prepend `$OSS_CAD_SUITE/bin` and `$OSS_CAD_SUITE/lib` to PATH if `iverilog` is not found):

```bash
iverilog -g2012 -s clk_readout_bd_top -o /dev/null \
  rtl/synchronizer.sv rtl/async_fifo.sv rtl/cdc_gray_count.sv \
  rtl/aclk_readout/aclk_readout_core.sv rtl/aclk_readout/aclk_readout_axi.sv \
  rtl/aclk_bridge/serdec4_9MHz.v rtl/aclk_lite/clk_byte_framer.sv \
  rtl/aclk_lite/clk_rcv.sv rtl/aclk_lite/clk_readout_top.sv \
  rtl/clk_readout_bd_top.v
```

Expected: no output, exit 0 (clean elaboration; X_INTERFACE attributes are ignored by Icarus).

- [ ] **Step 3: Commit**

```bash
git add rtl/clk_readout_bd_top.v
git commit -m "feat(clk): plain-Verilog BD wrapper clk_readout_bd_top (mirrors tclk one)"
```

---

### Task 4: `kr260_clk.xdc` + `build_clk.tcl`

**Files:**
- Create: `constraints/kr260_clk.xdc`
- Create: `vivado/build_clk.tcl`

**Interfaces:**
- Consumes: module reference `clk_readout_bd_top` (Task 3) with ports `clk_80m, clk_40m, rstn, clkline, mmcm_locked, clk40_dbg, clk100_dbg, cdc_dbg, dbg_hb` + `S_AXI`.
- Produces: a bitstream `uart_echo_bd_wrapper.bit.bin` in `build/kria/clk`.

- [ ] **Step 1: Create the XDC**

Create `constraints/kr260_clk.xdc` (mirrors `kr260_tclk.xdc`; serial line on H12, same scope pins, async groups):

```tcl
## constraints/kr260_clk.xdc - unified ACLK/TCLK serial input
##
## The Manchester serial line (real TCLK or real ACLK-Lite, 3.3V baseband) enters on
## KR260 PMOD1 pin 1 = package H12, LVCMOS33 (same physical pin the TCLK build uses).
set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports clkline]

## Clock-alive scope diagnostics (temporary): divided clk_40m + pl_clk0 to PMOD1 pins.
set_property -dict {PACKAGE_PIN E10 IOSTANDARD LVCMOS33} [get_ports clk40_dbg]
set_property -dict {PACKAGE_PIN E12 IOSTANDARD LVCMOS33} [get_ports clk100_dbg]
set_property -dict {PACKAGE_PIN D10 IOSTANDARD LVCMOS33} [get_ports cdc_dbg]
set_property -dict {PACKAGE_PIN D11 IOSTANDARD LVCMOS33} [get_ports dbg_hb]

## Asynchronous clock groups: the clk_wiz MMCM makes 80/40 MHz (clk_out1/clk_out2) from
## pl_clk0; they are not phase-comparable to the ~100 MHz PS/AXI clock (clk_pl_*). Every
## crossing goes through the readout's async FIFO, so declare the domains asynchronous.
set_clock_groups -name async_ps_vs_rx -asynchronous \
    -group [get_clocks -filter {NAME =~ "clk_pl_*"}] \
    -group [get_clocks -filter {NAME =~ "clk_out*clk_wiz*"}]
```

- [ ] **Step 2: Create the build TCL**

Create `vivado/build_clk.tcl` (mirrors `build_tclk.tcl`; keeps the 80+40 MHz MMCM, swaps sources + module ref + external port). Full content:

```tcl
# vivado/build_clk.tcl - unified ACLK/TCLK readout on the KR260.
#
# PS (pl_clk0 100 AXI + a PL MMCM 80/40 MHz) + the unified readout (clk_readout_bd_top)
# on the LPD AXI master at 0x8000_0000. The serial line enters on H12. Decodes BOTH real
# TCLK and real ACLK-Lite (serdec4_9MHz + clk_byte_framer). Reuses design_name=uart_echo_bd
# so the bitstream name and the overlay load unchanged. Counterpart to build_tclk.tcl,
# which is left untouched.
#
# Build:  .\hw.ps1 build -Tcl vivado\build_clk.tcl -Name clk

set proj_name   clk
set design_name uart_echo_bd
set part        xck26-sfvc784-2LV-c

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set rtl_dir    [file join $root_dir rtl]
set xdc_file   [file join $root_dir constraints kr260_clk.xdc]

if {[info exists ::env(KRIA_BUILD_DIR)] && [string length $::env(KRIA_BUILD_DIR)] > 0} {
    set build_dir $::env(KRIA_BUILD_DIR)
} elseif {[info exists ::env(USERPROFILE)]} {
    set build_dir [file join $::env(USERPROFILE) kria-builds $proj_name]
} elseif {[info exists ::env(HOME)]} {
    set build_dir [file join $::env(HOME) kria-builds $proj_name]
} else {
    set build_dir [file join $script_dir build_$proj_name]
}
puts "INFO: building in $build_dir"

create_project -force $proj_name $build_dir -part $part
set bp [get_board_parts -quiet -filter {NAME =~ "*kr260*"}]
if {[llength $bp] > 0} { set_property board_part [lindex $bp 0] [current_project] }

# RTL sources: shared readout chain + serdec + unified framer/decoder/top + BD top.
add_files -norecurse [list \
    [file join $rtl_dir synchronizer.sv] \
    [file join $rtl_dir async_fifo.sv] \
    [file join $rtl_dir cdc_gray_count.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_core.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_axi.sv] \
    [file join $rtl_dir aclk_bridge serdec4_9MHz.v] \
    [file join $rtl_dir aclk_lite clk_byte_framer.sv] \
    [file join $rtl_dir aclk_lite clk_rcv.sv] \
    [file join $rtl_dir aclk_lite clk_readout_top.sv] \
    [file join $rtl_dir clk_readout_bd_top.v] \
]
set_property file_type SystemVerilog [get_files *.sv]
add_files -fileset constrs_1 -norecurse [list $xdc_file]
update_compile_order -fileset sources_1

create_bd_design $design_name

set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* zynq_ultra_ps_e_0]
if {[llength $bp] > 0} {
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset 1} $ps
}
set_property -dict [list \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
    CONFIG.PSU__USE__M_AXI_GP0 {0} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__USE__M_AXI_GP2 {1} \
] $ps

set clk [create_bd_cell -type module -reference clk_readout_bd_top u_clk]

# PS -> our slave over the LPD master via an AXI SmartConnect (the auto interconnect
# dropped read data for non-16-byte-aligned offsets on hardware). One clock domain.
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* rst_pl0]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]    [get_bd_pins rst_pl0/slowest_sync_clk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins rst_pl0/ext_reset_in]

set sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* axi_sc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]  [get_bd_pins axi_sc/aclk]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins axi_sc/aresetn]

connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins zynq_ultra_ps_e_0/maxihpm0_lpd_aclk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]  [get_bd_pins u_clk/s_axi_aclk]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins u_clk/s_axi_aresetn]

connect_bd_intf_net [get_bd_intf_pins zynq_ultra_ps_e_0/M_AXI_HPM0_LPD] [get_bd_intf_pins axi_sc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_sc/M00_AXI] [get_bd_intf_pins u_clk/S_AXI]

# Clocking Wizard: 80 + 40 MHz from pl_clk0 INSIDE the PL (depend only on pl_clk0). The
# MMCM input takes pl_clk0 with no buffer; feed pl_clk0's EXACT realized Hz as PRIM_IN_FREQ
# or validate_bd_design trips BD 41-238.
set clkw [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:* clk_wiz_0]
set pl0_hz  [get_property CONFIG.FREQ_HZ [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]]
set pl0_mhz [format %.6f [expr {$pl0_hz / 1000000.0}]]
set_property -dict [list \
    CONFIG.PRIM_SOURCE {No_buffer} \
    CONFIG.PRIM_IN_FREQ $pl0_mhz \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {80.000} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {40.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_TYPE {ACTIVE_LOW} \
] $clkw
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins clk_wiz_0/clk_in1]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins clk_wiz_0/resetn]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] [get_bd_pins u_clk/clk_80m]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out2] [get_bd_pins u_clk/clk_40m]
connect_bd_net [get_bd_pins clk_wiz_0/locked] [get_bd_pins u_clk/mmcm_locked]

# Reset: tie the auto proc_sys_reset's dcm_locked HIGH (proven topology). Gating
# s_axi_aresetn on clk_wiz/locked WEDGED the LPD bus on hardware.
set rst [lindex [get_bd_cells -filter {VLNV =~ "*proc_sys_reset*"}] 0]
set vcc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* dcm_locked_const]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $vcc
connect_bd_net [get_bd_pins $vcc/dout] [get_bd_pins $rst/dcm_locked]
connect_bd_net [get_bd_pins $rst/peripheral_aresetn] [get_bd_pins u_clk/rstn]

# External serial line -> H12 (constrained in the XDC).
create_bd_port -dir I clkline
connect_bd_net [get_bd_port clkline] [get_bd_pins u_clk/clkline]

# Clock-alive scope diagnostics out to Pmod pins.
create_bd_port -dir O clk40_dbg
create_bd_port -dir O clk100_dbg
create_bd_port -dir O cdc_dbg
create_bd_port -dir O dbg_hb
connect_bd_net [get_bd_port clk40_dbg]  [get_bd_pins u_clk/clk40_dbg]
connect_bd_net [get_bd_port clk100_dbg] [get_bd_pins u_clk/clk100_dbg]
connect_bd_net [get_bd_port cdc_dbg]    [get_bd_pins u_clk/cdc_dbg]
connect_bd_net [get_bd_port dbg_hb]     [get_bd_pins u_clk/dbg_hb]

assign_bd_address
regenerate_bd_layout
validate_bd_design
save_bd_design

set bd_file [get_files ${design_name}.bd]
make_wrapper -files $bd_file -top -import
set_property top ${design_name}_wrapper [current_fileset]
update_compile_order -fileset sources_1

launch_runs synth_1 -jobs 4
wait_on_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1

set bit [file join $build_dir ${proj_name}.runs impl_1 ${design_name}_wrapper.bit]
if {[file exists $bit]} {
    puts "=========================================================="
    puts "BITSTREAM: $bit"
    puts "=========================================================="
} else {
    puts "ERROR: bitstream not found - check the impl_1 run log."
    exit 1
}
```

- [ ] **Step 3: Structural review against the TCLK build (the gate)**

Run:

```bash
git --no-pager diff --no-index vivado/build_tclk.tcl vivado/build_clk.tcl
git --no-pager diff --no-index constraints/kr260_tclk.xdc constraints/kr260_clk.xdc
```

Expected: differences confined to the intended deltas - `proj_name clk`, the xdc path, the source list (serdec + clk_byte_framer + clk_rcv + clk_readout_top + clk_readout_bd_top instead of the TCLK decoder set), the module reference `clk_readout_bd_top u_clk`, and the external port `tclk` -> `clkline`. The clk_wiz (80+40), SmartConnect, dcm_locked tie-high, reset, and address topology must be unchanged.

- [ ] **Step 4: (Heavy, requires Vivado) Run the build**

Run: `.\hw.ps1 build -Tcl vivado\build_clk.tcl -Name clk`
Expected: synth + impl + `write_bitstream` complete; `hw.ps1` bootgens and prints `BIT:`/`BIN:`/`MD5:`. The bin is `build/kria/clk/clk.runs/impl_1/uart_echo_bd_wrapper.bit.bin`. Confirm final timing is met (WNS >= 0) in the run log. (Takes tens of minutes and needs Vivado 2024.2; the `hw.ps1` AV-retry handles the IPI flake. If Vivado is unavailable here, defer to the user and treat Step 3 as the gate.)

- [ ] **Step 5: Commit**

```bash
git add constraints/kr260_clk.xdc vivado/build_clk.tcl
git commit -m "feat(clk): build_clk.tcl + kr260_clk.xdc (unified ACLK/TCLK board build, H12)"
```

---

### Task 5: PS reader, runbook, and deploy wiring

**Files:**
- Create: `deploy/clk_read.py`
- Create: `deploy/clk.md`
- Modify: `hw.ps1` (the deploy `$pyMap`)

**Interfaces:**
- Consumes: the shared 16-byte AXI register map; `deploy/tclk_filter.py` (`parse_drop_codes`, `filter_cfg_word`).
- Produces: `clk_read.py` (UIO reader, 40 MHz tick) + a `deploy -Name clk` mapping.

- [ ] **Step 1: Create the reader**

Create `deploy/clk_read.py` (drains the shared readout; `TICK_NS=25.0` for 40 MHz; prints 8-bit TCLK and 16-bit ACLK events + 64-bit data):

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
if an AXI read wedges the bus.
"""
import mmap, os, struct, sys, threading, time
from tclk_filter import parse_drop_codes, filter_cfg_word

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

def say(msg):
    print(msg, flush=True)

_args = sys.argv[1:]
_drop_spec = ""
_pos = []
_i = 0
while _i < len(_args):
    if _args[_i] == "--drop" and _i + 1 < len(_args):
        _drop_spec = _args[_i + 1]; _i += 2
    else:
        _pos.append(_args[_i]); _i += 1
DEV = _pos[0] if _pos else "/dev/uio4"
DROP_CODES = parse_drop_codes(_drop_spec)
OFF = 0 if "uio" in DEV else 0x8000_0000

STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xA0
)
HEARTBEAT, LOCK = 0xB0, 0xC0
FILTER_CFG, FILTERED_COUNT = 0xD0, 0xE0
TICK_NS = 25.0  # clk_40m = 40 MHz timestamp tick

NAME = {STATUS: "STATUS", EVENT: "EVENT", DATA_HI: "DATA_HI", DATA_LO: "DATA_LO",
        TS_HI: "TS_HI", TS_LO: "TS_LO", POP: "POP", EVENT_COUNT: "EVENT_COUNT",
        NULL_COUNT: "NULL_COUNT", ERROR_COUNT: "ERROR_COUNT", DEBUG: "DEBUG",
        HEARTBEAT: "HEARTBEAT", LOCK: "LOCK",
        FILTER_CFG: "FILTER_CFG", FILTERED_COUNT: "FILTERED_COUNT"}

_watch = {"label": None, "t": 0.0}

def _watchdog():
    warned = False
    while True:
        time.sleep(0.5)
        lbl = _watch["label"]
        if lbl is None:
            warned = False
            continue
        if (time.monotonic() - _watch["t"]) > 2.0 and not warned:
            say("# !! WATCHDOG: blocked >2s in %s" % lbl)
            say("# !! An AXI read is wedging the bus (reset held / overlay-bitstream not "
                "loaded / wrong address). This is the PL/BD side, NOT this Python script.")
            warned = True

def _enter(label):
    _watch["t"] = time.monotonic()
    _watch["label"] = label

def _leave():
    _watch["label"] = None

def rd(o):
    _enter("read %s (0x%02X)" % (NAME.get(o, "?"), o))
    v = struct.unpack("<I", m[o:o + 4])[0]
    _leave()
    return v

def wr(o, v=0):
    _enter("write %s (0x%02X)" % (NAME.get(o, "?"), o))
    m[o:o + 4] = struct.pack("<I", v & 0xFFFFFFFF)
    _leave()

def read_event():
    ev = rd(EVENT)
    event = ev & 0xFFFF
    flags = (ev >> 16) & 0xFFFF
    data = (rd(DATA_HI) << 32) | rd(DATA_LO)
    ts = (rd(TS_HI) << 32) | rd(TS_LO)
    wr(POP)
    return event, flags, data, ts

def stats_line():
    dbg = rd(DEBUG)
    return "[stats] EVT=%d NULL=%d ERR=%d FILT=%d | line_edges=%d level=%d sig_err=%d | hb=%d lock=%d" % (
        rd(EVENT_COUNT), rd(NULL_COUNT), rd(ERROR_COUNT), rd(FILTERED_COUNT),
        dbg & 0x3FFFFFFF, (dbg >> 30) & 1, (dbg >> 31) & 1,
        rd(HEARTBEAT), rd(LOCK) & 1)

def probe():
    say("# --- startup probe (a freeze here names the wedged offset) ---")
    for o in (STATUS, EVENT_COUNT, ERROR_COUNT, DEBUG):
        say("#   reading %-12s 0x%02X ..." % (NAME[o], o))
        say("#     %-12s = 0x%08X" % (NAME[o], rd(o)))
    lock = rd(LOCK) & 1
    hb1 = rd(HEARTBEAT); time.sleep(0.05); hb2 = rd(HEARTBEAT)
    say("#   MMCM lock (0xC0) = %d   heartbeat (0xB0): %d -> %d (+%d)" % (lock, hb1, hb2, hb2 - hb1))
    if lock != 1:
        say("# --- RED FLAG: MMCM not locked => clk_40m/clk_80m dead; the decoder has no clock. ---")
    elif hb2 != hb1 and hb1 != 0:
        say("# --- TRUST OK: heartbeat moving => AXI counter readback works. line_edges=0 just "
            "means no signal at the pin yet. ---")
    else:
        say("# --- WARNING: MMCM locked but heartbeat STUCK => counter readback broken. ---")
    say("# --- probe complete: AXI reads return, the bus is alive. ---")

say("# opening %s (offset 0x%x) ..." % (DEV, OFF))
fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OFF)
say("# mmap ok (0x1000 bytes). starting watchdog ...")
threading.Thread(target=_watchdog, daemon=True).start()
for _c in DROP_CODES:
    wr(FILTER_CFG, filter_cfg_word(_c))
if DROP_CODES:
    say("# drop-mask: suppressing " + ", ".join("0x%02X" % c for c in DROP_CODES))

say("# streaming ACLK/TCLK events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, OFF))
probe()
say(stats_line())
say("#        ts_ticks    dt_us   event     data               tclk  has_data")

last_ts = None
last_stats = time.monotonic()
try:
    while True:
        if rd(STATUS) & 0x1:
            now = time.monotonic()
            if now - last_stats >= 1.0:
                say(stats_line()); last_stats = now
            time.sleep(0.001)
            continue
        event, flags, data, ts = read_event()
        is_tclk = (flags >> 1) & 1
        has_data = flags & 1
        dt = "   --  " if last_ts is None else "%7.1f" % ((ts - last_ts) * TICK_NS / 1000.0)
        last_ts = ts
        data_str = "0x%016X" % data if has_data else "       --         "
        say("  %16d %s   0x%04X  %s    %d      %d" % (ts, dt, event, data_str, is_tclk, has_data))
except KeyboardInterrupt:
    say("\n# stopped.")
    say(stats_line())
```

- [ ] **Step 2: Compile-check the reader**

Run: `python -m py_compile deploy/clk_read.py deploy/tclk_filter.py`
Expected: no output, exit 0. (If `python` is absent, try `python3` or `py`.)

- [ ] **Step 3: Add the deploy mapping in `hw.ps1`**

In `hw.ps1`, the deploy task's `$pyMap` currently reads:

```powershell
        $pyMap = @{
            "tclk"      = @("tclk_read.py", "tclk_filter.py")
            "aclk"      = @("aclk_read.py", "tclk_filter.py")
            "uart_echo" = @("uart_echo_test.py")
        }
```

Add the `clk` entry:

```powershell
        $pyMap = @{
            "tclk"      = @("tclk_read.py", "tclk_filter.py")
            "aclk"      = @("aclk_read.py", "tclk_filter.py")
            "clk"       = @("clk_read.py", "tclk_filter.py")
            "uart_echo" = @("uart_echo_test.py")
        }
```

(If the `aclk` line is not present because that work landed differently, still add the `clk` line; the only requirement is a `"clk" = @("clk_read.py", "tclk_filter.py")` entry exists.)

- [ ] **Step 4: Create the runbook**

Create `deploy/clk.md`:

```markdown
# Unified ACLK/TCLK readout on the KR260

One bitstream that decodes BOTH real TCLK and real ACLK-Lite from a single H12 input
(serdec4_9MHz + clk_byte_framer -> shared readout). The shipped tclk/aclk builds are
unchanged; this is the unified target.

## Build (PC, Vivado 2024.2)

    .\hw.ps1 build -Tcl vivado\build_clk.tcl -Name clk

Produces `build/kria/clk/clk.runs/impl_1/uart_echo_bd_wrapper.bit.bin` (+ MD5).

## Deploy

    .\hw.ps1 deploy -Name clk -DeployHost ubuntu@<host>

Copies the bin, `clk_read.py`, and `tclk_filter.py` to ~ on the board.

## Load + read

    md5sum ~/uart_echo_bd_wrapper.bit.bin     # must equal the PC-side MD5
    sudo xmutil unloadapp
    sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo
    sudo python3 -u clk_read.py /dev/uio4

Each decoded event prints `ts_ticks dt_us event data tclk has_data`. Plug the real
office TCLK into H12 -> 8-bit events (tclk=1) matching the TCLK event table; plug the
generator (after it is reflashed to the real framing) -> 16-bit ACLK events + 64-bit
data. `--drop 07,0F` suppresses event codes. line_edges climbs whenever H12 toggles.

## Input

Same H12 pin (PMOD1 pin 1, LVCMOS33) as the TCLK build; swap the cable between the real
TCLK source and the generator. Both must be the real ISD Manchester framing; the decoder
auto-detects frame length (1 byte = TCLK, 2 = ACLK event, 12 = event + data).
```

- [ ] **Step 5: Commit**

```bash
git add deploy/clk_read.py deploy/clk.md hw.ps1
git commit -m "feat(clk): PS reader (clk_read.py), runbook, and hw.ps1 deploy mapping"
```

---

## Phase 2 (follow-on, separate effort): generator re-alignment

Not part of this plan's task list - it lives on the generator branch/board and needs its
own brief once that branch is active. Requirements (from the spec): change the generator's
Manchester encoder to emit the REAL ISD framing - `serdec`-compatible biphase cells at
100 ns, per-byte (start 0 + 8 data MSB-first + even parity), bytes back-to-back with no
inter-byte stop, 2 terminal idle-1 cells at frame end, frame types of 1 / 2 / 12 bytes
(the 12-byte packet = event[0:1] + data[2:9] + a CRC8 byte + a control byte; the CRC may
be a fixed placeholder since the decoder ignores it). Validate by a loopback sim:
generator encoder -> `serdec4_9MHz` -> `clk_byte_framer` -> decoded events match injected.
Until then, the unified decoder is HW-verifiable on the real office TCLK immediately; ACLK
HW verification waits for this reflash.

## Self-Review

**Spec coverage:**
- Unified `serdec`-fed decoder (framer + rcv): Task 1. Covered.
- `clk_readout_top` (adapter -> shared readout, DROP_NULL=0, parity_error direct, DEBUG word): Task 2. Covered.
- BD wrapper: Task 3. Covered.
- Board build (build_clk.tcl, kr260_clk.xdc, 80/40 MHz, H12, SmartConnect, dcm high, mmcm_locked): Task 4. Covered.
- PS reader (40 MHz tick, 16-bit/64-bit format) + runbook + deploy map: Task 5. Covered.
- Framing rules (byte-oriented, per-byte parity, 2 terminal cells, 1/2/12-byte dispatch, MSB-first, CRC ignored): encoded in `clk_byte_framer` (Task 1) + the TX model + tests. Covered.
- Shipped files untouched: Global Constraints + Task 4 Step 3 diffs against the TCLK build. Covered.
- Testing (unit-via-serdec, full chain, HW on real TCLK now / ACLK after generator): Tasks 1-4 + Phase 2 note. Covered.
- Generator re-alignment: Phase 2 note (sequenced second, separate branch) per the spec's sequencing decision. Covered.
- CRC8 deferred: Global Constraints + framer ignores bytes 10/11. Covered.

**Placeholder scan:** No TBD/TODO; every code step has complete content; commands have expected output.

**Type/name consistency:** `clk_byte_framer` ports (`clk,rstn,sclk,sdata,event_valid,event_id[15:0],data_valid,data[63:0],parity_error,is_tclk`) match `clk_rcv`'s instantiation (Task 1); `clk_rcv` ports (`RESETn,CLK_40M,CLK_80M,clkline,...,sig_err`) match `clk_readout_top`'s instantiation (Task 2); `clk_readout_top` ports match `clk_readout_bd_top`'s instantiation (Task 3) and the `connect_bd_*`/`create_bd_port` calls (Task 4); register offsets in `clk_read.py` match the shared map; external line port is `clkline` consistently across RTL/BD/TCL/XDC. `serdec4_9MHz` ports match its source. Consistent.
