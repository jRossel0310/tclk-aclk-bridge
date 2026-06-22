"""Smoke test for rtl/aclk_gen_bd_top.v: with clk_os running and rstn released, the
wrapper must produce a toggling Manchester output on aclk_out, at least one
frame_sync_dbg pulse (start of the first trio), and a toggling clkos_dbg (the
divided clk_os alive indicator). This catches wrapper wiring errors before a
~30-minute Vivado synthesis.

Clock: CLK_NS=12.5 ns (80 MHz, matching the retuned MMCM CLKOUT1). The timeline
warms up for TRIO_GAP=80000 cycles before the first frame_sync, so POLL_CYCLES
must exceed that; 90000 comfortably covers it.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK_NS    = 12.5   # 80 MHz cell clock (matches build_aclkgen.tcl CLKOUT1)
POLL_CYCLES = 90000  # TRIO_GAP=80000 warm-up + margin for first frame_sync


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
    for _ in range(POLL_CYCLES):
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
