"""Cocotb tests for rtl/async_fifo.sv, the dual-clock (asynchronous) FIFO that the
ACLK readout uses to cross from the recovered RX clock domain (where ACLK_RCV
runs) into the PS-facing AXI clock domain (pl_clk0).

The write and read clocks run at different, unrelated rates here on purpose, so
the Gray-coded pointer crossing is genuinely exercised. The tests prove:

  1. integrity under backpressure: every word written comes out exactly once, in
     order, with zero loss, even when the reader stalls and the FIFO fills.
  2. overflow alarm: if the writer ignores `full` and overruns the FIFO, the
     sticky `overflow` flag latches and only the first DEPTH words survive.

When a run completes, an occupancy-vs-time plot is written under
sim_build/async_fifo/plots/ (matplotlib, skipped with a warning if unavailable).
"""

import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

# RTL defaults (rtl/async_fifo.sv): WIDTH=96, ADDR_WIDTH=6 -> DEPTH=64.
WIDTH = 96
DEPTH = 64
MASK  = (1 << WIDTH) - 1

WR_PERIOD_NS = 16     # about 62.5 MHz, stands in for the recovered RX clock
RD_PERIOD_NS = 10     # 100 MHz, stands in for pl_clk0


def _distinct(i: int) -> int:
    """A distinct WIDTH-bit pattern per index, so an order check is meaningful."""
    return (((i & 0xFFFF) << 64) | (0xC0FFEE00 + i)) & MASK


def _b(sig) -> int:
    """Read a 1-bit signal as 0 or 1; unresolved (x/z) returns -1 so callers can
    treat warm-up cycles as 'not ready' instead of crashing on int(x)."""
    try:
        return int(sig.value)
    except Exception:
        return -1


# ---------------------------------------------------------------------------
# Reset and driver coroutines
# ---------------------------------------------------------------------------

async def _reset(dut):
    """Assert both active-low resets, then release and let the pointer
    synchronizers (which have no reset of their own) fill so empty/full settle."""
    dut.wr_en.value   = 0
    dut.rd_en.value   = 0
    dut.wr_data.value = 0
    dut.wr_rstn.value = 0
    dut.rd_rstn.value = 0
    await ClockCycles(dut.wr_clk, 5)
    await ClockCycles(dut.rd_clk, 5)
    await Timer(1, unit="ns")
    dut.wr_rstn.value = 1
    dut.rd_rstn.value = 1
    await ClockCycles(dut.rd_clk, 4)
    await ClockCycles(dut.wr_clk, 4)


async def _writer(dut, values, acct, throttle=0):
    """Push every value, respecting `full` so nothing is ever dropped. The `full`
    value sampled just after a wr_clk edge is the one that gates the next edge's
    capture, so a write driven after seeing full==0 is guaranteed accepted."""
    dut.wr_en.value = 0
    for v in values:
        await RisingEdge(dut.wr_clk)
        await Timer(1, unit="ns")
        while _b(dut.full) == 1:
            dut.wr_en.value = 0
            await RisingEdge(dut.wr_clk)
            await Timer(1, unit="ns")
        dut.wr_data.value = v
        dut.wr_en.value = 1
        await RisingEdge(dut.wr_clk)        # captured here (full was 0)
        dut.wr_en.value = 0
        acct["w"] += 1
        await Timer(1, unit="ns")
        for _ in range(throttle):
            await RisingEdge(dut.wr_clk)
            await Timer(1, unit="ns")
    dut.wr_en.value = 0


async def _reader(dut, captured, n_expected, throttle=0):
    """Drain the FIFO. With FWFT the head is exposed on rd_data while empty is
    low, so capture it first, then pulse rd_en to advance to the next entry."""
    dut.rd_en.value = 0
    while len(captured) < n_expected:
        await RisingEdge(dut.rd_clk)
        await Timer(1, unit="ns")
        if _b(dut.empty) == 0:
            captured.append(int(dut.rd_data.value) & MASK)
            dut.rd_en.value = 1
            await RisingEdge(dut.rd_clk)    # pop happens here
            dut.rd_en.value = 0
            await Timer(1, unit="ns")
            for _ in range(throttle):
                await RisingEdge(dut.rd_clk)
                await Timer(1, unit="ns")
        else:
            dut.rd_en.value = 0


async def _sampler(dut, acct, captured, series, interval_ns=20):
    """Record (time, occupancy, cumulative writes, cumulative reads) on a fixed
    cadence so the completed run can be plotted. Time is tracked locally to avoid
    depending on a specific get_sim_time signature."""
    t = 0
    while not acct.get("done"):
        await Timer(interval_ns, unit="ns")
        t += interval_ns
        series.append((t, acct["w"] - len(captured), acct["w"], len(captured)))


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _save_occupancy_plot(series, name):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                # noqa: BLE001 (optional dependency)
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return None
    if not series:
        return None

    ts  = [s[0] for s in series]
    occ = [s[1] for s in series]
    wr  = [s[2] for s in series]
    rd  = [s[3] for s in series]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)

    ax1.plot(ts, occ, color="tab:blue", lw=1.6)
    ax1.axhline(DEPTH, color="tab:red", ls="--", lw=1, label=f"DEPTH = {DEPTH}")
    ax1.set_ylabel("FIFO occupancy (words)")
    ax1.set_title("async_fifo: clock-domain crossing under backpressure")
    ax1.set_ylim(bottom=0)
    ax1.legend(loc="upper right")
    ax1.grid(True, alpha=0.3)

    ax2.plot(ts, wr, color="tab:green",  lw=1.6, label="words written")
    ax2.plot(ts, rd, color="tab:orange", lw=1.6, label="words read")
    ax2.set_xlabel("sim time (ns)")
    ax2.set_ylabel("cumulative words")
    ax2.legend(loc="upper left")
    ax2.grid(True, alpha=0.3)

    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "async_fifo" / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_integrity_under_backpressure(dut):
    """Write 4x the FIFO depth through the clock crossing while the reader is
    throttled, so the FIFO fills, back-pressures the writer, then drains. Every
    word must come out exactly once, in order, with no overflow."""
    cocotb.start_soon(Clock(dut.wr_clk, WR_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.rd_clk, RD_PERIOD_NS, unit="ns").start())
    await _reset(dut)

    n = 4 * DEPTH
    values = [_distinct(i) for i in range(n)]
    captured, acct, series = [], {"w": 0}, []

    samp = cocotb.start_soon(_sampler(dut, acct, captured, series))
    wr   = cocotb.start_soon(_writer(dut, values, acct))
    await _reader(dut, captured, n, throttle=3)   # slow reader -> FIFO fills
    await wr
    acct["done"] = True
    samp.cancel()

    assert int(dut.overflow.value) == 0, "overflow latched though we respected full"
    assert len(captured) == n, f"lost words: got {len(captured)} of {n}"
    if captured != values:
        i = next(k for k, (a, b) in enumerate(zip(captured, values)) if a != b)
        raise AssertionError(
            f"order/data broken at index {i}: got 0x{captured[i]:024X}, "
            f"expected 0x{values[i]:024X}"
        )

    path = _save_occupancy_plot(series, "occupancy_backpressure.png")
    if path:
        dut._log.info(f"occupancy plot written to {path}")
    dut._log.info(f"integrity OK: {n} words crossed the clock domain in order, no loss")


@cocotb.test()
async def test_overflow_latches_and_keeps_first_words(dut):
    """With the reader idle, blast DEPTH + extra words while ignoring `full`. The
    FIFO must fill, `overflow` must latch, and only the first DEPTH words survive
    (the overrun tail is dropped)."""
    cocotb.start_soon(Clock(dut.wr_clk, WR_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.rd_clk, RD_PERIOD_NS, unit="ns").start())
    await _reset(dut)

    extra  = 16
    values = [_distinct(i) for i in range(DEPTH + extra)]

    dut.rd_en.value = 0
    for v in values:
        await RisingEdge(dut.wr_clk)
        dut.wr_data.value = v
        dut.wr_en.value = 1
    await RisingEdge(dut.wr_clk)
    dut.wr_en.value = 0
    await Timer(1, unit="ns")

    assert int(dut.full.value) == 1, "FIFO should be full after the overrun"
    assert int(dut.overflow.value) == 1, "overflow did not latch on overrun"

    captured = []
    await _reader(dut, captured, DEPTH, throttle=0)
    assert captured == values[:DEPTH], (
        f"expected the first {DEPTH} words to survive the overrun; "
        f"got {len(captured)} words that differ"
    )
    dut._log.info(f"overflow OK: latched, {DEPTH} kept, {extra} dropped by design")
