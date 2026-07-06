"""wr_timebase: strict-zero before arm, arm-and-lock at PPS, ns tracks the WR
cells with local interpolation, both replicas agree, loss of either reference
unlocks strictly (re-arm required), disarm unlocks immediately.

Sim second = 50 cells = 5000 ns (see tb/wr_model.py)."""
import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer
from cocotb.utils import get_sim_time

from cocotb_helpers import _b, start_clock
from wr_model import WrGen

SIM_NS_PER_SEC = 5000     # 50 cells * 100 ns
SEC0 = 1_751_800_000      # arbitrary Unix-like armed seconds value


def _start_clocks(dut):
    # cocotb kills a test's spawned tasks (including clock drivers) when the
    # test ends, so EVERY test must start its own clocks.
    start_clock(dut.clk_a, 25)
    cocotb.start_soon(Clock(dut.clk_b, 6400, unit="ps").start())
    start_clock(dut.cfg_clk, 10)


async def _reset(dut):
    dut.rstn.value = 0
    dut.cfg_rstn.value = 0
    dut.cfg_valid.value = 0
    dut.cfg_disarm.value = 0
    dut.cfg_sec.value = 0
    dut.wr_clk10.value = 0
    dut.wr_pps.value = 0
    await ClockCycles(dut.cfg_clk, 10)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    dut.cfg_rstn.value = 1
    await ClockCycles(dut.cfg_clk, 10)


async def _arm(dut, sec):
    await RisingEdge(dut.cfg_clk)
    dut.cfg_sec.value = sec
    dut.cfg_disarm.value = 0
    dut.cfg_valid.value = 1
    await RisingEdge(dut.cfg_clk)
    dut.cfg_valid.value = 0


async def _disarm(dut):
    await RisingEdge(dut.cfg_clk)
    dut.cfg_disarm.value = 1
    dut.cfg_valid.value = 1
    await RisingEdge(dut.cfg_clk)
    dut.cfg_valid.value = 0
    dut.cfg_disarm.value = 0


def _split(ts):
    return (ts >> 32) & 0xFFFFFFFF, ts & 0xFFFFFFFF


def _combined(ts):
    """Sim-timeline value in ns since second SEC0 (sim second = 5000 ns)."""
    sec, ns = _split(ts)
    return (sec - SEC0) * SIM_NS_PER_SEC + ns


async def _wait_locked(dut, timeout_sim_seconds=3):
    for _ in range(timeout_sim_seconds * SIM_NS_PER_SEC // 25):
        await RisingEdge(dut.clk_a)
        await Timer(1, unit="ns")
        if _b(dut.locked_a) == 1 and _b(dut.locked_b) == 1:
            return
    raise AssertionError("timebase never locked after arm")


@cocotb.test()
async def test_strict_arm_lock_and_tracking(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps)
    gen.start()

    # -- strict zero before arm: run 3 sim-seconds, ts stays 0 --
    for _ in range(30):
        await ClockCycles(dut.clk_a, 20)
        await Timer(1, unit="ns")
        assert _b(dut.ts_a) == 0, "ts_a nonzero before arm"
        assert _b(dut.ts_b) == 0, "ts_b nonzero before arm"
        assert _b(dut.locked_a) == 0 and _b(dut.locked_b) == 0
    # references are alive and the interval cell count is measured anyway
    assert _b(dut.pps_alive_a) == 1 and _b(dut.clk10_alive_a) == 1
    assert _b(dut.cells_last_a) == 50, (
        f"cells_last {_b(dut.cells_last_a)} != 50")

    # -- arm mid-second, lock at the next PPS with the armed seconds --
    await _arm(dut, SEC0)
    await Timer(1, unit="ns")
    await _wait_locked(dut)
    sec_a, ns_a = _split(_b(dut.ts_a))
    assert sec_a == SEC0, f"sec {sec_a} != armed {SEC0}"
    assert ns_a < SIM_NS_PER_SEC, f"ns {ns_a} out of range"
    assert _b(dut.arm_pending_a) == 0, "arm still pending after lock"

    # -- seconds self-increment at each PPS --
    n_pps = len(gen.pps_times_ns)
    while len(gen.pps_times_ns) < n_pps + 2:
        await ClockCycles(dut.clk_a, 20)
    await ClockCycles(dut.clk_a, 8)   # let the edge cross the 2-FF sync
    await Timer(1, unit="ns")
    sec_a2, _ = _split(_b(dut.ts_a))
    assert sec_a2 >= SEC0 + 2, f"sec did not increment: {sec_a2}"

    # -- ns tracks WR truth, both domains agree; collect samples for the plot --
    samples = []   # (sim_ns, expected_ns, ns_a, ns_b, comb_a, comb_b)
    for i in range(200):
        await ClockCycles(dut.clk_a, 7 + (i % 5))
        await Timer(1, unit="ns")
        now = get_sim_time(unit="ns")
        expected = now - gen.pps_times_ns[-1]
        ts_a, ts_b = _b(dut.ts_a), _b(dut.ts_b)
        _, ns_a = _split(ts_a)
        _, ns_b = _split(ts_b)
        ca, cb = _combined(ts_a), _combined(ts_b)
        # DUT lags real time by 2-FF sync + edge detect; never leads by > 2 clk.
        # Skip samples within a sync window of the PPS (sec/ns straddle there).
        if expected > 200:
            assert ns_a <= expected + 50,  f"ns_a {ns_a} leads wall time {expected}"
            assert ns_a >= expected - 250, f"ns_a {ns_a} lags too far ({expected})"
            assert abs(ca - cb) <= 150, (
                f"domains disagree: a={ca} b={cb} (delta {ca - cb})")
        samples.append((now, expected, ns_a, ns_b, ca, cb))

    _save_plot(samples)
    gen.stop()


@cocotb.test()
async def test_reference_loss_unlocks_strictly(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps)
    gen.start()
    await ClockCycles(dut.clk_a, 300)
    await _arm(dut, SEC0)
    await _wait_locked(dut)

    # -- 10 MHz dies: unlock within CLK10_TIMEOUT (16 clk_a = 400 ns) + margin --
    gen.clk10_on = False
    dut.wr_clk10.value = 0
    await ClockCycles(dut.clk_a, 30)
    await Timer(1, unit="ns")
    assert _b(dut.locked_a) == 0 and _b(dut.ts_a) == 0, "clk10 loss did not unlock"

    # -- restoring the reference does NOT relock (strict: re-arm required) --
    gen.clk10_on = True
    await ClockCycles(dut.clk_a, 3 * SIM_NS_PER_SEC // 25)
    await Timer(1, unit="ns")
    assert _b(dut.locked_a) == 0, "relocked without a fresh arm (strict violated)"
    await _arm(dut, SEC0 + 100)
    await _wait_locked(dut)
    sec_a, _ = _split(_b(dut.ts_a))
    assert sec_a >= SEC0 + 100

    # -- PPS dies: unlock after PPS_TIMEOUT (240 clk_a = 6 us) --
    gen.pps_on = False
    await ClockCycles(dut.clk_a, 300)
    await Timer(1, unit="ns")
    assert _b(dut.locked_a) == 0 and _b(dut.ts_a) == 0, "PPS loss did not unlock"
    gen.pps_on = True
    await _arm(dut, SEC0 + 200)
    await _wait_locked(dut)
    gen.stop()


@cocotb.test()
async def test_disarm_unlocks(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps)
    gen.start()
    await ClockCycles(dut.clk_a, 300)
    await _arm(dut, SEC0)
    await _wait_locked(dut)

    await _disarm(dut)
    await ClockCycles(dut.clk_a, 12)   # cfg CDC + a couple of clk_a
    await Timer(1, unit="ns")
    assert _b(dut.locked_a) == 0 and _b(dut.ts_a) == 0, "disarm did not unlock"
    assert _b(dut.locked_b) == 0 and _b(dut.ts_b) == 0
    gen.stop()


def _save_plot(samples):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return
    t   = [s[0] for s in samples]
    exp = [s[1] for s in samples]
    na  = [s[2] for s in samples]
    nb  = [s[3] for s in samples]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(t, exp, color="tab:gray",   lw=1.0, label="wall-clock ns since PPS")
    ax.plot(t, na,  color="tab:blue",   lw=1.4, label="ns_a (25 ns domain)")
    ax.plot(t, nb,  color="tab:orange", lw=1.4, linestyle="--",
            label="ns_b (6.4 ns domain)")
    ax.set_xlabel("sim time (ns)")
    ax.set_ylabel("ns field")
    ax.set_title("wr_timebase: ns tracks the WR cells in both domains")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    out_dir = (Path(__file__).resolve().parents[2]
               / "sim_build" / "wr_timebase" / "plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_dir / "ns_tracking.png", dpi=120)
    plt.close(fig)
