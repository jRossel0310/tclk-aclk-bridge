"""Cocotb tests for rtl/debouncer.sv.

The debounce window is tiny here so the test runs in a few hundred ns. The
values below MUST match the `parameters` passed in runner.py:

    SAMPLE_CNT_MAX = 4   ->  one sample tick every 4 clk cycles
    PULSE_CNT_MAX  = 4   ->  4 consecutive high samples needed to assert

So an input held high asserts the output after ~SAMPLE_CNT_MAX * PULSE_CNT_MAX
cycles, and a single low sample immediately clears it.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer

CLK_PERIOD_NS = 10

# Keep in sync with runner.py's `parameters`.
SAMPLE_CNT_MAX = 4
PULSE_CNT_MAX = 4

# Cycles that comfortably cover the full debounce window plus alignment slack.
DEBOUNCE_CYCLES = (PULSE_CNT_MAX + 2) * SAMPLE_CNT_MAX


def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())


async def settle(dut):
    await Timer(1, unit="ns")


@cocotb.test()
async def test_debouncer_asserts_on_steady_high(dut):
    """A steadily-held input eventually drives the output high."""
    start_clock(dut)
    dut.glitchy_signal.value = 0
    await ClockCycles(dut.clk, SAMPLE_CNT_MAX)
    await settle(dut)
    assert int(dut.debounced_signal.value) == 0, "output should start low"

    dut.glitchy_signal.value = 1
    await ClockCycles(dut.clk, DEBOUNCE_CYCLES)
    await settle(dut)
    assert int(dut.debounced_signal.value) == 1, "steady-high input should debounce to 1"
    dut._log.info("steady high OK: output asserted after debounce window")


@cocotb.test()
async def test_debouncer_clears_on_release(dut):
    """After the input drops, the output returns low."""
    start_clock(dut)
    dut.glitchy_signal.value = 1
    await ClockCycles(dut.clk, DEBOUNCE_CYCLES)
    await settle(dut)
    assert int(dut.debounced_signal.value) == 1, "precondition: output should be high"

    dut.glitchy_signal.value = 0
    # A single low sample resets the saturating counter; allow up to two sample
    # ticks for that low to be observed.
    await ClockCycles(dut.clk, 2 * SAMPLE_CNT_MAX)
    await settle(dut)
    assert int(dut.debounced_signal.value) == 0, "released input should clear the output"
    dut._log.info("release OK: output cleared after input dropped")


@cocotb.test()
async def test_debouncer_rejects_glitch(dut):
    """A brief glitch shorter than the window never reaches the output."""
    start_clock(dut)
    dut.glitchy_signal.value = 0
    await ClockCycles(dut.clk, SAMPLE_CNT_MAX)
    await settle(dut)

    # High for only a couple of sample ticks (< PULSE_CNT_MAX), then released.
    dut.glitchy_signal.value = 1
    await ClockCycles(dut.clk, 2 * SAMPLE_CNT_MAX)
    dut.glitchy_signal.value = 0
    await ClockCycles(dut.clk, DEBOUNCE_CYCLES)
    await settle(dut)
    assert int(dut.debounced_signal.value) == 0, "a short glitch must not assert the output"
    dut._log.info("glitch OK: short pulse rejected")
