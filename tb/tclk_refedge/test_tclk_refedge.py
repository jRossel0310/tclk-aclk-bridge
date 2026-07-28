"""Cocotb tests for the REF_EDGE frame-detection strobe added to
rtl/aclk_bridge/TCLK_DESERIALIZER2.v and threaded through TCLK_RCV.

REF_EDGE is a pure tap on the existing frame-accept condition (data_reg[10:8]
== 110 & parity match) -- it must pulse exactly once per accepted good frame,
on the exact same CLK_40M cycle DAVn goes low (both are registered off the
same DAVn_int/SCLK_posedge combinational signals on the same clock edge), and
must NOT pulse for a bad-parity frame (which sets PERR, not DAVn_int).

Reuses the same DUT stack and warm-up dance as tb/tclk_rcv/test_tclk_rcv.py.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from tclk_tx_model import stream_samples, drive_samples, SAMPLES_PER_CELL
from cocotb_helpers import _b

CLK80_PERIOD_PS = 100_000 // SAMPLES_PER_CELL
CLK40_PERIOD_PS = 200_000 // SAMPLES_PER_CELL
WARMUP_CELLS = 40


async def reset_dut(dut):
    dut.TCLK.value = 1
    dut.TCLK_RATE.value = 1
    dut.PERR_CLR.value = 0
    dut.SIG_ERR_CLR.value = 0
    dut.RESETn.value = 0
    await ClockCycles(dut.CLK_80M, 10)
    await Timer(1, unit="ns")
    dut.RESETn.value = 1
    await ClockCycles(dut.CLK_80M, 10)


async def monitor(dut, captured, ref_edges, mismatches):
    """Every CLK_40M cycle: record DATA on DAVn-low, count REF_EDGE pulses, and
    flag any cycle where REF_EDGE and ~DAVn disagree (the RTL ties them to the
    same DAVn_int & SCLK_posedge source, so they must coincide exactly)."""
    while True:
        await RisingEdge(dut.CLK_40M)
        await Timer(1, unit="ns")
        davn_lo = _b(dut.DAVn) == 0
        ref = _b(dut.REF_EDGE) == 1
        if davn_lo:
            captured.append(_b(dut.DATA))
        if ref:
            ref_edges.append(_b(dut.DATA))
        if davn_lo != ref:
            mismatches.append((davn_lo, ref))


def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.CLK_80M, CLK80_PERIOD_PS, unit="ps").start())
    cocotb.start_soon(Clock(dut.CLK_40M, CLK40_PERIOD_PS, unit="ps").start())


async def _warmup_then_monitor(dut, events, captured, ref_edges, mismatches):
    samples = stream_samples(events, warmup_cells=WARMUP_CELLS)
    warm_n = WARMUP_CELLS * SAMPLES_PER_CELL

    dut.PERR_CLR.value = 1
    await drive_samples(dut.CLK_80M, dut.TCLK, samples[:warm_n])
    dut.PERR_CLR.value = 0
    await ClockCycles(dut.CLK_40M, 2)

    cocotb.start_soon(monitor(dut, captured, ref_edges, mismatches))
    await drive_samples(dut.CLK_80M, dut.TCLK, samples[warm_n:])
    await ClockCycles(dut.CLK_40M, 30)


@cocotb.test()
async def test_ref_edge_once_per_good_frame(dut):
    """REF_EDGE pulses exactly once per accepted good frame, coincident with
    the ~DAVn frame-accept cycle, over a run of N good frames plus one
    bad-parity frame (which must not pulse REF_EDGE at all)."""
    _start_clocks(dut)
    await reset_dut(dut)

    good_events = [0x9D, 0xD2, 0x00, 0x07, 0x0F, 0xA5, 0x29]
    events = good_events[:3] + [(0x55, True)] + good_events[3:]
    captured, ref_edges, mismatches = [], [], []
    await _warmup_then_monitor(dut, events, captured, ref_edges, mismatches)

    assert not mismatches, (
        f"REF_EDGE did not coincide with ~DAVn on {len(mismatches)} cycle(s): "
        f"{mismatches}"
    )
    assert len(ref_edges) == len(good_events), (
        f"expected {len(good_events)} REF_EDGE pulses (one per good frame), "
        f"got {len(ref_edges)}: {[f'0x{x:02X}' for x in ref_edges]}"
    )
    assert ref_edges == good_events, (
        f"REF_EDGE data mismatch: {[f'0x{x:02X}' for x in ref_edges]} != "
        f"{[f'0x{x:02X}' for x in good_events]}"
    )
    assert 0x55 not in ref_edges, "bad-parity frame pulsed REF_EDGE"
    dut._log.info(
        f"REF_EDGE OK: {len(ref_edges)} pulses, each coincident with ~DAVn, "
        f"bad-parity frame silent"
    )
