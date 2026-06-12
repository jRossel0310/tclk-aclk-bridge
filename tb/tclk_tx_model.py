"""Python model of the Fermilab TCLK biphase-mark ("modified Manchester")
encoder, per resources/TCLK_Paper.pdf, used to drive rtl/aclk_bridge/TCLK_RCV
in simulation.

TCLK: 10 MHz serial. There is a transition at every 100 ns bit-cell boundary,
plus an extra transition at mid-cell iff the bit is 1 (none for 0). The
transition direction is arbitrary (it just carries from the previous level).
Word = start(0) + 8 data bits (MSB first) + parity. Idle between events =
continuous 1s (a 10 MHz square wave).

The line is oversampled at CLK_80M, so there are 8 samples per cell (4 per
half-cell). The parity convention is taken from TCLK_DESERIALIZER2.v: the
deserializer detects a frame when its shift window is {1,1,0,d7..d0} and checks
parity_reg == parity_calc, which works out to parity = 1 ^ XOR(data bits).
"""

from cocotb.triggers import RisingEdge

SAMPLES_PER_CELL = 8
HALF = SAMPLES_PER_CELL // 2


def event_bits(byte: int, bad_parity: bool = False):
    """start(0) + 8 data (MSB first) + parity. The deserializer accepts a frame
    when its 11-bit window {1,1,0,d7..d0} parity matches the parity bit, which
    works out to parity = XOR(data) (even parity)."""
    data = [(byte >> (7 - i)) & 1 for i in range(8)]
    parity = 0
    for d in data:
        parity ^= d
    if bad_parity:
        parity ^= 1
    return [0] + data + [parity]


def biphase_samples(bits, level=1):
    """Turn a bit list into CLK_80M line samples (biphase-mark). Returns
    (samples, ending_level) so successive calls carry the line level."""
    out = []
    for b in bits:
        level ^= 1                  # transition at the cell boundary
        out += [level] * HALF
        if b:
            level ^= 1              # extra mid-cell transition for a 1
        out += [level] * HALF
    return out, level


def stream_samples(events, warmup_cells=40, gap_cells=12, level=1):
    """Idle warm-up, then each event followed by an idle gap. `events` may be
    ints (good frames) or (byte, True) tuples for a bad-parity frame."""
    samples, level = biphase_samples([1] * warmup_cells, level)
    for ev in events:
        byte, bad = (ev if isinstance(ev, tuple) else (ev, False))
        s, level = biphase_samples(event_bits(byte, bad), level)
        samples += s
        g, level = biphase_samples([1] * gap_cells, level)
        samples += g
    return samples


async def drive_samples(clk, tclk_sig, samples):
    """Drive one line sample per clk rising edge."""
    for s in samples:
        await RisingEdge(clk)
        tclk_sig.value = s
