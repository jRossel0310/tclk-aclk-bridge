"""Cocotb tests for rtl/synchronizer.sv — an N-stage CDC synchronizer.

The synchronizer has no reset; it simply resamples `async_signal` into the
`clk` domain through STAGES back-to-back flops, so a new input value appears on
`sync_signal` after exactly STAGES rising edges.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK_PERIOD_NS = 10
STAGES = 2          # must match the synchronizer's STAGES parameter (default 2)


def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())


async def settle(dut):
    """Step past the <= update region so reads see post-edge values."""
    await Timer(1, unit="ns")


async def flush(dut, value):
    """Drive `value` long enough for it to fully propagate through the chain."""
    dut.async_signal.value = value
    await ClockCycles(dut.clk, STAGES + 1)
    await settle(dut)


@cocotb.test()
async def test_synchronizer_latency(dut):
    """A 0->1 change appears on the output after exactly STAGES clock edges."""
    start_clock(dut)
    await flush(dut, 0)
    assert int(dut.sync_signal.value) == 0, "output should track a steady 0"

    dut.async_signal.value = 1
    for edge in range(1, STAGES + 1):
        await RisingEdge(dut.clk)
        await settle(dut)
        if edge < STAGES:
            assert int(dut.sync_signal.value) == 0, \
                f"propagated too early: output high after only {edge}/{STAGES} edges"
    assert int(dut.sync_signal.value) == 1, \
        f"input should reach the output after {STAGES} edges"
    dut._log.info(f"latency OK: 0->1 propagated in exactly {STAGES} edges")


@cocotb.test()
async def test_synchronizer_tracks_both_levels(dut):
    """After settling, the output follows the input for both 0 and 1."""
    start_clock(dut)
    for value in (1, 0, 1, 0):
        await flush(dut, value)
        assert int(dut.sync_signal.value) == value, \
            f"output should track steady input {value}, got {int(dut.sync_signal.value)}"
    dut._log.info("level tracking OK: output follows steady input")
