"""Generator -> RX gearbox -> ACLK_RCV in one sim: the decoded events must equal
the generator's compiled-in timeline, in order, with no CRC errors."""
import sys
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TIMELINE = [(0x0001, 0x1111222233334444), (0x00A5, 0xAAAABBBBCCCCDDDD),
            (0x1000, 0x0123456789ABCDEF)]


@cocotb.test()
async def test_gen_to_rcv(dut):
    cocotb.start_soon(Clock(dut.CLK1, 16, unit="ns").start())
    dut.RESETn.value = 0
    await ClockCycles(dut.CLK1, 5)
    await Timer(1, unit="ns")
    dut.RESETn.value = 1

    captured, errors = [], 0
    # Run long enough for several timeline passes after alignment.
    # 6 * len(TIMELINE) * 8 + 200 cycles gives warmup + alignment + several passes.
    total_cycles = 6 * len(TIMELINE) * 8 + 200
    times_ns = []
    cumulative_decoded = []
    align_time_ns = None
    for cycle in range(total_cycles):
        await RisingEdge(dut.CLK1)
        await Timer(1, unit="ns")
        if int(dut.ACLK_VALID.value) == 1:
            captured.append((int(dut.ACLK_EVENT.value), int(dut.ACLK_DATA.value)))
        if int(dut.ACLK_ERROR.value) == 1:
            errors += 1
        # Track alignment time for the plot
        if align_time_ns is None and int(dut.RX_ALIGNED_OUT.value) == 1:
            align_time_ns = cycle * 16
        # Record time series for plot
        times_ns.append(cycle * 16)
        cumulative_decoded.append(len(captured))

    assert int(dut.RX_ALIGNED_OUT.value) == 1, "RX never aligned to the generator"
    assert errors == 0, f"unexpected ACLK_ERROR on a clean generator: {errors}"
    assert captured, "no events decoded from the generator"
    # Every captured event must be one of the timeline entries, in cyclic order.
    start = TIMELINE.index(captured[0])
    for i, got in enumerate(captured):
        exp = TIMELINE[(start + i) % len(TIMELINE)]
        assert got == exp, f"#{i} decoded {got} != timeline {exp}"
    dut._log.info(f"gen<->rcv agree: {len(captured)} events decoded in order, 0 errors")

    # Save the loopback agreement plot.
    _plot_loopback(times_ns, cumulative_decoded, align_time_ns,
                   out_dir=Path(__file__).resolve().parents[2]
                   / "sim_build" / "aclkgt_gen_loop" / "plots")


def _plot_loopback(times_ns, cumulative_decoded, align_time_ns, out_dir):
    """Save a plot of cumulative decoded events vs sim time, marking alignment."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                            # noqa: BLE001
        import warnings
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(times_ns, cumulative_decoded, color="tab:blue", lw=1.6,
            label="cumulative decoded events")
    if align_time_ns is not None:
        ax.axvline(align_time_ns, color="tab:red", lw=1.2, linestyle="--",
                   label=f"RX_ALIGNED at {align_time_ns} ns")
    ax.set_xlabel("sim time (ns)")
    ax.set_ylabel("cumulative events decoded")
    ax.set_title("aclk_gt_frame_gen -> ACLK_RCV loopback: decoded events vs time")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "aclkgt_gen_loop_events.png"
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    cocotb.log.info(f"plot saved to {out_path}")
