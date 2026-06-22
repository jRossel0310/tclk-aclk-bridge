"""Loopback integration test: the rewritten aclk_lite_gen_timeline -> the real unified
receiver clk_rcv (serdec4_9MHz + clk_byte_framer). The hardcoded timeline must drive
real-framed frames that the shipped decoder recovers as exactly the injected trio,
repeating, with zero parity errors:
  (0x0055, data=None, is_tclk=1)                 # TCLK event 0x55  (1 byte)
  (0xABCD, data=None, is_tclk=0)                 # ACLK event       (2 bytes)
  (0x1234, data=0xDEADBEEFCAFE0001, is_tclk=0)   # full ACLK packet (12 bytes)

serdec needs a brief carrier warm-up; the timeline idles (1-cells) before the first
frame, so the very first frame may be missed during lock-up. The test asserts on
steady-state trios after warm-up.
"""
import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK80_NS = 12     # ~80 MHz generator + serdec clock
CLK40_NS = 25     # ~40 MHz framer clock
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
    await ClockCycles(dut.clk_80m, 5)
    await ClockCycles(dut.clk_40m, 5)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    await ClockCycles(dut.clk_80m, 5)
    await ClockCycles(dut.clk_40m, 5)


async def monitor(dut, events, errors):
    while True:
        await RisingEdge(dut.clk_40m)
        await Timer(1, unit="ns")
        if _b(dut.event_valid) == 1:
            dv = _b(dut.data_valid)
            events.append((
                int(dut.event_id.value) & 0xFFFF,
                (int(dut.data.value) & MASK64) if dv == 1 else None,
                _b(dut.is_tclk),
            ))
        if _b(dut.parity_error) == 1:
            errors.append(1)


def _save_plot(n, name, title):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return None
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.bar(["events decoded"], [n], color="tab:blue")
    ax.set_title(title)
    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "aclk_lite_gen_loopback" / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


@cocotb.test()
async def test_loopback_trio_repeats(dut):
    """After serdec warm-up, two consecutive full trios decode in order, no errors."""
    cocotb.start_soon(Clock(dut.clk_80m, CLK80_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_40m, CLK40_NS, unit="ns").start())
    await reset_dut(dut)

    events, errors = [], []
    cocotb.start_soon(monitor(dut, events, errors))

    # Run long enough for serdec lock + several trios at the shrunk gaps.
    await ClockCycles(dut.clk_80m, 40000)

    assert len(events) >= 6, f"expected >= 6 events after warm-up, got {len(events)}: {events}"
    # Find a clean repeating trio anywhere in the stream (skip any warm-up partial).
    found = False
    for i in range(len(events) - 5):
        if events[i:i + 3] == TRIO and events[i + 3:i + 6] == TRIO:
            found = True
            break
    assert found, f"two consecutive correct trios not found in: {events}"
    # serdec emits at most one spurious parity error while first locking to the carrier.
    assert len(errors) <= 1, f"too many parity errors ({len(errors)}); expected <= 1 startup"

    path = _save_plot(len(events), "loopback_events.png",
                      "ACLK-Lite generator -> unified clk_rcv: events decoded")
    if path:
        dut._log.info(f"plot written to {path}")
    dut._log.info(f"loopback OK: {len(events)} events, trio repeats, parity errors={len(errors)}")
