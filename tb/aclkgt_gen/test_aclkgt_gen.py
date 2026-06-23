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
    _plot_word_stream(got[:s + len(EXPECTED_WORDS) + 6], s, out_dir=Path(__file__).resolve().parents[2]
                      / "sim_build" / "aclkgt_gen" / "plots")


def _plot_word_stream(words, comma_start, out_dir):
    """Save a two-panel plot: DATA16 value and K_OUT flag vs sample index.
    Comma positions (K=01, low byte 0xBC) are marked with vertical lines."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                            # noqa: BLE001
        import warnings
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return

    # Delegate to the shared utility if it has a word-stream helper,
    # otherwise draw inline (no duplication in plot_util.py needed).
    indices = list(range(len(words)))
    values  = [w for w, _k in words]
    kflags  = [k for _w, k in words]
    commas  = [i for i, (w, k) in enumerate(words) if k == 0b01 and (w & 0xFF) == 0xBC]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6), sharex=True)

    ax1.step(indices, values, where="post", color="tab:blue", lw=1.4)
    for c in commas:
        ax1.axvline(c, color="tab:red", lw=0.8, alpha=0.7, linestyle="--")
    ax1.set_ylabel("DATA16 (hex)")
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"0x{int(v):04X}"))
    ax1.set_title("aclk_gt_frame_gen word stream (red = comma word boundary)")
    ax1.grid(True, alpha=0.3)

    ax2.step(indices, kflags, where="post", color="tab:orange", lw=1.4)
    for c in commas:
        ax2.axvline(c, color="tab:red", lw=0.8, alpha=0.7, linestyle="--")
    ax2.set_ylabel("K_OUT")
    ax2.set_xlabel("sample index (clock cycle)")
    ax2.set_ylim(-0.1, 1.1)
    ax2.grid(True, alpha=0.3)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "aclkgt_gen_word_stream.png"
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    cocotb.log.info(f"plot saved to {out_path}")
