"""wr_timebase_axi: STATUS bits, SEC_ARM write arms + locks the monitor at PPS,
atomic SEC_NOW/NS_NOW latch, PPS_COUNT / CELLS_LAST diagnostics, CTRL disarm +
sticky lost_lock semantics. Sim second = 50 cells = 5000 ns."""
import warnings
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles, Timer

from cocotb_helpers import start_clock
from axi_lite_bfm import axi_read, axi_write
from wr_model import WrGen

STATUS, SEC_ARM, SEC_NOW, NS_NOW, PPS_COUNT, CELLS_LAST, CTRL = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60)

SIM_NS_PER_SEC = 5000
SEC0 = 1_751_800_000


def _start_clocks(dut):
    # cocotb kills a test's spawned tasks (including clock drivers) when the
    # test ends, so EVERY test must start its own clocks.
    start_clock(dut.s_axi_aclk, 10)


async def _reset(dut):
    dut.s_axi_aresetn.value = 0
    dut.locked_a.value = 0
    dut.locked_b.value = 0
    dut.wr_clk10.value = 0
    dut.wr_pps.value = 0
    for sig in ("awaddr", "awvalid", "wdata", "wstrb", "wvalid",
                "bready", "araddr", "arvalid", "rready"):
        getattr(dut, "s_axi_" + sig).value = 0
    await ClockCycles(dut.s_axi_aclk, 10)
    await Timer(1, unit="ns")
    dut.s_axi_aresetn.value = 1
    await ClockCycles(dut.s_axi_aclk, 10)


async def _wait_status_bit(dut, bit, value, timeout_cycles=3000):
    for _ in range(timeout_cycles // 10):
        s = await axi_read(dut, STATUS)
        if ((s >> bit) & 1) == value:
            return s
        await ClockCycles(dut.s_axi_aclk, 10)
    raise AssertionError(f"STATUS bit {bit} never became {value}")


@cocotb.test()
async def test_status_and_diag_counters(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps)
    gen.start()

    # replica lock inputs surface as STATUS[1:0]
    s = await axi_read(dut, STATUS)
    assert (s & 0x3) == 0
    dut.locked_a.value = 1
    dut.locked_b.value = 1
    await ClockCycles(dut.s_axi_aclk, 5)
    s = await axi_read(dut, STATUS)
    assert (s & 0x3) == 0x3, f"replica lock bits missing: 0x{s:08X}"
    dut.locked_a.value = 0
    dut.locked_b.value = 0

    # aliveness + interval measurement come up on their own (no arm needed)
    s = await _wait_status_bit(dut, 3, 1)          # pps_alive
    assert (s >> 4) & 1 == 1, "clk10_alive not set"
    await ClockCycles(dut.s_axi_aclk, 2 * SIM_NS_PER_SEC // 10)
    cells = await axi_read(dut, CELLS_LAST)
    assert cells == 50, f"CELLS_LAST {cells} != 50"
    c1 = await axi_read(dut, PPS_COUNT)
    await ClockCycles(dut.s_axi_aclk, SIM_NS_PER_SEC // 10 + 50)
    c2 = await axi_read(dut, PPS_COUNT)
    assert c2 > c1, f"PPS_COUNT stuck at {c1}"
    gen.stop()


@cocotb.test()
async def test_arm_now_regs_and_sticky(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps)
    gen.start()
    await ClockCycles(dut.s_axi_aclk, 100)

    # before arm: strict zeros
    assert await axi_read(dut, SEC_NOW) == 0
    assert await axi_read(dut, NS_NOW) == 0

    # arm: readback + arm_pending, then the monitor locks at the next PPS
    await axi_write(dut, SEC_ARM, SEC0)
    assert await axi_read(dut, SEC_ARM) == SEC0
    s = await axi_read(dut, STATUS)
    assert (s >> 5) & 1 == 1, "arm_pending not set after SEC_ARM write"
    s = await _wait_status_bit(dut, 2, 1)          # locked_mon

    # SEC_NOW/NS_NOW: sec is the armed label (+ elapsed sim-seconds), the
    # NS_NOW read returns the value latched AT the SEC_NOW read (atomic pair).
    samples = []
    for _ in range(20):
        sec = await axi_read(dut, SEC_NOW)
        ns = await axi_read(dut, NS_NOW)
        assert sec >= SEC0, f"SEC_NOW {sec} < armed {SEC0}"
        assert ns < SIM_NS_PER_SEC, f"NS_NOW {ns} >= sim second"
        samples.append((sec - SEC0) * SIM_NS_PER_SEC + ns)
        await ClockCycles(dut.s_axi_aclk, 37)
    assert samples == sorted(samples), f"combined time not monotonic: {samples}"

    # CTRL[1] disarm: monitor unlocks, sticky sets; CTRL[0] clears the sticky
    await axi_write(dut, CTRL, 0x2)
    s = await _wait_status_bit(dut, 2, 0)
    assert (s >> 8) & 1 == 1, f"lost_lock sticky not set: 0x{s:08X}"
    assert await axi_read(dut, SEC_NOW) == 0, "SEC_NOW nonzero after disarm"
    await axi_write(dut, CTRL, 0x1)
    s = await axi_read(dut, STATUS)
    assert (s >> 8) & 1 == 0, "lost_lock sticky did not clear"

    # re-arm relocks
    await axi_write(dut, SEC_ARM, SEC0 + 500)
    await _wait_status_bit(dut, 2, 1)

    _save_plot(samples)
    gen.stop()


def _save_plot(samples):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return
    xs = list(range(len(samples)))
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.step(xs, samples, where="post", color="tab:blue", lw=1.6)
    ax.set_xlabel("SEC_NOW/NS_NOW read pair index")
    ax.set_ylabel("combined time since arm (sim ns)")
    ax.set_title("wr_timebase_axi: atomic SEC_NOW/NS_NOW reads are monotonic")
    ax.grid(True, alpha=0.3)
    out_dir = (Path(__file__).resolve().parents[2]
               / "sim_build" / "wr_timebase_axi" / "plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_dir / "now_reads.png", dpi=120)
    plt.close(fig)
