"""Cocotb tests for rtl/aclk_bridge/ACLK_REV.v (module ACLK_RCV), the ACLK-Lite
decoder.

ACLK_RCV consumes a continuous stream of 16-bit words + 2-bit K flags (one per
CLK1 edge). Internally GEARBOX_16_TO_96 reassembles every 6 words into a 96-bit
packet, CRC8_CALC checks it, and after a few consecutive good packets the link
declares RX_ALIGNED_OUT and pulses ACLK_VALID once per good packet, presenting
ACLK_EVENT[15:0] + ACLK_DATA[63:0].

The transmit-side model that drives it lives in tb/aclk_tx_model.py and is shared
with the readout testbench (tb/aclk_readout).
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from aclk_tx_model import stream_frames

CLK_PERIOD_NS = 10


# ---------------------------------------------------------------------------
# DUT helpers
# ---------------------------------------------------------------------------

def start_clock(dut):
    cocotb.start_soon(Clock(dut.CLK1, CLK_PERIOD_NS, unit="ns").start())


async def reset_dut(dut):
    """Assert the async active-low reset, leaving the bus idle on return."""
    dut.DATA_FROM_XCVR.value = 0
    dut.K_FROM_XCVR.value = 0
    dut.RESETn.value = 0
    await ClockCycles(dut.CLK1, 5)
    await Timer(1, unit="ns")
    dut.RESETn.value = 1
    await RisingEdge(dut.CLK1)


async def monitor(dut, captured, errors):
    """Capture (EVENT, DATA) on every ACLK_VALID cycle and note ACLK_ERROR cycles.
    Runs until cocotb cancels it at test end. Pre-alignment frames produce no
    VALID, so the warm-up is dropped automatically."""
    while True:
        await RisingEdge(dut.CLK1)
        await Timer(1, unit="ns")             # let the registered outputs settle
        if int(dut.ACLK_VALID.value) == 1:
            captured.append((int(dut.ACLK_EVENT.value), int(dut.ACLK_DATA.value)))
        if int(dut.ACLK_ERROR.value) == 1:
            errors.append((int(dut.ACLK_EVENT.value), int(dut.ACLK_DATA.value)))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_alignment_and_decode(dut):
    """Stream one frame repeatedly: the link aligns and decodes EVENT/DATA exactly."""
    start_clock(dut)
    await reset_dut(dut)

    event, data = 0x1234, 0xCAFEF00DDEADBEEF
    captured, errors = [], []
    cocotb.start_soon(monitor(dut, captured, errors))

    await stream_frames(dut, [(event, data)], repeat=32)
    await ClockCycles(dut.CLK1, 2)            # let the monitor grab the last VALIDs

    assert int(dut.RX_ALIGNED_OUT.value) == 1, "RX never aligned"
    assert captured, "no ACLK_VALID pulses, decoder never produced output"
    for ev, da in captured:
        assert (ev, da) == (event, data), \
            f"decode mismatch: got (0x{ev:04X}, 0x{da:016X})"
    assert not errors, f"unexpected ACLK_ERROR on clean frames: {len(errors)}"
    dut._log.info(f"aligned + decoded {len(captured)} frames as (0x{event:04X}, 0x{data:016X})")


@cocotb.test()
async def test_event_sequence_integrity(dut):
    """A repeating sequence of distinct events decodes back in order."""
    start_clock(dut)
    await reset_dut(dut)

    # All event low-bytes != 0xFF (0x..FF denotes a null/idle packet, per top_module.v).
    frames = [
        (0x0001, 0x1111222233334444),
        (0x00A5, 0xAAAABBBBCCCCDDDD),
        (0x1000, 0x0123456789ABCDEF),
        (0x3C00, 0xFEDCBA9876543210),
    ]
    captured, errors = [], []
    cocotb.start_soon(monitor(dut, captured, errors))

    await stream_frames(dut, frames, repeat=16)
    await ClockCycles(dut.CLK1, 2)

    assert int(dut.RX_ALIGNED_OUT.value) == 1, "RX never aligned"
    assert len(captured) >= 2 * len(frames), \
        f"too few captures ({len(captured)}) to verify ordering"
    assert not errors, f"unexpected ACLK_ERROR on clean frames: {len(errors)}"

    # Captures are a contiguous, in-order slice of the repeating sequence.
    assert captured[0] in frames, f"first capture {captured[0]} not a sent frame"
    start = frames.index(captured[0])
    for i, got in enumerate(captured):
        exp = frames[(start + i) % len(frames)]
        assert got == exp, (
            f"sequence break at #{i}: expected (0x{exp[0]:04X}, 0x{exp[1]:016X}), "
            f"got (0x{got[0]:04X}, 0x{got[1]:016X})"
        )
    dut._log.info(f"sequence OK: {len(captured)} captures follow the input order")


@cocotb.test()
async def test_bad_crc_error_path(dut):
    """A single corrupted frame raises ACLK_ERROR (not VALID); the link stays
    aligned and good frames keep decoding."""
    start_clock(dut)
    await reset_dut(dut)

    event, data = 0x1234, 0xCAFEF00DDEADBEEF
    captured, errors = [], []
    cocotb.start_soon(monitor(dut, captured, errors))

    # Corrupt frame #12, well after alignment (first VALID lands ~frame 6).
    await stream_frames(dut, [(event, data)], repeat=20, corrupt_at=12)
    await ClockCycles(dut.CLK1, 2)

    assert errors, "corrupted frame did not raise ACLK_ERROR"
    assert int(dut.RX_ALIGNED_OUT.value) == 1, \
        "one bad packet should not drop alignment (needs 4 consecutive)"
    # Only good frames are ever captured (a bad CRC suppresses VALID), so every
    # capture must equal the intended payload, the corrupt frame never appears.
    for ev, da in captured:
        assert (ev, da) == (event, data), \
            f"a corrupted frame leaked into VALID output: (0x{ev:04X}, 0x{da:016X})"
    assert len(captured) >= 5, f"link did not recover: only {len(captured)} good captures"
    dut._log.info(f"error path OK: {len(errors)} ACLK_ERROR, {len(captured)} good captures, still aligned")
