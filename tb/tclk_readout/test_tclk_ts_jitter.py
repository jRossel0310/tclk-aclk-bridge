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
