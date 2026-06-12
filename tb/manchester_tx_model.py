"""Reusable Python model of the ACLK-Lite Manchester encoder (the TX side that
matches rtl/aclk_lite/aclk_lite_decoder.sv). It builds the per-clk line levels
for a length-aware frame and can drive a DUT's line input.

Encoding (must match the decoder): bit b -> two half-bits {~b, b}, OVERSAMPLE
clk cycles per bit; idle = steady high; frame = start(0) + payload (MSB first) +
parity (even = XOR payload), then back to idle.

OVERSAMPLE must equal the decoder's parameter (default 16).
"""

from cocotb.triggers import RisingEdge

OVERSAMPLE = 16
HALF = OVERSAMPLE // 2


def frame_levels(payload: int, length: int, flip_bit: int | None = None):
    """Per-clk line levels for one frame. If flip_bit is given, the parity is
    computed over the intended payload and then that payload bit is flipped, so
    payload and parity disagree (used to exercise the parity-error path)."""
    pbits = [(payload >> (length - 1 - i)) & 1 for i in range(length)]
    parity = 0
    for b in pbits:
        parity ^= b
    if flip_bit is not None:
        pbits[flip_bit] ^= 1
    bits = [0] + pbits + [parity]               # start, payload, parity
    levels = []
    for b in bits:
        levels += [1 - b] * HALF + [b] * HALF    # first half ~b, second half b
    return levels


async def send_frame(clk, line_sig, payload, length, idle_before=8, idle_after=48, flip_bit=None):
    """Drive idle, then one frame, then idle, on `clk`. idle_after must exceed
    ~1.5 bits so the decoder sees the frame end. Returns the driven line levels."""
    levels = ([1] * idle_before) + frame_levels(payload, length, flip_bit) + ([1] * idle_after)
    for lv in levels:
        await RisingEdge(clk)
        line_sig.value = lv
    return levels
