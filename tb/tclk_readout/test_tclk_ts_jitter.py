"""Characterization: quantify the DAVn-latched timestamp dither and confirm it is
a BOUNDED recovered-SCLK-to-clk_40m resync quantization, not variable byte-assembly
latency.

Byte assembly in TCLK_DESERIALIZER2 is fixed-length (start + 8 data + parity, one
transition per cell either way), so it contributes no variable latency by
construction; the only place dither can come from is the CDC resync of the
recovered line activity into clk_40m. To make that resync beat actually visible in
sim, THIS TEST DELIBERATELY RUNS ITS OWN clk_40m (self-contained _start_clocks
below) instead of the sibling suite's clk_40m, pinned NEAR the board's 200 MHz
timestamp rate (4998 ps, vs. the board's exact 5000 ps) but NOT commensurate with
clk_80m. If clk_80m and clk_40m are exact rational multiples of each other (e.g.
the 12500 ps / 5000 ps = 5:2 ratio used verbatim for the 200 MHz board build),
every driven event period in this testbench lands on the exact same clk_40m phase
every time -- by clock-ratio arithmetic alone, independent of anything the DUT
does -- and the measured spread is trivially 0 regardless of whether the DUT has
any real dither. Nudging clk_40m 2 ps off the exact board rate (cocotb's Clock
requires an even period in ps) breaks that artifact and lets the real resync beat
show up, while staying close enough to 200 MHz to stand in for the board's
timestamp clock.

Sim limitation: in real hardware the recovered SCLK is asynchronous to clk_40m
(an independent oscillator recovered from the external TCLK line), and captures
show ~250 ns of dither. In this testbench the driven line is generated from
clk_80m, itself a plain synchronous cocotb clock, so the full asynchronous
magnitude is not reproducible here -- a non-commensurate clk_40m is only a proxy
that exercises the resync/CDC path and shows it is BOUNDED (~1 clk_40m tick), not
that it reproduces the real-line magnitude.
"""
import statistics
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from tclk_tx_model import biphase_samples, event_bits, drive_samples
from axi_lite_bfm import axi_read

# reuse the register map + reset/read helpers from the sibling test, but NOT its
# _start_clocks / CLK40_PERIOD_PS -- this test owns its own clocks (see docstring).
from test_tclk_readout import STATUS, reset_dut, axi_read_event, _start_quadrature

N_EVENTS = 30
GAP_CELLS = 12

# clk_80m stays at the OSR=8 rate the driver/DUT assume. clk_40m is pinned NEAR the
# board's 200 MHz timestamp rate (5000 ps) but nudged 2 ps off it to 4998 ps -- close
# enough to be the board rate within measurement noise, but NOT an exact rational
# multiple of 12500 ps, so the 12500/4998 phase walks and the recovered-SCLK-to-
# clk_40m phase is free to differ event to event instead of being locked by clock-
# ratio arithmetic (exactly 5000 ps is commensurate with 12500 ps at a 2:5 ratio and
# gives a degenerate spread=0 -- see the module docstring). cocotb's Clock() requires
# an even period in ps, so 4999 is not usable; 4998 is the nearest even, non-
# commensurate stand-in for the board rate.
CLK80_PERIOD_PS = 12500          # 80 MHz, OSR=8
STAMP_CLK40_PS = 4998            # ~200 MHz board rate, nudged off exact commensurability
AXI_PERIOD_NS = 14


def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.clk_80m, CLK80_PERIOD_PS, unit="ps").start())
    cocotb.start_soon(Clock(dut.clk_40m, STAMP_CLK40_PS, unit="ps").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, AXI_PERIOD_NS, unit="ns").start())
    # clk_p90/p180/p270 just need to keep the fine-TDC's synchronizers out of X
    # for this test (it characterizes DAVn-latched dither, not the fine-TDC);
    # the 1 ps quadrature-phase truncation from STAMP_CLK40_PS not being a
    # multiple of 4 is immaterial here.
    cocotb.start_soon(_start_quadrature(dut, STAMP_CLK40_PS))


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
    tick_ns = STAMP_CLK40_PS / 1000.0
    spread_ticks = max(intervals) - min(intervals)
    dut._log.info(
        f"period ticks median={statistics.median(intervals)}, "
        f"spread={spread_ticks} ticks (~{spread_ticks*tick_ns:.1f} ns), "
        f"stdev={statistics.pstdev(intervals):.2f} ticks "
        f"(non-commensurate stamp clock: clk_80m={CLK80_PERIOD_PS}ps, clk_40m={STAMP_CLK40_PS}ps)"
    )
    # Resync hypothesis: with a non-commensurate stamp clock the CDC resync beat is
    # actually free to vary event to event; it should still be bounded to about one
    # clk_40m tick of quantization, NOT hundreds of ns of byte-assembly variation.
    assert spread_ticks <= 3, (
        f"interval spread {spread_ticks} ticks exceeds the resync bound; "
        f"the dither is not a simple resync beat - revisit the increment-C premise"
    )
