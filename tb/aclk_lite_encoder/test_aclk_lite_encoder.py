"""Cocotb test for rtl/aclk_lite/aclk_lite_encoder.sv, the real-framing biphase-mark
encoder. For each frame_type (TCLK 1-byte, ACLK event 2-byte, full 12-byte) the
encoder's emitted line, reduced to its transition pattern, must contain the golden
biphase-mark framing of the assembled bytes (transition at every cell boundary, plus
a mid-cell transition iff the cell bit is 1). The transition view is level- and
phase-independent, so it is robust to exactly when the frame starts.
"""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from clk_tx_model import frame_bits   # real-framing per-byte bit list

SPC = 8        # SAMPLES_PER_CELL
HALF = 4
CLK_NS = 12    # exact period is irrelevant; only sample counts matter


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


def byte_list(event_id, data, frame_type):
    """Mirror the encoder's byte assembly (MSB-first)."""
    if frame_type == 0:
        return [event_id & 0xFF]
    if frame_type == 1:
        return [(event_id >> 8) & 0xFF, event_id & 0xFF]
    bl = [(event_id >> 8) & 0xFF, event_id & 0xFF]
    for shift in range(56, -1, -8):
        bl.append((data >> shift) & 0xFF)
    bl += [0x00, 0x00]            # CRC + control placeholders
    return bl


def golden_transitions(bl):
    """Per cell: a transition at offset 0 (boundary), and at offset HALF iff bit==1."""
    out = []
    for bit in frame_bits(bl):
        cell = [0] * SPC
        cell[0] = 1
        cell[HALF] = bit
        out += cell
    return out


def _contains(big, sub):
    n = len(sub)
    return any(big[i:i + n] == sub for i in range(len(big) - n + 1))


async def reset_dut(dut):
    dut.start.value = 0
    dut.event_id.value = 0
    dut.data.value = 0
    dut.frame_type.value = 0
    dut.rstn.value = 0
    await ClockCycles(dut.clk, 5)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    await ClockCycles(dut.clk, 5)


async def send_and_capture(dut, event_id, data, frame_type, n_cells):
    dut.event_id.value = event_id
    dut.data.value = data
    dut.frame_type.value = frame_type
    await RisingEdge(dut.clk)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    levels = []
    for _ in range(n_cells * SPC):
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        levels.append(_b(dut.line))
    return levels


@cocotb.test()
async def test_encoder_biphase_framing(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    await reset_dut(dut)

    cases = [
        (0x0055, 0, 0),                    # TCLK 1 byte
        (0xABCD, 0, 1),                    # ACLK event 2 bytes
        (0x1234, 0xDEADBEEFCAFE0001, 2),   # full 12 bytes
    ]
    for ev, dat, ft in cases:
        bl = byte_list(ev, dat, ft)
        n_cells = len(bl) * 10 + 30        # frame cells + idle margin
        levels = await send_and_capture(dut, ev, dat, ft, n_cells)
        trans = [levels[i] ^ levels[i - 1] for i in range(1, len(levels))]
        gt = golden_transitions(bl)
        assert _contains(trans, gt), \
            f"frame_type {ft}: biphase framing not found in emitted line"
        await ClockCycles(dut.clk, 20 * SPC)   # drain back to idle

    dut._log.info("encoder biphase framing OK for 1/2/12-byte frames")
