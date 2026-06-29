"""Two destination domains observe the same free-running ref_clk tick count:
each output is monotonic and they agree within a couple of sync cycles."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer


def _save_ts_plot(samples_a, samples_b, out_dir):
    """Save a step plot of ts_a and ts_b vs ref-clock sample index."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        import warnings
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return None
    from pathlib import Path
    xs = list(range(len(samples_a)))
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.step(xs, samples_a, where="post", color="tab:blue", lw=1.4, label="ts_a")
    ax.step(xs, samples_b, where="post", color="tab:orange", lw=1.4, label="ts_b", linestyle="--")
    ax.set_xlabel("ref_clk sample index")
    ax.set_ylabel("timestamp value")
    ax.set_title("global_timebase: ts_a and ts_b vs ref_clk samples")
    ax.legend()
    ax.grid(True, alpha=0.3)
    out_path = Path(out_dir) / "plots"
    out_path.mkdir(parents=True, exist_ok=True)
    path = out_path / "ts_ab_vs_refclk.png"
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


@cocotb.test()
async def test_shared_monotonic(dut):
    cocotb.start_soon(Clock(dut.ref_clk, 10, unit="ns").start())
    cocotb.start_soon(Clock(dut.dst_clk_a, 25, unit="ns").start())
    cocotb.start_soon(Clock(dut.dst_clk_b, 16, unit="ns").start())
    dut.ref_rstn.value = 0
    await ClockCycles(dut.ref_clk, 5)
    await Timer(1, unit="ns")
    dut.ref_rstn.value = 1
    # Wait long enough for the synchronizer pipeline (2 stages) and the
    # registered gray->binary decode (1 stage) to flush X through both
    # dst_clk_a (25 ns period) and dst_clk_b (16 ns period) domains.
    # 10 ref_clk cycles (100 ns) covers >3 dst_clk_a cycles and >6 dst_clk_b.
    await ClockCycles(dut.ref_clk, 10)

    samples_a = []
    samples_b = []
    prev_a = prev_b = -1
    for _ in range(400):
        await RisingEdge(dut.ref_clk)
        await Timer(1, unit="ns")
        a = int(dut.ts_a.value)
        b = int(dut.ts_b.value)
        assert a >= prev_a, f"ts_a went backwards: {a} < {prev_a}"
        assert b >= prev_b, f"ts_b went backwards: {b} < {prev_b}"
        prev_a, prev_b = a, b
        samples_a.append(a)
        samples_b.append(b)

    # After running, both domains should be close. The tolerance of 6 accounts
    # for up to 2-stage Gray CDC synchronizer latency (2 dst_clk cycles) plus
    # the registered gray->binary decode flip-flop (1 dst_clk cycle), sampled
    # at the faster ref_clk rate. This is CDC sync latency, not a logic bug.
    assert abs(int(dut.ts_a.value) - int(dut.ts_b.value)) <= 6, (
        f"domains diverged: ts_a={int(dut.ts_a.value)} ts_b={int(dut.ts_b.value)}"
    )
    dut._log.info(f"timebase a={int(dut.ts_a.value)} b={int(dut.ts_b.value)}")

    # Save a plot of the captured timestamps.
    import os
    from pathlib import Path
    proj_dir = Path(__file__).resolve().parents[2]
    build_dir = proj_dir / "sim_build" / "global_timebase"
    _save_ts_plot(samples_a, samples_b, build_dir)
