"""Cocotb tests for rtl/aclk_bridge/ACLK_REV.v (module ACLK_RCV), the ACLK-Lite
decoder.

ACLK_RCV consumes a continuous stream of 16-bit words + 2-bit K flags (one per
CLK1 edge). Internally GEARBOX_16_TO_96 reassembles every 6 words into a 96-bit
packet, CRC8_CALC checks it, and after a few consecutive good packets the link
declares RX_ALIGNED_OUT and pulses ACLK_VALID once per good packet, presenting
ACLK_EVENT[15:0] + ACLK_DATA[63:0].

To drive it we model the *transmit* side in Python: build the 96-bit packet
(comma + event + data + CRC8) and gearbox it down to the 6-word stream exactly
the way rtl/aclk_bridge/gearbox_96_to_16.v does. The CRC and word/byte mapping
were transcribed bit-for-bit from the RTL; build_frame() self-checks the CRC so
a bad port fails loudly instead of silently never aligning.

96-bit frame layout P (P[95] = MSB):
    P[95:88] = 0xBC comma  |  P[87:72] = EVENT  |  P[71:8] = DATA  |  P[7:0] = CRC8
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK_PERIOD_NS = 10
COMMA = 0xBC

MASK64 = (1 << 64) - 1
MASK88 = (1 << 88) - 1


# ---------------------------------------------------------------------------
# Python model of the TX encoder (mirrors the RTL exactly)
# ---------------------------------------------------------------------------

def crc8(x88: int) -> int:
    """CRC-8 transcribed from crc8_calc.v: seed = inverted top byte (~data[87:80]),
    then shift in bits [79:0] MSB-first, XOR 0x2F whenever the pre-shift MSB is 1."""
    x88 &= MASK88
    result = (~(x88 >> 80)) & 0xFF          # ~data_reg[87:80]
    for i in range(87, 7, -1):              # i = 87 .. 8
        bit = (x88 >> (i - 8)) & 1          # data_reg[i-8]: bits 79..0
        msb = (result >> 7) & 1
        result = ((result << 1) | bit) & 0xFF
        if msb:
            result ^= 0x2F
    return result & 0xFF


def build_frame(event: int, data: int) -> int:
    """Return the 96-bit frame {0xBC, EVENT[15:0], DATA[63:0], CRC8}."""
    packet80 = ((event & 0xFFFF) << 64) | (data & MASK64)
    crc = crc8((packet80 << 8) & MASK88)            # CRC over {packet80, 0x00}
    # Self-check: the RX checks CRC over {packet80, crc} == 0 (crc8_calc.v line 92).
    assert crc8((packet80 << 8) | crc) == 0, "CRC self-check failed, bad crc8 port"
    return (COMMA << 88) | (packet80 << 8) | crc


def _byte(P: int, msb: int) -> int:
    """The 8-bit field P[msb:msb-7]."""
    return (P >> (msb - 7)) & 0xFF


def frame_to_words(P: int):
    """Mirror gearbox_96_to_16.v: 6 words, each {hi_byte, lo_byte} (byte-swapped
    16-bit slice), MSB slice first. Only word0 carries K=2'b01 (its low byte is
    the 0xBC comma, which is what aligns the RX gearbox). NEVER set K[1]."""
    return [
        ((_byte(P, 87) << 8) | _byte(P, 95), 0b01),   # {P[87:80], P[95:88]=0xBC}
        ((_byte(P, 71) << 8) | _byte(P, 79), 0b00),   # {P[71:64], P[79:72]}
        ((_byte(P, 55) << 8) | _byte(P, 63), 0b00),   # {P[55:48], P[63:56]}
        ((_byte(P, 39) << 8) | _byte(P, 47), 0b00),   # {P[39:32], P[47:40]}
        ((_byte(P, 23) << 8) | _byte(P, 31), 0b00),   # {P[23:16], P[31:24]}
        ((_byte(P,  7) << 8) | _byte(P, 15), 0b00),   # {P[7:0],   P[15:8]}
    ]


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


async def stream_frames(dut, frames, repeat, corrupt_at=None):
    """Emit the 6 words of each frame contiguously, one per CLK1 edge, no gaps,
    the RX gearbox's internal 1-cycle delay needs gapless streaming or the byte
    slots misfill. `frames` is a list of (event, data); the list is looped
    `repeat` times. If `corrupt_at` is given, flip one non-comma payload bit on
    that global frame index (to exercise the CRC error path)."""
    idx = 0
    for _ in range(repeat):
        for (event, data) in frames:
            words = frame_to_words(build_frame(event, data))
            if corrupt_at is not None and idx == corrupt_at:
                w, k = words[3]
                words[3] = (w ^ 0x0040, k)        # flip a payload data bit
            for (word16, k2) in words:
                await RisingEdge(dut.CLK1)
                dut.DATA_FROM_XCVR.value = word16
                dut.K_FROM_XCVR.value = k2
            idx += 1


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
