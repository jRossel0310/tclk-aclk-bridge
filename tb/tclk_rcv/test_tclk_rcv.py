"""Cocotb tests for the inherited TCLK receiver (rtl/aclk_bridge/TCLK_RCV.v),
validated against the documented Fermilab TCLK biphase-mark format. The
tb/tclk_tx_model.py model drives the TCLK line; the test checks that known event
codes decode (DATA + DAVn) and that a bad-parity frame raises PERR.

CLK_80M = 80 MHz (8x oversample of the 10 MHz TCLK), CLK_40M = 40 MHz, TCLK_RATE
= 1 (10 MHz mode). The receiver emits one spurious PERR while serdec first locks
to the carrier, so the warm-up is driven with PERR_CLR held and monitoring only
begins once the link is settled (a real receiver clears PERR after init too).
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from tclk_tx_model import stream_samples, drive_samples, SAMPLES_PER_CELL

WARMUP_CELLS = 40


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


async def reset_dut(dut):
    dut.TCLK.value = 1                  # idle high
    dut.TCLK_RATE.value = 1             # 10 MHz mode
    dut.PERR_CLR.value = 0
    dut.SIG_ERR_CLR.value = 0
    dut.RESETn.value = 0
    await ClockCycles(dut.CLK_80M, 10)
    await Timer(1, unit="ns")
    dut.RESETn.value = 1
    await ClockCycles(dut.CLK_80M, 10)


async def monitor(dut, captured, perrs):
    """Capture DATA on each DAVn strobe (active low) and note PERR rising."""
    prev_perr = 0
    while True:
        await RisingEdge(dut.CLK_40M)
        await Timer(1, unit="ns")
        if _b(dut.DAVn) == 0:
            captured.append(_b(dut.DATA))
        p = _b(dut.PERR)
        if p == 1 and prev_perr == 0:
            perrs.append(True)
        prev_perr = p


def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.CLK_80M, 12500, unit="ps").start())
    cocotb.start_soon(Clock(dut.CLK_40M, 25000, unit="ps").start())


async def _warmup_then_monitor(dut, events, captured, perrs):
    """Drive the idle warm-up with PERR_CLR held (to swallow the serdec lock
    transient), then start monitoring and drive the events as one continuous
    biphase stream (the slice keeps the line level continuous)."""
    samples = stream_samples(events, warmup_cells=WARMUP_CELLS)
    warm_n = WARMUP_CELLS * SAMPLES_PER_CELL

    dut.PERR_CLR.value = 1
    await drive_samples(dut.CLK_80M, dut.TCLK, samples[:warm_n])
    dut.PERR_CLR.value = 0
    await ClockCycles(dut.CLK_40M, 2)

    cocotb.start_soon(monitor(dut, captured, perrs))
    await drive_samples(dut.CLK_80M, dut.TCLK, samples[warm_n:])
    await ClockCycles(dut.CLK_40M, 30)


@cocotb.test()
async def test_decode_known_events(dut):
    """A sequence of known TCLK event codes decodes back in order, no parity
    errors. 0x9D and 0xD2 are the two events from Fig. 1 of the TCLK paper."""
    _start_clocks(dut)
    await reset_dut(dut)

    events = [0x9D, 0xD2, 0x00, 0x07, 0x0F, 0xA5, 0x29]
    captured, perrs = [], []
    await _warmup_then_monitor(dut, events, captured, perrs)

    assert not perrs, f"unexpected PERR on clean frames: {len(perrs)}"
    assert captured == events, (
        f"decoded {[f'0x{x:02X}' for x in captured]} != "
        f"sent {[f'0x{x:02X}' for x in events]}"
    )
    dut._log.info(f"TCLK decode OK: {len(captured)} events decoded in order: "
                  f"{[f'0x{x:02X}' for x in captured]}")


@cocotb.test()
async def test_parity_error(dut):
    """A frame with a flipped parity bit must raise PERR and not produce DAVn."""
    _start_clocks(dut)
    await reset_dut(dut)

    # one good event, then a bad-parity event, then a good one
    events = [0x3C, (0x55, True), 0x42]
    captured, perrs = [], []
    await _warmup_then_monitor(dut, events, captured, perrs)

    assert perrs, "bad-parity frame did not raise PERR"
    assert 0x55 not in captured, f"bad-parity frame leaked into DATA: {captured}"
    assert 0x3C in captured and 0x42 in captured, \
        f"good frames around the bad one were lost: {[f'0x{x:02X}' for x in captured]}"
    dut._log.info(f"TCLK parity path OK: PERR raised, good frames {[f'0x{x:02X}' for x in captured]}")
