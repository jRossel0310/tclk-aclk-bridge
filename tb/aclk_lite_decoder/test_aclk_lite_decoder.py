"""Cocotb tests for rtl/aclk_lite/aclk_lite_decoder.sv, the Manchester ACLK-Lite
decoder (ADM). The matching Manchester encoder model (tb/manchester_tx_model.py)
drives the line input; the test checks the length-aware decode (8 / 16 / 80
payload bits), the is_tclk flag, the parity-error path, and idle behavior.

On completion a plot of the recovered Manchester line for the 80-bit example
frame is written under sim_build/aclk_lite_decoder/plots/.
"""

import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from manchester_tx_model import send_frame, OVERSAMPLE

CLK_NS = 10                 # 100 MHz oversampling clock
MASK64 = (1 << 64) - 1


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


async def reset_dut(dut):
    dut.line.value = 1                          # idle high
    dut.rstn.value = 0
    await ClockCycles(dut.clk, 5)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    await ClockCycles(dut.clk, 5)               # let the line synchronizer settle


async def monitor(dut, events, errors):
    """Capture (event_id, data, is_tclk) on each event, and parity errors."""
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        if _b(dut.event_valid) == 1:
            dv = _b(dut.data_valid)
            events.append((
                int(dut.event_id.value) & 0xFFFF,
                (int(dut.data.value) & MASK64) if dv == 1 else None,
                _b(dut.is_tclk),
            ))
        if _b(dut.parity_error) == 1:
            errors.append(True)


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
    ax.step(xs, levels, where="post", color="tab:purple", lw=1.4)
    ax.set_ylim(-0.2, 1.2)
    ax.set_yticks([0, 1])
    ax.set_xlabel("oversampling-clock sample")
    ax.set_ylabel("line")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)

    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "aclk_lite_decoder" / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


@cocotb.test()
async def test_length_aware_decode(dut):
    """8-bit TCLK, 16-bit ACLK, and 80-bit ACLK+DATA frames each decode to the
    right event_id (and data), with the right valid / is_tclk strobes."""
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    await reset_dut(dut)

    events, errors = [], []
    cocotb.start_soon(monitor(dut, events, errors))

    await send_frame(dut.clk, dut.line, 0x5A, 8)                 # TCLK event 0x5A
    await send_frame(dut.clk, dut.line, 0x1234, 16)              # ACLK event 0x1234
    payload80 = (0xABCD << 64) | 0x0123456789ABCDEF
    last_levels = await send_frame(dut.clk, dut.line, payload80, 80)
    await ClockCycles(dut.clk, OVERSAMPLE * 3)

    assert not errors, f"unexpected parity errors: {len(errors)}"
    assert len(events) == 3, f"expected 3 events, got {len(events)}: {events}"

    assert events[0] == (0x005A, None, 1), f"8-bit decode wrong: {events[0]}"
    assert events[1] == (0x1234, None, 0), f"16-bit decode wrong: {events[1]}"
    assert events[2] == (0xABCD, 0x0123456789ABCDEF, 0), f"80-bit decode wrong: {events[2]}"

    path = _save_line_plot(
        last_levels, "manchester_frame.png",
        "ACLK-Lite Manchester line: 80-bit frame (event 0xABCD + 64-bit data)",
    )
    if path:
        dut._log.info(f"line plot written to {path}")
    dut._log.info("length-aware decode OK: 8 / 16 / 80-bit frames decoded, is_tclk correct")


@cocotb.test()
async def test_parity_error(dut):
    """A corrupted payload bit must raise parity_error and produce no event."""
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    await reset_dut(dut)

    events, errors = [], []
    cocotb.start_soon(monitor(dut, events, errors))

    await send_frame(dut.clk, dut.line, 0x1234, 16, flip_bit=5)  # corrupt one payload bit
    await ClockCycles(dut.clk, OVERSAMPLE * 3)

    assert errors, "corrupted frame did not raise parity_error"
    assert not events, f"a corrupted frame leaked an event: {events}"
    dut._log.info(f"parity path OK: {len(errors)} parity_error, no event emitted")


@cocotb.test()
async def test_idle_no_false_events(dut):
    """A long idle period (steady high) must not produce any spurious events."""
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    await reset_dut(dut)

    events, errors = [], []
    cocotb.start_soon(monitor(dut, events, errors))

    await ClockCycles(dut.clk, OVERSAMPLE * 20)             # just idle
    assert not events and not errors, \
        f"idle produced spurious output: events={events}, errors={len(errors)}"
    dut._log.info("idle OK: no false events over a long idle period")
