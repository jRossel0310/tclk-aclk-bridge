"""Capture aclk_gt_frame_gen's 16-bit + K word stream and confirm each group of
6 words reassembles (via the gigabit-ACLK model's inverse) into the compiled-in
events with a correct CRC. Proves the RTL generator and the Python golden model
agree on framing + CRC before any hardware."""
import sys
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aclk_tx_model import build_frame, frame_to_words  # noqa: E402
from plot_util import save_word_stream_plot              # noqa: E402

# Must match the RTL generator's compiled-in timeline (Step 3).
TIMELINE = [(0x0001, 0x1111222233334444), (0x00A5, 0xAAAABBBBCCCCDDDD),
            (0x1000, 0x0123456789ABCDEF)]
EXPECTED_WORDS = []
for ev, da in TIMELINE:
    EXPECTED_WORDS += frame_to_words(build_frame(ev, da))   # list of (word16, k2)

@cocotb.test()
async def test_frame_gen_matches_model(dut):
    cocotb.start_soon(Clock(dut.CLK1, 16, unit="ns").start())
    dut.RESETn.value = 0
    await ClockCycles(dut.CLK1, 5)
    await Timer(1, unit="ns")
    dut.RESETn.value = 1

    # Capture enough words to cover two full passes of the timeline, then phase-align.
    need = len(EXPECTED_WORDS) * 2
    got = []
    while len(got) < need + len(EXPECTED_WORDS):
        await RisingEdge(dut.CLK1)
        await Timer(1, unit="ns")
        got.append((int(dut.DATA16.value), int(dut.K_OUT.value)))

    # Find the comma word (K=0b01, low byte 0xBC) to phase-align, then compare a cycle.
    starts = [i for i, (w, k) in enumerate(got) if k == 0b01 and (w & 0xFF) == 0xBC]
    assert starts, "no comma word (K=01, low byte 0xBC) ever emitted"
    s = starts[0]
    window = got[s:s + len(EXPECTED_WORDS)]
    # EXPECTED_WORDS starts at the comma word of TIMELINE[0].
    assert window == EXPECTED_WORDS, (
        f"gen word stream != model:\n got={['(0x%04X,%d)'%(w,k) for w,k in window]}\n"
        f" exp={['(0x%04X,%d)'%(w,k) for w,k in EXPECTED_WORDS]}")
    dut._log.info(f"frame_gen matches model over {len(EXPECTED_WORDS)} words")

    # Emit a word-stream plot (word value + K flag vs sample index).
    plot_words = got[:s + len(EXPECTED_WORDS) + 6]
    out_path = (Path(__file__).resolve().parents[2]
                / "sim_build" / "aclkgt_gen" / "plots"
                / "aclkgt_gen_word_stream.png")
    result = save_word_stream_plot(plot_words, out_path)
    if result:
        cocotb.log.info(f"plot saved to {result}")
