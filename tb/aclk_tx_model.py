"""Reusable Python model of the ACLK-Lite transmit/encoder side, transcribed
bit-for-bit from rtl/aclk_bridge (crc8_calc.v + gearbox_96_to_16.v). It builds
valid 96-bit frames and gears them down to the 16-bit + K-flag word stream that
ACLK_RCV consumes, so any testbench can inject known ACLK traffic.

96-bit frame layout P (P[95] = MSB):
    P[95:88] = 0xBC comma | P[87:72] = EVENT | P[71:8] = DATA | P[7:0] = CRC8

The numeric functions here are pure Python. stream_frames() is the one cocotb
helper: it drives a DUT that exposes the ACLK_RCV receive-side port names
(CLK1, DATA_FROM_XCVR, K_FROM_XCVR), so it works for both the decoder testbench
and any readout testbench wrapping the decoder.
"""

from cocotb.triggers import RisingEdge

COMMA  = 0xBC
MASK64 = (1 << 64) - 1
MASK88 = (1 << 88) - 1


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


async def stream_frames(dut, frames, repeat, corrupt_at=None):
    """Emit the 6 words of each frame contiguously, one per CLK1 edge, no gaps.
    The RX gearbox's internal 1-cycle delay needs gapless streaming or the byte
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
