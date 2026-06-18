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
