"""Cocotb tests for rtl/button_parser.sv.

button_parser = synchronizer (2 stages) -> debouncer -> edge_detector, so a
clean, sustained press produces exactly one one-cycle pulse on `out`.

SAMPLE_CNT_MAX/PULSE_CNT_MAX below MUST match the `parameters` in runner.py.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK_PERIOD_NS = 10

# Keep in sync with runner.py's `parameters`.
SAMPLE_CNT_MAX = 4
PULSE_CNT_MAX = 4

# Generous window covering sync (2) + debounce + edge-detect (1) + slack.
SETTLE_CYCLES = (PULSE_CNT_MAX + 4) * SAMPLE_CNT_MAX + 8


def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())


async def settle(dut):
    await Timer(1, unit="ns")


async def count_pulses(dut, cycles):
    """Count cycles where `out` is asserted over the next `cycles` clocks.

    The edge detector emits one-cycle pulses, so this equals the number of
    detected presses.
    """
    pulses = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        await settle(dut)
        if int(dut.out.value) != 0:
            pulses += 1
    return pulses


@cocotb.test()
async def test_button_parser_single_press(dut):
    """A sustained press yields exactly one pulse."""
    start_clock(dut)
    btn = getattr(dut, "in")          # "in" is a Python keyword
    btn.value = 0
    await ClockCycles(dut.clk, SAMPLE_CNT_MAX)

    btn.value = 1
    pulses = await count_pulses(dut, SETTLE_CYCLES)
    assert pulses == 1, f"a single press should yield exactly one pulse, got {pulses}"
    dut._log.info("single press OK: exactly one pulse")


@cocotb.test()
async def test_button_parser_press_release_press(dut):
    """Two distinct presses yield two separate pulses; releasing does not."""
    start_clock(dut)
    btn = getattr(dut, "in")          # "in" is a Python keyword

    # Start from a known released state (tests share one simulation, so a
    # previous test may have left the button pressed).
    btn.value = 0
    await count_pulses(dut, SETTLE_CYCLES)

    # First press.
    btn.value = 1
    assert (await count_pulses(dut, SETTLE_CYCLES)) == 1, "first press should pulse once"

    # Release: no rising edge, so no pulse.
    btn.value = 0
    assert (await count_pulses(dut, SETTLE_CYCLES)) == 0, "release should not pulse"

    # Second press.
    btn.value = 1
    assert (await count_pulses(dut, SETTLE_CYCLES)) == 1, "second press should pulse once"
    dut._log.info("press/release/press OK: one pulse per press")
