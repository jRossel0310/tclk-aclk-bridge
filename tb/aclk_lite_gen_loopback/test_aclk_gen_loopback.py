"""Loopback integration test: aclk_lite_gen_timeline -> aclk_lite_decoder, both at
OVERSAMPLE=12. The hardcoded timeline must drive frames that the real decoder
recovers as exactly the injected trio, repeating, with zero parity errors:
  (0x0055, data=None, is_tclk=1)   # 8-bit TCLK event 0x55
  (0xABCD, data=None, is_tclk=0)   # 16-bit ACLK event
  (0x1234, data=0xDEADBEEFCAFE0001, is_tclk=0)   # 80-bit ACLK event + data

The tb top shrinks IDLE_GAP/TRIO_GAP so a few trios run quickly. On completion an
events-over-time throughput plot is written under
sim_build/aclk_lite_gen_loopback/plots/.
"""

import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK_NS = 10
MASK64 = (1 << 64) - 1

TRIO = [
    (0x0055, None, 1),
    (0xABCD, None, 0),
    (0x1234, 0xDEADBEEFCAFE0001, 0),
]


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


async def reset_dut(dut):
    dut.rstn.value = 0
    await ClockCycles(dut.clk, 5)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    await ClockCycles(dut.clk, 5)


async def monitor(dut, events, errors, times):
    cyc = 0
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        cyc += 1
        if _b(dut.event_valid) == 1:
            dv = _b(dut.data_valid)
            events.append((
                int(dut.event_id.value) & 0xFFFF,
                (int(dut.data.value) & MASK64) if dv == 1 else None,
                _b(dut.is_tclk),
            ))
            times.append(cyc)
        if _b(dut.parity_error) == 1:
            errors.append(cyc)


def _save_throughput_plot(times, name, title):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return None
    cum = list(range(1, len(times) + 1))
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.step(times, cum, where="post", color="tab:blue", lw=1.6)
    ax.set_xlabel("oversampling-clock cycle")
    ax.set_ylabel("cumulative events decoded")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "aclk_lite_gen_loopback" / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


@cocotb.test()
async def test_loopback_trio_repeats(dut):
    """At least two full trios decode in order with no parity errors."""
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    await reset_dut(dut)

    events, errors, times = [], [], []
    cocotb.start_soon(monitor(dut, events, errors, times))

    # Run long enough for >= 2 full trios at the shrunk gaps (see tb top params).
    await ClockCycles(dut.clk, 6000)

    assert not errors, f"unexpected parity errors at cycles {errors}"
    assert len(events) >= 6, f"expected >= 6 events (2 trios), got {len(events)}: {events}"
    assert events[0:3] == TRIO, f"first trio wrong: {events[0:3]}"
    assert events[3:6] == TRIO, f"second trio wrong: {events[3:6]}"

    path = _save_throughput_plot(
        times, "loopback_throughput.png",
        "ACLK-Lite generator -> decoder: cumulative events decoded",
    )
    if path:
        dut._log.info(f"throughput plot written to {path}")
    dut._log.info(f"loopback OK: {len(events)} events, trio repeats, 0 parity errors")
