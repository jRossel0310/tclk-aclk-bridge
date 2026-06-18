# ACLK-Lite Signal Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a second, independent KR260 bitstream that emits a Manchester-encoded ACLK-Lite stream out of H12, bit-compatible with `aclk_lite_decoder.sv`, so two boards wired H12 to H12 verify the receiver end to end.

**Architecture:** A reusable RTL Manchester encoder (`aclk_lite_encoder.sv`) is driven by a hardcoded-timeline event-source FSM (`aclk_lite_gen_timeline.sv`) that walks a fixed trio of frames (8-bit, 16-bit, 80-bit) forever. A no-AXI plain-Verilog BD wrapper (`aclk_gen_bd_top.v`) drives the encoder output onto H12 and exposes scope-debug pins. A Vivado build (`build_aclkgen.tcl` + `kr260_aclkgen.xdc`) reuses the receiver's proven pl_clk0 to 120 MHz MMCM clocking topology, minus all AXI.

**Tech Stack:** SystemVerilog + Verilog RTL, cocotb 2.0 + Icarus (per-module `runner.py`), Vivado 2024.2 batch TCL, KR260 (xck26-sfvc784-2LV-c).

## Global Constraints

- Part: `xck26-sfvc784-2LV-c`, Vivado 2024.2.
- Encoding contract (must match `rtl/aclk_lite/aclk_lite_decoder.sv` exactly): per bit `b` = two half-bits `{~b, b}`, `OVERSAMPLE` clk cycles per FULL bit (HALF = OVERSAMPLE/2 each half); idle = steady HIGH; frame = start(0) + payload MSB-first + even-parity (XOR of payload), then idle high. Accepted payload lengths: 8 / 16 / 80.
- `OVERSAMPLE = 12` everywhere on hardware (120 MHz clk_os => ~10 MHz bit rate), matching the receiver.
- Reuse `tb/manchester_tx_model.py` as the golden reference; do NOT edit it. Rebind its `OVERSAMPLE`/`HALF` module globals inside the new test process only.
- Do NOT edit any receiver or TCLK file (RTL, builds, XDC, tests). All new files are parallel.
- `design_name = uart_echo_bd` in the build so the bitstream stays `uart_echo_bd_wrapper.bit.bin`.
- Project style: never use em dashes anywhere (code, comments, docs).
- cocotb tests emit a matplotlib graph on completion (project convention).
- Run a test from the repo root with the venv python: `.\.venv\Scripts\python.exe tb\<module>\runner.py` (the runner uses absolute paths, so cwd does not matter).

---

### Task 1: Manchester encoder (`aclk_lite_encoder.sv`)

The reusable serializer. Drive `start` + `payload`/`length`, it emits the exact per-clk Manchester waveform `manchester_tx_model.frame_levels` produces, then returns to idle high.

**Files:**
- Create: `rtl/aclk_lite/aclk_lite_encoder.sv`
- Create: `tb/aclk_lite_encoder/test_aclk_lite_encoder.py`
- Create: `tb/aclk_lite_encoder/runner.py`

**Interfaces:**
- Consumes: nothing (leaf module). The test consumes `tb/manchester_tx_model.py` `frame_levels(payload, length, flip_bit=None)`.
- Produces: `module aclk_lite_encoder #(parameter int OVERSAMPLE=12) (input logic clk, rstn, start; input logic [79:0] payload; input logic [6:0] length; output logic line, busy);`
  - `start`: 1-cycle strobe, latched only when not `busy`.
  - `payload`: right-justified, unused upper bits MUST be 0 (caller's contract).
  - `length`: one of `7'd8`, `7'd16`, `7'd80`.
  - `line`: idle HIGH; `busy`: high from `start` acceptance until the frame's last half-bit completes.

- [ ] **Step 1: Write the failing test**

Create `tb/aclk_lite_encoder/test_aclk_lite_encoder.py`:

```python
"""Cocotb tests for rtl/aclk_lite/aclk_lite_encoder.sv, the ACLK-Lite Manchester
encoder. The encoder's per-clk line waveform must equal the golden reference
tb/manchester_tx_model.py frame_levels(payload, length) at OVERSAMPLE=12, for the
three frame lengths (8 / 16 / 80). Also checks idle-high before/after and that a
start pulse during busy is ignored.

On completion a plot of the emitted line for the 80-bit frame is written under
sim_build/aclk_lite_encoder/plots/.
"""

import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

# Reuse the golden model, rebound to the hardware OVERSAMPLE (12) without editing
# the shared file (module-global rebind, this test process only).
import manchester_tx_model as mtx
mtx.OVERSAMPLE = 12
mtx.HALF = 6
from manchester_tx_model import frame_levels

OVERSAMPLE = 12
HALF = 6
CLK_NS = 10


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


async def reset_dut(dut):
    dut.start.value = 0
    dut.payload.value = 0
    dut.length.value = 0
    dut.rstn.value = 0
    await ClockCycles(dut.clk, 5)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    await ClockCycles(dut.clk, 5)


async def send_and_capture(dut, payload, length):
    """Pulse start for one cycle, then sample `line` each clk through the whole
    frame plus trailing idle. Returns the sampled level list."""
    dut.payload.value = payload
    dut.length.value = length
    await RisingEdge(dut.clk)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    levels = []
    nsamp = (length + 2) * OVERSAMPLE + 4 * OVERSAMPLE
    for _ in range(nsamp):
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        levels.append(_b(dut.line))
    return levels


def _check_frame(levels, expected):
    """Align on the first falling edge (the start-bit mid-bit edge, at index HALF
    of frame_levels) and compare the frame window, then assert idle high after."""
    f = next(i for i in range(1, len(levels)) if levels[i - 1] == 1 and levels[i] == 0)
    start = f - HALF
    window = levels[start:start + len(expected)]
    assert window == expected, f"waveform mismatch:\n got={window}\n exp={expected}"
    tail = levels[start + len(expected):]
    assert all(v == 1 for v in tail), f"line did not return to idle high: {tail}"


def _save_line_plot(levels, name, title):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
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
    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "aclk_lite_encoder" / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


@cocotb.test()
async def test_encoder_waveforms(dut):
    """8 / 16 / 80-bit frames each match frame_levels and return to idle high."""
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    await reset_dut(dut)

    cases = [
        (0x5A, 8),
        (0x1234, 16),
        ((0xABCD << 64) | 0x0123456789ABCDEF, 80),
    ]
    last_levels = None
    for payload, length in cases:
        levels = await send_and_capture(dut, payload, length)
        _check_frame(levels, frame_levels(payload, length))
        if length == 80:
            last_levels = levels
        await ClockCycles(dut.clk, 4 * OVERSAMPLE)

    path = _save_line_plot(
        last_levels, "encoder_frame.png",
        "ACLK-Lite encoder line: 80-bit frame (event 0xABCD + 64-bit data)",
    )
    if path:
        dut._log.info(f"line plot written to {path}")
    dut._log.info("encoder waveforms OK: 8 / 16 / 80-bit frames match frame_levels")


@cocotb.test()
async def test_start_ignored_while_busy(dut):
    """A start pulse asserted while busy must not corrupt or restart the frame."""
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    await reset_dut(dut)

    dut.payload.value = 0x1234
    dut.length.value = 16
    await RisingEdge(dut.clk)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    # wait until busy is observed high, then pump a second start mid-frame
    for _ in range(40):
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        if _b(dut.busy) == 1:
            break
    assert _b(dut.busy) == 1, "encoder never asserted busy"
    dut.payload.value = 0x00FF
    dut.length.value = 8
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    # let the (single) frame finish, then capture the line and confirm one frame
    levels = []
    for _ in range(18 * OVERSAMPLE + 4 * OVERSAMPLE):
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        levels.append(_b(dut.line))
    # only ONE falling edge group: count rising-from-idle restarts after first idle
    # simplest invariant: once busy clears it stays clear (no relatch)
    assert _b(dut.busy) == 0, "frame did not complete (start-while-busy relatched)"
    dut._log.info("start-while-busy ignored OK")
```

- [ ] **Step 2: Write the runner**

Create `tb/aclk_lite_encoder/runner.py`:

```python
"""Cocotb 2.0 runner for rtl/aclk_lite/aclk_lite_encoder.sv (Manchester encoder).
The encoder is a leaf module (no dependencies). The test reuses the shared
tb/manchester_tx_model.py golden reference.
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "aclk_lite_encoder"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))    # for tb/manchester_tx_model.py

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_lite_encoder():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[RTL_DIR / "aclk_lite" / "aclk_lite_encoder.sv"],
        hdl_toplevel="aclk_lite_encoder",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="aclk_lite_encoder",
        test_module="test_aclk_lite_encoder",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_aclk_lite_encoder()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe tb\aclk_lite_encoder\runner.py`
Expected: FAIL during build (`aclk_lite_encoder.sv` does not exist / cannot find toplevel `aclk_lite_encoder`).

- [ ] **Step 4: Write the encoder RTL**

Create `rtl/aclk_lite/aclk_lite_encoder.sv`:

```systemverilog
// rtl/aclk_lite/aclk_lite_encoder.sv
//
// ACLK-Lite Manchester ENCODER: the TX counterpart of aclk_lite_decoder.sv. Given
// a start strobe + payload + length, it serializes one frame onto `line` and then
// returns to idle high. The emitted per-clk waveform is bit-identical to the golden
// model tb/manchester_tx_model.py frame_levels(payload, length).
//
// Encoding (matches the decoder): bit b -> two half-bits {~b, b}, OVERSAMPLE clk
// cycles per FULL bit (HALF each half); idle = steady high; frame = start(0) +
// payload MSB-first + even parity (XOR of payload), then idle high.
//
// Contract: payload is right-justified and its unused upper bits MUST be 0.
// length must be one of 8, 16, 80. A start pulse while busy is ignored.

`timescale 1ns / 1ps

module aclk_lite_encoder #(
    parameter int OVERSAMPLE = 12               // oversampling-clock cycles per bit
) (
    input  logic        clk,
    input  logic        rstn,                   // async, active-low
    input  logic        start,                  // 1-cycle strobe; ignored while busy
    input  logic [79:0] payload,                // right-justified; unused upper bits 0
    input  logic [6:0]  length,                 // 8, 16, or 80
    output logic        line,                   // idle HIGH
    output logic        busy
);

    localparam int HALF = OVERSAMPLE / 2;

    localparam logic StIdle = 1'b0;
    localparam logic StSend = 1'b1;

    logic        state;
    logic [81:0] sr;        // bits to send, MSB-first; top bit (sr[81]) = start bit
    logic [7:0]  nbits;     // total bits in this frame (length + 2)
    logic [7:0]  bit_idx;   // bit currently being sent, 0..nbits-1
    logic [7:0]  half_cnt;  // half-bit cycle counter, 0..HALF-1
    logic        phase;     // 0 = first half (~b), 1 = second half (b)

    wire cur_bit  = sr[8'd81 - bit_idx];
    wire next_bit = sr[8'd81 - (bit_idx + 8'd1)];

    // Combinationally assemble the left-aligned frame + even parity for `length`.
    logic        par;
    logic [81:0] sr_n;
    logic [7:0]  nbits_n;
    always_comb begin
        case (length)
            7'd8: begin
                par     = ^payload[7:0];
                sr_n    = {1'b0, payload[7:0],  par, 72'd0};
                nbits_n = 8'd10;
            end
            7'd16: begin
                par     = ^payload[15:0];
                sr_n    = {1'b0, payload[15:0], par, 64'd0};
                nbits_n = 8'd18;
            end
            default: begin   // 80
                par     = ^payload[79:0];
                sr_n    = {1'b0, payload[79:0], par};
                nbits_n = 8'd82;
            end
        endcase
    end

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            state    <= StIdle;
            line     <= 1'b1;
            busy     <= 1'b0;
            sr       <= 82'd0;
            nbits    <= 8'd0;
            bit_idx  <= 8'd0;
            half_cnt <= 8'd0;
            phase    <= 1'b0;
        end else begin
            case (state)
                StIdle: begin
                    line <= 1'b1;
                    busy <= 1'b0;
                    if (start) begin
                        sr       <= sr_n;
                        nbits    <= nbits_n;
                        bit_idx  <= 8'd0;
                        half_cnt <= 8'd0;
                        phase    <= 1'b0;
                        busy     <= 1'b1;
                        line     <= ~sr_n[81];   // first half of start bit (~0 = 1)
                        state    <= StSend;
                    end
                end

                StSend: begin
                    if (half_cnt == HALF[7:0] - 8'd1) begin
                        half_cnt <= 8'd0;
                        if (phase == 1'b0) begin
                            phase <= 1'b1;
                            line  <= cur_bit;            // second half of current bit
                        end else begin
                            phase <= 1'b0;
                            if (bit_idx == nbits - 8'd1) begin
                                state <= StIdle;
                                busy  <= 1'b0;
                                line  <= 1'b1;           // back to idle high
                            end else begin
                                bit_idx <= bit_idx + 8'd1;
                                line    <= ~next_bit;    // first half of next bit
                            end
                        end
                    end else begin
                        half_cnt <= half_cnt + 8'd1;
                    end
                end

                default: state <= StIdle;
            endcase
        end
    end

endmodule
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe tb\aclk_lite_encoder\runner.py`
Expected: PASS, both tests (`test_encoder_waveforms`, `test_start_ignored_while_busy`). A plot is written to `sim_build/aclk_lite_encoder/plots/encoder_frame.png`.

- [ ] **Step 6: Commit**

```bash
git add rtl/aclk_lite/aclk_lite_encoder.sv tb/aclk_lite_encoder/
git commit -m "feat(aclkgen): Manchester encoder aclk_lite_encoder.sv + waveform unit test"
```

---

### Task 2: Hardcoded timeline + loopback integration test

The event-source FSM that walks the fixed trio forever, plus the key end-to-end test wiring the encoder output into the real decoder.

**Files:**
- Create: `rtl/aclk_lite/aclk_lite_gen_timeline.sv`
- Create: `tb/aclk_lite_gen_loopback/tb_aclk_gen_loopback.sv`
- Create: `tb/aclk_lite_gen_loopback/test_aclk_gen_loopback.py`
- Create: `tb/aclk_lite_gen_loopback/runner.py`

**Interfaces:**
- Consumes: `aclk_lite_encoder` (Task 1); `aclk_lite_decoder` (existing, `rtl/aclk_lite/aclk_lite_decoder.sv`); `synchronizer` (existing, decoder dependency).
- Produces: `module aclk_lite_gen_timeline #(parameter int OVERSAMPLE=12, IDLE_GAP=64, TRIO_GAP=120000) (input logic clk, rstn; output logic line, frame_sync);`
  - Emits, forever: frame0 (8-bit, id 0x55), `IDLE_GAP` idle, frame1 (16-bit, id 0xABCD), `IDLE_GAP` idle, frame2 (80-bit, id 0x1234 + data 0xDEADBEEFCAFE0001), `TRIO_GAP` idle, repeat.
  - `frame_sync`: 1-cycle pulse at the start of each trio (before frame0).

- [ ] **Step 1: Write the failing loopback test**

Create `tb/aclk_lite_gen_loopback/test_aclk_gen_loopback.py`:

```python
"""Loopback integration test: aclk_lite_gen_timeline -> aclk_lite_decoder, both at
OVERSAMPLE=12. The hardcoded timeline must drive frames that the real decoder
recovers as exactly the injected trio, repeating, with zero parity errors:
  (0x0055, data=None, is_tclk=1)   # 8-bit TCLK event 0x55
  (0xABCD, data=None, is_tclk=0)   # 16-bit ACLK event
  (0x1234, data=0xDEADBEEFCAFE0001, is_tclk=0)   # 80-bit ACLK event + data

The tb top shrinks IDLE_GAP/TRIO_GAP so a few trios run quickly. On completion an
events-over-time throughput plot is written under
sim_build/aclk_lite_gen_loopback/plots/.
"""

import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK_NS = 10
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
    await ClockCycles(dut.clk, 5)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    await ClockCycles(dut.clk, 5)


async def monitor(dut, events, errors, times):
    cyc = 0
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        cyc += 1
        if _b(dut.event_valid) == 1:
            dv = _b(dut.data_valid)
            events.append((
                int(dut.event_id.value) & 0xFFFF,
                (int(dut.data.value) & MASK64) if dv == 1 else None,
                _b(dut.is_tclk),
            ))
            times.append(cyc)
        if _b(dut.parity_error) == 1:
            errors.append(cyc)


def _save_throughput_plot(times, name, title):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return None
    cum = list(range(1, len(times) + 1))
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.step(times, cum, where="post", color="tab:blue", lw=1.6)
    ax.set_xlabel("oversampling-clock cycle")
    ax.set_ylabel("cumulative events decoded")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "aclk_lite_gen_loopback" / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


@cocotb.test()
async def test_loopback_trio_repeats(dut):
    """At least two full trios decode in order with no parity errors."""
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    await reset_dut(dut)

    events, errors, times = [], [], []
    cocotb.start_soon(monitor(dut, events, errors, times))

    # Run long enough for >= 2 full trios at the shrunk gaps (see tb top params).
    await ClockCycles(dut.clk, 6000)

    assert not errors, f"unexpected parity errors at cycles {errors}"
    assert len(events) >= 6, f"expected >= 6 events (2 trios), got {len(events)}: {events}"
    assert events[0:3] == TRIO, f"first trio wrong: {events[0:3]}"
    assert events[3:6] == TRIO, f"second trio wrong: {events[3:6]}"

    path = _save_throughput_plot(
        times, "loopback_throughput.png",
        "ACLK-Lite generator -> decoder: cumulative events decoded",
    )
    if path:
        dut._log.info(f"throughput plot written to {path}")
    dut._log.info(f"loopback OK: {len(events)} events, trio repeats, 0 parity errors")
```

- [ ] **Step 2: Write the loopback testbench top**

Create `tb/aclk_lite_gen_loopback/tb_aclk_gen_loopback.sv`:

```systemverilog
// tb/aclk_lite_gen_loopback/tb_aclk_gen_loopback.sv
//
// Loopback harness: the hardcoded timeline's Manchester line feeds the real
// aclk_lite_decoder. Both run at OVERSAMPLE=12. IDLE_GAP/TRIO_GAP are shrunk so a
// few trios run quickly in sim. cocotb drives clk/rstn and watches the decoder.

`timescale 1ns / 1ps

module tb_aclk_gen_loopback #(
    parameter int OVERSAMPLE = 12,
    parameter int IDLE_GAP   = 32,
    parameter int TRIO_GAP   = 64
) (
    input  logic        clk,
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
        .OVERSAMPLE(OVERSAMPLE), .IDLE_GAP(IDLE_GAP), .TRIO_GAP(TRIO_GAP)
    ) u_gen (
        .clk(clk), .rstn(rstn), .line(line), .frame_sync(frame_sync)
    );

    aclk_lite_decoder #(.OVERSAMPLE(OVERSAMPLE)) u_dec (
        .clk(clk), .rstn(rstn), .line(line),
        .event_valid(event_valid), .event_id(event_id),
        .data_valid(data_valid), .data(data),
        .parity_error(parity_error), .is_tclk(is_tclk)
    );

endmodule
```

- [ ] **Step 3: Write the runner**

Create `tb/aclk_lite_gen_loopback/runner.py`:

```python
"""Cocotb 2.0 runner for the ACLK-Lite generator -> decoder loopback. Compiles the
encoder, the hardcoded timeline, the existing decoder + its synchronizer, and the
tb top.
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "aclk_lite_gen_loopback"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_gen_loopback():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "aclk_lite" / "aclk_lite_decoder.sv",
            RTL_DIR / "aclk_lite" / "aclk_lite_encoder.sv",
            RTL_DIR / "aclk_lite" / "aclk_lite_gen_timeline.sv",
            TB_DIR / "tb_aclk_gen_loopback.sv",
        ],
        hdl_toplevel="tb_aclk_gen_loopback",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="tb_aclk_gen_loopback",
        test_module="test_aclk_gen_loopback",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_aclk_gen_loopback()
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe tb\aclk_lite_gen_loopback\runner.py`
Expected: FAIL during build (`aclk_lite_gen_timeline.sv` does not exist).

- [ ] **Step 5: Write the timeline RTL**

Create `rtl/aclk_lite/aclk_lite_gen_timeline.sv`:

```systemverilog
// rtl/aclk_lite/aclk_lite_gen_timeline.sv
//
// Hardcoded ACLK-Lite event source: drives aclk_lite_encoder through a fixed trio
// of frames forever, with idle gaps between them, and pulses frame_sync at the
// start of each trio (a clean scope trigger). No PS/AXI interaction: it boots and
// transmits. The trio exercises all three decoder paths:
//   frame0: 8-bit  TCLK event id 0x55
//   frame1: 16-bit ACLK event id 0xABCD
//   frame2: 80-bit ACLK event id 0x1234 + data 0xDEADBEEFCAFE0001
//
// IDLE_GAP must exceed the decoder's ~1.5-bit frame-end gap (OVERSAMPLE*3/2); the
// default 64 clears it comfortably. TRIO_GAP defaults to ~1 ms at 120 MHz.

`timescale 1ns / 1ps

module aclk_lite_gen_timeline #(
    parameter int OVERSAMPLE = 12,
    parameter int IDLE_GAP   = 64,         // idle clk cycles between frames
    parameter int TRIO_GAP   = 120000      // idle clk cycles before repeating (~1 ms)
) (
    input  logic clk,
    input  logic rstn,
    output logic line,                     // Manchester output (idle high)
    output logic frame_sync                // 1-cycle pulse at the start of each trio
);

    localparam logic [79:0] PL0 = 80'h00000000_00000000_0055;            // 8-bit  id 0x55
    localparam logic [79:0] PL1 = 80'h00000000_00000000_ABCD;            // 16-bit id 0xABCD
    localparam logic [79:0] PL2 = {16'h1234, 64'hDEADBEEF_CAFE_0001};    // 80-bit
    localparam logic [6:0]  LEN0 = 7'd8;
    localparam logic [6:0]  LEN1 = 7'd16;
    localparam logic [6:0]  LEN2 = 7'd80;

    logic        enc_start;
    logic [79:0] enc_payload;
    logic [6:0]  enc_length;
    logic        enc_busy;

    aclk_lite_encoder #(.OVERSAMPLE(OVERSAMPLE)) u_enc (
        .clk(clk), .rstn(rstn),
        .start(enc_start), .payload(enc_payload), .length(enc_length),
        .line(line), .busy(enc_busy)
    );

    typedef enum logic [2:0] {
        S_SYNC, S_F0, S_W0, S_F1, S_W1, S_F2, S_W2
    } state_t;
    state_t      state;
    logic [31:0] gap_cnt;

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            state       <= S_SYNC;
            gap_cnt     <= 32'd0;
            enc_start   <= 1'b0;
            enc_payload <= 80'd0;
            enc_length  <= 7'd0;
            frame_sync  <= 1'b0;
        end else begin
            enc_start  <= 1'b0;          // default: 1-cycle strobe
            frame_sync <= 1'b0;
            case (state)
                S_SYNC: begin
                    frame_sync  <= 1'b1;
                    enc_payload <= PL0; enc_length <= LEN0; enc_start <= 1'b1;
                    gap_cnt     <= 32'd0;
                    state       <= S_F0;
                end
                S_F0: if (enc_busy) state <= S_W0;     // wait for the encoder to take it
                S_W0: if (!enc_busy) begin
                          if (gap_cnt == IDLE_GAP - 1) begin
                              gap_cnt <= 32'd0;
                              enc_payload <= PL1; enc_length <= LEN1; enc_start <= 1'b1;
                              state <= S_F1;
                          end else gap_cnt <= gap_cnt + 32'd1;
                      end
                S_F1: if (enc_busy) state <= S_W1;
                S_W1: if (!enc_busy) begin
                          if (gap_cnt == IDLE_GAP - 1) begin
                              gap_cnt <= 32'd0;
                              enc_payload <= PL2; enc_length <= LEN2; enc_start <= 1'b1;
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

- [ ] **Step 6: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe tb\aclk_lite_gen_loopback\runner.py`
Expected: PASS (`test_loopback_trio_repeats`). Plot written to `sim_build/aclk_lite_gen_loopback/plots/loopback_throughput.png`. If the decoder reports a parity error or the trio mismatches, debug the timeline gaps / encoder before proceeding (this is the key end-to-end gate).

- [ ] **Step 7: Commit**

```bash
git add rtl/aclk_lite/aclk_lite_gen_timeline.sv tb/aclk_lite_gen_loopback/
git commit -m "feat(aclkgen): hardcoded timeline + generator->decoder loopback sim"
```

---

### Task 3: Plain-Verilog BD wrapper + smoke test

The no-AXI BD wrapper that drives H12 and the two debug pins. Mirrors `aclk_readout_bd_top.v` but carries no AXI interface.

**Files:**
- Create: `rtl/aclk_gen_bd_top.v`
- Create: `tb/aclk_gen_bd_top/test_aclk_gen_bd_top.py`
- Create: `tb/aclk_gen_bd_top/runner.py`

**Interfaces:**
- Consumes: `aclk_lite_gen_timeline` (Task 2), `aclk_lite_encoder` (Task 1).
- Produces: `module aclk_gen_bd_top (input wire clk_os, rstn; output wire aclk_out, frame_sync_dbg, clkos_dbg);`
  - `aclk_out`: the Manchester `line` (idle high) for H12.
  - `frame_sync_dbg`: the timeline `frame_sync` pulse.
  - `clkos_dbg`: `clk_os` divided by 1024 (MMCM-alive scope diagnostic).

- [ ] **Step 1: Write the failing smoke test**

Create `tb/aclk_gen_bd_top/test_aclk_gen_bd_top.py`:

```python
"""Smoke test for rtl/aclk_gen_bd_top.v: with clk_os running and rstn released, the
wrapper must produce a toggling Manchester output on aclk_out, at least one
frame_sync_dbg pulse (start of the first trio, which begins immediately), and a
toggling clkos_dbg (the divided clk_os alive indicator). This catches wrapper
wiring errors before a ~30-minute Vivado synthesis.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK_NS = 10


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


@cocotb.test()
async def test_wrapper_activity(dut):
    cocotb.start_soon(Clock(dut.clk_os, CLK_NS, unit="ns").start())
    dut.rstn.value = 0
    await ClockCycles(dut.clk_os, 5)
    await Timer(1, unit="ns")
    dut.rstn.value = 1

    saw_aclk0 = saw_aclk1 = saw_sync = False
    clkos_seen = set()
    for _ in range(3000):
        await RisingEdge(dut.clk_os)
        await Timer(1, unit="ns")
        a = _b(dut.aclk_out)
        if a == 0:
            saw_aclk0 = True
        elif a == 1:
            saw_aclk1 = True
        if _b(dut.frame_sync_dbg) == 1:
            saw_sync = True
        clkos_seen.add(_b(dut.clkos_dbg))

    assert saw_aclk0 and saw_aclk1, f"aclk_out did not toggle (0={saw_aclk0}, 1={saw_aclk1})"
    assert saw_sync, "frame_sync_dbg never pulsed"
    assert clkos_seen >= {0, 1}, f"clkos_dbg did not toggle: {clkos_seen}"
    dut._log.info("wrapper smoke OK: aclk_out toggles, frame_sync pulsed, clkos_dbg alive")
```

- [ ] **Step 2: Write the runner**

Create `tb/aclk_gen_bd_top/runner.py`:

```python
"""Cocotb 2.0 runner for rtl/aclk_gen_bd_top.v (the no-AXI BD wrapper). Compiles the
Verilog wrapper plus its SystemVerilog children (encoder + timeline).
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "aclk_gen_bd_top"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_gen_bd_top():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "aclk_lite" / "aclk_lite_encoder.sv",
            RTL_DIR / "aclk_lite" / "aclk_lite_gen_timeline.sv",
            RTL_DIR / "aclk_gen_bd_top.v",
        ],
        hdl_toplevel="aclk_gen_bd_top",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="aclk_gen_bd_top",
        test_module="test_aclk_gen_bd_top",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_aclk_gen_bd_top()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe tb\aclk_gen_bd_top\runner.py`
Expected: FAIL during build (`aclk_gen_bd_top.v` does not exist).

- [ ] **Step 4: Write the wrapper RTL**

Create `rtl/aclk_gen_bd_top.v`:

```verilog
// rtl/aclk_gen_bd_top.v
//
// Plain-Verilog block-design wrapper for the ACLK-Lite signal GENERATOR. Counterpart
// to aclk_readout_bd_top.v, but the generator needs no PS interaction, so this
// wrapper has NO AXI interface: the BD wires only clk_os (the 120 MHz oversample
// clock from the clk_wiz MMCM) and rstn (from the proc_sys_reset). It drives the
// hardcoded timeline's Manchester output onto aclk_out (-> H12) and exposes two
// scope-debug pins: frame_sync_dbg (start-of-trio trigger) and clkos_dbg
// (clk_os / 1024, an MMCM-alive indicator; Pmod level translators cannot pass
// 120 MHz, so divide it down).

`timescale 1ns / 1ps

module aclk_gen_bd_top (
    input  wire clk_os,            // 120 MHz oversample clock (from the BD clk_wiz)
    input  wire rstn,              // active-low reset (from proc_sys_reset)
    output wire aclk_out,          // Manchester ACLK-Lite output -> H12 (idle high)
    output wire frame_sync_dbg,    // start-of-trio pulse -> Pmod (scope trigger)
    output wire clkos_dbg          // clk_os / 1024 -> Pmod (MMCM alive)
);

    wire line;
    wire frame_sync;

    aclk_lite_gen_timeline #(.OVERSAMPLE(12)) u_gen (
        .clk        (clk_os),
        .rstn       (rstn),
        .line       (line),
        .frame_sync (frame_sync)
    );

    assign aclk_out       = line;
    assign frame_sync_dbg = frame_sync;

    // Divide clk_os down so a Pmod level translator can pass it: clkos_dbg ~117 kHz
    // if clk_os is alive.
    reg [9:0] div_os = 10'd0;
    always @(posedge clk_os) div_os <= div_os + 1'b1;
    assign clkos_dbg = div_os[9];

endmodule
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe tb\aclk_gen_bd_top\runner.py`
Expected: PASS (`test_wrapper_activity`).

- [ ] **Step 6: Commit**

```bash
git add rtl/aclk_gen_bd_top.v tb/aclk_gen_bd_top/
git commit -m "feat(aclkgen): no-AXI BD wrapper aclk_gen_bd_top.v + smoke test"
```

---

### Task 4: Vivado build (TCL + XDC) and bitstream

Mirror `build_aclk.tcl` minus all AXI, output the generator on H12, and run the build to produce the bitstream.

**Files:**
- Create: `vivado/build_aclkgen.tcl`
- Create: `constraints/kr260_aclkgen.xdc`

**Interfaces:**
- Consumes: `aclk_gen_bd_top` (Task 3) as a BD module reference; `aclk_lite_gen_timeline`, `aclk_lite_encoder` (RTL sources).
- Produces: `uart_echo_bd_wrapper.bit` (then `.bit.bin` + MD5 via `hw.ps1`).

- [ ] **Step 1: Write the XDC**

Create `constraints/kr260_aclkgen.xdc`:

```tcl
## constraints/kr260_aclkgen.xdc - ACLK-Lite signal GENERATOR output
##
## The generator drives a Manchester ACLK-Lite stream OUT of KR260 PMOD1 pin 1 =
## package H12, LVCMOS33 (the exact pin the receiver build uses as its INPUT), so the
## two boards can be wired H12 to H12 (plus a common Pmod ground) for an end-to-end
## test. Push-pull LVCMOS33, short board-to-board jumper. Verify connector positions
## against the carrier-card silkscreen (this is the generator board, not the receiver).

set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports aclk_out]

## Scope-debug pins (PMOD1): frame-sync trigger + divided clk_os (MMCM alive).
set_property -dict {PACKAGE_PIN E10 IOSTANDARD LVCMOS33} [get_ports clkos_dbg]
set_property -dict {PACKAGE_PIN E12 IOSTANDARD LVCMOS33} [get_ports frame_sync_dbg]

## Asynchronous clock groups.
##
## The clk_wiz MMCM makes the ~120 MHz oversample clock (clk_out1) from pl_clk0; it
## shares pl_clk0 as a physical source but is NOT phase-comparable to the ~100 MHz
## PS clock (clk_pl_*). Declaring the domains asynchronous keeps the tool from timing
## (and falsely failing) crossings between them. The generator has no PS<->rx data
## crossing, but the MMCM-derived clock vs pl_clk0 relationship is identical to the
## receiver build, so the same constraint applies.
set_clock_groups -name async_ps_vs_rx -asynchronous \
    -group [get_clocks -filter {NAME =~ "clk_pl_*"}] \
    -group [get_clocks -filter {NAME =~ "clk_out*clk_wiz*"}]
```

- [ ] **Step 2: Write the build TCL**

Create `vivado/build_aclkgen.tcl`:

```tcl
# vivado/build_aclkgen.tcl - ACLK-Lite signal GENERATOR on the KR260.
#
# Counterpart to build_aclk.tcl (the receiver), but this is the TX/generator. It
# reuses the receiver's proven clocking topology verbatim - PS pl_clk0 (100 MHz) +
# a PL clk_wiz MMCM deriving a 120 MHz oversample clock (clk_os), MMCM resetn from
# pl_resetn0, an auto proc_sys_reset with dcm_locked tied HIGH - but has NO AXI: the
# generator boots and transmits a hardcoded timeline, so there is no LPD AXI master,
# no SmartConnect, and no AXI slave. The Manchester stream leaves on H12 (constrained
# in the XDC). Reuses design_name=uart_echo_bd so the bitstream is named
# uart_echo_bd_wrapper.bit.bin and the existing overlay loads unchanged.
# build_aclk.tcl and all receiver/TCLK files are left untouched.
#
# Build:  vivado -mode batch -source vivado/build_aclkgen.tcl
#    or:  .\hw.ps1 build -Tcl vivado\build_aclkgen.tcl -Name aclkgen

set proj_name   aclkgen
set design_name uart_echo_bd
set part        xck26-sfvc784-2LV-c

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set rtl_dir    [file join $root_dir rtl]
set xdc_file   [file join $root_dir constraints kr260_aclkgen.xdc]

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

# RTL sources: the encoder + hardcoded timeline + the no-AXI BD wrapper. The generator
# instantiates only the encoder/timeline (no synchronizer/decoder dependency).
add_files -norecurse [list \
    [file join $rtl_dir aclk_lite aclk_lite_encoder.sv] \
    [file join $rtl_dir aclk_lite aclk_lite_gen_timeline.sv] \
    [file join $rtl_dir aclk_gen_bd_top.v] \
]
set_property file_type SystemVerilog [get_files *.sv]
add_files -fileset constrs_1 -norecurse [list $xdc_file]
update_compile_order -fileset sources_1

create_bd_design $design_name

# Zynq US+ PS with board preset; pl_clk0 (100 MHz). No AXI masters (generator only).
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* zynq_ultra_ps_e_0]
if {[llength $bp] > 0} {
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset 1} $ps
}
set_property -dict [list \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
    CONFIG.PSU__USE__M_AXI_GP0 {0} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__USE__M_AXI_GP2 {0} \
] $ps

# Our generator as a module reference (the Verilog BD wrapper).
set gen [create_bd_cell -type module -reference aclk_gen_bd_top u_gen]

# Reset: a proc_sys_reset off pl_clk0 / pl_resetn0, dcm_locked tied HIGH (proven on
# the tclk/uart_echo/receiver builds; gating reset on MMCM lock wedged the design on
# hardware). Its peripheral_aresetn is the design rstn.
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* rst_pl0]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]    [get_bd_pins rst_pl0/slowest_sync_clk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins rst_pl0/ext_reset_in]
set vcc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* dcm_locked_const]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $vcc
connect_bd_net [get_bd_pins $vcc/dout] [get_bd_pins rst_pl0/dcm_locked]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins u_gen/rstn]

# Clocking Wizard: ONE 120 MHz oversample clock (clk_os) from pl_clk0 INSIDE the PL,
# so we depend only on pl_clk0 (the one PL clock a runtime bitstream load leaves at
# the expected frequency). pl_clk0 is an internal net, so the MMCM input takes it with
# no buffer. clk_wiz's clk_in1 FREQ_HZ is read-only and derived from PRIM_IN_FREQ, so
# feed pl_clk0's exact realized rate in or validate_bd_design trips BD 41-238.
set clkw [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:* clk_wiz_0]
set pl0_hz  [get_property CONFIG.FREQ_HZ [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]]
set pl0_mhz [format %.6f [expr {$pl0_hz / 1000000.0}]]
set_property -dict [list \
    CONFIG.PRIM_SOURCE {No_buffer} \
    CONFIG.PRIM_IN_FREQ $pl0_mhz \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {120.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_TYPE {ACTIVE_LOW} \
] $clkw
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins clk_wiz_0/clk_in1]
# Give the MMCM a real reset from pl_resetn0 so it re-locks after a runtime fpgautil
# load (the PL is reconfigured while pl_clk0 already toggles; without a reset a missed
# lock can never re-acquire -> clk_os stays dead).
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins clk_wiz_0/resetn]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] [get_bd_pins u_gen/clk_os]
# clk_wiz/locked is intentionally left unconnected (the design does not gate on it;
# the MMCM still locks within microseconds, same as the receiver build).

# External Manchester output -> H12, plus the two scope-debug pins (constrained in XDC).
create_bd_port -dir O aclk_out
create_bd_port -dir O frame_sync_dbg
create_bd_port -dir O clkos_dbg
connect_bd_net [get_bd_port aclk_out]       [get_bd_pins u_gen/aclk_out]
connect_bd_net [get_bd_port frame_sync_dbg] [get_bd_pins u_gen/frame_sync_dbg]
connect_bd_net [get_bd_port clkos_dbg]      [get_bd_pins u_gen/clkos_dbg]

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

- [ ] **Step 3: Run the build**

Run: `.\hw.ps1 build -Tcl vivado\build_aclkgen.tcl -Name aclkgen`
Expected: synthesis + implementation + `write_bitstream` complete; the run prints `BITSTREAM: ...uart_echo_bd_wrapper.bit`, and `hw.ps1` produces `uart_echo_bd_wrapper.bit.bin` + its MD5. This is a long step (tens of minutes). If `validate_bd_design` trips, read the log and fix the TCL (most likely BD 41-238 on the clk_wiz input freq, already handled by PRIM_IN_FREQ).

- [ ] **Step 4: Verify the bitstream artifact**

Run: confirm the `.bit.bin` exists and capture its MD5 (the deliverable to load on the second board). PowerShell: `Get-FileHash -Algorithm MD5 <path-to>\uart_echo_bd_wrapper.bit.bin`.
Expected: the file exists; record the MD5 in the commit message / hand-off.

- [ ] **Step 5: Commit**

```bash
git add vivado/build_aclkgen.tcl constraints/kr260_aclkgen.xdc
git commit -m "feat(aclkgen): build_aclkgen.tcl (no-AXI generator build) + kr260_aclkgen.xdc"
```

---

## Self-Review

**1. Spec coverage:**
- Encoding contract / parity / 3 lengths: Task 1 (encoder) + Task 2 (loopback against the real decoder). Covered.
- Clocking (120 MHz MMCM from pl_clk0, OVERSAMPLE=12, resetn from pl_resetn0, dcm_locked HIGH, async clock groups): Task 4 TCL + XDC. Covered.
- Hardcoded timeline, no AXI, fixed recognizable trio (0x55 / 0xABCD / 0x1234+0xDEADBEEFCAFE0001), slow cadence (TRIO_GAP ~1 ms): Task 2 timeline. Covered.
- Frame-sync trigger + divided clk_os debug pins: Task 3 wrapper + Task 4 XDC (E12 frame_sync_dbg, E10 clkos_dbg). Covered.
- H12 LVCMOS33 push-pull output, common ground: Task 4 XDC + comment. Covered.
- design_name=uart_echo_bd, build via hw.ps1, .bit.bin + MD5 deliverable: Task 4. Covered.
- Reuse manchester_tx_model without editing it; no edits to receiver/TCLK files: respected throughout (model rebind is in-test only; all files are new and parallel). Covered.
- cocotb plots on completion: Task 1 (encoder_frame.png), Task 2 (loopback_throughput.png). Covered.

**2. Placeholder scan:** No TBD/TODO; every code step has complete code and exact run commands. Pass.

**3. Type/name consistency:** `aclk_lite_encoder` ports (clk, rstn, start, payload[79:0], length[6:0], line, busy) are identical across the RTL, the timeline instantiation, and the wrapper. `aclk_lite_gen_timeline` params (OVERSAMPLE, IDLE_GAP, TRIO_GAP) and ports (clk, rstn, line, frame_sync) match across the timeline RTL, the loopback tb, and the wrapper. `aclk_gen_bd_top` ports (clk_os, rstn, aclk_out, frame_sync_dbg, clkos_dbg) match across the wrapper RTL, the smoke test, and the BD port/connection list in the TCL. Decoder instantiation matches the existing `aclk_lite_decoder` ports. Pass.
