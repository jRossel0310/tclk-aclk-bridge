"""Cocotb tests for rtl/edge_detector.sv — a rising-edge detector.

The output is registered, so a 0->1 transition on `signal_in` produces a single
one-cycle pulse on `edge_detect_pulse` on the *next* clock edge. Falling edges
and steady levels produce no pulse.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

CLK_PERIOD_NS = 10


def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())


async def settle(dut):
    await Timer(1, unit="ns")


async def step(dut):
    await RisingEdge(dut.clk)
    await settle(dut)


@cocotb.test()
async def test_edge_detector_single_pulse(dut):
    """A rising edge yields exactly one one-cycle pulse, one cycle later."""
    start_clock(dut)

    # Hold low; flush the initial state. No pulse while steady low.
    dut.signal_in.value = 0
    await step(dut)
    await step(dut)
    assert int(dut.edge_detect_pulse.value) == 0, "no pulse expected while steady low"

    # Drive the rising edge. The pulse appears on the *next* edge.
    dut.signal_in.value = 1
    await step(dut)
    assert int(dut.edge_detect_pulse.value) == 1, "expected a pulse one cycle after the rising edge"

    # Input stays high: the pulse must fall after exactly one cycle.
    await step(dut)
    assert int(dut.edge_detect_pulse.value) == 0, "pulse should last exactly one cycle"
    dut._log.info("rising edge OK: single one-cycle pulse")


@cocotb.test()
async def test_edge_detector_no_pulse_on_falling(dut):
    """Falling edges and steady-high produce no pulse."""
    start_clock(dut)

    dut.signal_in.value = 1
    await step(dut)          # rising edge -> pulse next cycle
    await step(dut)          # pulse cycle
    await step(dut)          # steady high now
    assert int(dut.edge_detect_pulse.value) == 0, "no pulse expected while steady high"

    dut.signal_in.value = 0  # falling edge
    await step(dut)
    assert int(dut.edge_detect_pulse.value) == 0, "no pulse expected on a falling edge"
    await step(dut)
    assert int(dut.edge_detect_pulse.value) == 0, "no pulse expected while steady low"
    dut._log.info("falling/steady OK: no spurious pulses")


@cocotb.test()
async def test_edge_detector_repeated(dut):
    """Each separate rising edge produces its own pulse."""
    start_clock(dut)
    dut.signal_in.value = 0
    await step(dut)

    pulses = 0
    for _ in range(3):
        dut.signal_in.value = 1
        await step(dut)
        pulses += int(dut.edge_detect_pulse.value) & 1
        dut.signal_in.value = 0
        await step(dut)
    assert pulses == 3, f"expected 3 pulses for 3 presses, got {pulses}"
    dut._log.info("repeated edges OK: one pulse per press")
