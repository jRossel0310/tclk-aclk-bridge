"""Cocotb 2.0 smoke test for rtl/counter.sv.

Goal: prove the toolchain works (compile -> simulate -> cocotb -> waveform),
not to exhaustively verify the counter. Written in a reusable style:
a clock-start helper and a reset helper, plus explicit assertions, so real
tests can follow the same shape.

NOTE: cocotb 2.0 API (differs from 1.x):
  - the runner lives in `cocotb_tools.runner` (was `cocotb.runner`)
  - Timer/Clock take `unit=` (singular), not `units=`
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK_PERIOD_NS = 10


def start_clock(dut):
    """Start a free-running clock on dut.clk. Reusable across tests."""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    return clock


async def reset_dut(dut, cycles: int = 2):
    """Drive the synchronous active-high reset for `cycles` clock edges,
    leaving the DUT idle (en=0) and out of reset on return."""
    dut.en.value = 0
    dut.rst.value = 1
    await ClockCycles(dut.clk, cycles)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


async def settle(dut):
    """Let nonblocking (<=) updates apply before sampling outputs.

    A cocotb edge callback wakes us in the same timestep *before* the RTL's
    `<=` updates land, so reading right after an edge sees the pre-edge value.
    A tiny Timer delay steps past the update region for a clean sample.
    """
    await Timer(1, unit="ns")


@cocotb.test()
async def test_counter_increments(dut):
    """After reset the counter is 0, then increments once per enabled cycle."""
    start_clock(dut)

    # --- reset: expect a clean zero --------------------------------------
    await reset_dut(dut)
    await settle(dut)
    assert int(dut.count.value) == 0, \
        f"after reset, expected count=0, got {int(dut.count.value)}"
    dut._log.info("reset OK: count == 0")

    # --- count N enabled cycles: expect exactly N ------------------------
    n = 5
    dut.en.value = 1
    await ClockCycles(dut.clk, n)
    await settle(dut)

    got = int(dut.count.value)
    assert got == n, f"expected count={n} after {n} enabled cycles, got {got}"
    dut._log.info(f"count OK: reached {got} after {n} enabled cycles")

    # --- enable low holds the value -------------------------------------
    dut.en.value = 0
    await ClockCycles(dut.clk, 3)
    await settle(dut)

    held = int(dut.count.value)
    assert held == n, f"with en=0 expected count to hold at {n}, got {held}"
    dut._log.info(f"hold OK: count stayed at {held} with en=0")
