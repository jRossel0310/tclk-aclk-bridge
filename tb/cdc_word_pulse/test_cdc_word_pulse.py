"""Toggle-handshake word CDC: each src_valid delivers exactly one dst_valid with
the captured word; a dst-domain reset must NOT replay a stale transfer."""
import warnings
from pathlib import Path

import cocotb
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from cocotb_helpers import _b, start_clock


def _start_clocks(dut):
    # cocotb kills a test's spawned tasks (including clocks) when the test
    # ends, so every test must start its own clocks.
    start_clock(dut.src_clk, 10)   # 100 MHz source
    start_clock(dut.dst_clk, 25)   # 40 MHz destination (slower, worst case)


async def _reset(dut):
    dut.src_rstn.value = 0
    dut.dst_rstn.value = 0
    dut.src_valid.value = 0
    dut.src_data.value = 0
    await ClockCycles(dut.dst_clk, 4)
    await Timer(1, unit="ns")
    dut.src_rstn.value = 1
    dut.dst_rstn.value = 1
    await ClockCycles(dut.dst_clk, 6)   # warmup counter flush


async def _send(dut, value):
    await RisingEdge(dut.src_clk)
    dut.src_data.value = value
    dut.src_valid.value = 1
    await RisingEdge(dut.src_clk)
    dut.src_valid.value = 0


async def _collect(dut, cycles):
    """Sample dst_valid for `cycles` dst clocks; return list of received words."""
    got = []
    for _ in range(cycles):
        await RisingEdge(dut.dst_clk)
        await Timer(1, unit="ns")
        if _b(dut.dst_valid) == 1:
            got.append(int(dut.dst_data.value))
    return got


@cocotb.test()
async def test_single_and_repeated_transfers(dut):
    _start_clocks(dut)
    await _reset(dut)

    valid_levels = []

    await _send(dut, 0xDEADBEEF)
    got = await _collect(dut, 12)
    assert got == [0xDEADBEEF], f"expected one delivery of 0xDEADBEEF, got {got}"

    # spaced transfers (>= 3 dst clocks apart) all arrive, once each
    sent = [0x11111111, 0x22222222, 0x33333333]
    got = []
    for v in sent:
        await _send(dut, v)
        got += await _collect(dut, 12)
        for _ in range(3):
            await RisingEdge(dut.dst_clk)
            await Timer(1, unit="ns")
            valid_levels.append(_b(dut.dst_valid))
    assert got == sent, f"expected {sent}, got {got}"

    _save_plot(valid_levels)


@cocotb.test()
async def test_dst_reset_does_not_replay(dut):
    _start_clocks(dut)
    await _reset(dut)

    # One real transfer flips the toggle to 1.
    await _send(dut, 0xCAFED00D)
    got = await _collect(dut, 12)
    assert got == [0xCAFED00D]

    # Reset ONLY the destination (models a GT relock resetting rx domain logic).
    dut.dst_rstn.value = 0
    await ClockCycles(dut.dst_clk, 3)
    await Timer(1, unit="ns")
    dut.dst_rstn.value = 1

    # No new src_valid: the stale toggle level must NOT fire dst_valid again.
    got = await _collect(dut, 20)
    assert got == [], f"stale transfer replayed after dst reset: {got}"

    # A fresh transfer still works.
    await _send(dut, 0x55AA55AA)
    got = await _collect(dut, 12)
    assert got == [0x55AA55AA]


def _save_plot(valid_levels):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return
    xs = list(range(len(valid_levels)))
    fig, ax = plt.subplots(figsize=(9, 2.5))
    ax.step(xs, valid_levels, where="post", color="tab:blue", lw=1.4)
    ax.set_ylim(-0.2, 1.2)
    ax.set_yticks([0, 1])
    ax.set_xlabel("dst_clk sample")
    ax.set_ylabel("dst_valid")
    ax.set_title("cdc_word_pulse: dst_valid strobes between transfers")
    ax.grid(True, alpha=0.3)
    out_dir = (Path(__file__).resolve().parents[2]
               / "sim_build" / "cdc_word_pulse" / "plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_dir / "dst_valid.png", dpi=120)
    plt.close(fig)
