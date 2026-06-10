"""Cocotb tests for the ACLK readout datapath (tb_aclk_readout_top): the real
ACLK_RCV decoder feeding aclk_readout_core (null-drop + pack + dual-clock FIFO).

This is the end-to-end PL readout path minus the AXI face. The shared TX model
injects a mix of real and null/idle ACLK frames on the recovered-RX clock; a
separate read clock drains the FIFO. The test proves that:
  - the link aligns and real events reach the read side,
  - null / idle packets (event low byte 0xFF) are dropped, never forwarded,
  - events arrive in order with zero loss (overflow stays low),
  - the FIFO absorbs an initial reader stall (burst tolerance) without dropping.

On completion it writes an events-in vs events-out / occupancy plot under
sim_build/aclk_readout/plots/.
"""

import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from aclk_tx_model import stream_frames, MASK64

RX_PERIOD_NS = 16     # recovered RX clock (CLK1), about 62.5 MHz
RD_PERIOD_NS = 10     # PS-facing read clock, 100 MHz

NULL_EVENT = (0xFFFF, MASK64)     # low event byte 0xFF marks a null/idle packet


def _b(sig) -> int:
    """A 1-bit signal as 0/1; unresolved (x/z) returns -1 (warm-up not ready)."""
    try:
        return int(sig.value)
    except Exception:
        return -1


def _unpack(word: int):
    """Split a 144-bit FIFO word { TS[63:0], EVENT[15:0], DATA[63:0] }."""
    data = word & MASK64
    event = (word >> 64) & 0xFFFF
    ts = (word >> 80) & MASK64
    return (event, data, ts)


async def _reset(dut):
    dut.DATA_FROM_XCVR.value = 0
    dut.K_FROM_XCVR.value = 0
    dut.rd_en.value = 0
    dut.RESETn.value = 0
    dut.rd_rstn.value = 0
    await ClockCycles(dut.CLK1, 5)
    await ClockCycles(dut.rd_clk, 5)
    await Timer(1, unit="ns")
    dut.RESETn.value = 1
    dut.rd_rstn.value = 1
    await ClockCycles(dut.rd_clk, 4)
    await ClockCycles(dut.CLK1, 4)


async def _rx_monitor(dut, acct):
    """Count, in the rx domain, the events the packer pushes into the FIFO
    (valid and not null) and the null packets it drops. With no overflow, the
    push count equals the FIFO write count."""
    while not acct.get("rx_done"):
        await RisingEdge(dut.CLK1)
        await Timer(1, unit="ns")
        if _b(dut.aclk_valid) == 1:
            if _b(dut.dropped_null) == 1:
                acct["nulls"] += 1
            else:
                acct["w"] += 1


async def _drain(dut, captured, stop, start_delay_ns=0, throttle=1):
    """Pop words from the FIFO read side (FWFT). After an optional initial stall
    (to let the FIFO fill and prove burst tolerance), drain until the stream is
    done and the FIFO is empty."""
    dut.rd_en.value = 0
    if start_delay_ns:
        await Timer(start_delay_ns, unit="ns")
    while True:
        await RisingEdge(dut.rd_clk)
        await Timer(1, unit="ns")
        if _b(dut.empty) == 0:
            captured.append(int(dut.rd_data.value))
            dut.rd_en.value = 1
            await RisingEdge(dut.rd_clk)        # pop
            dut.rd_en.value = 0
            await Timer(1, unit="ns")
            for _ in range(throttle):
                await RisingEdge(dut.rd_clk)
                await Timer(1, unit="ns")
        else:
            dut.rd_en.value = 0
            if stop.get("done"):
                break


async def _sampler(dut, acct, captured, series, interval_ns=50):
    t = 0
    while not acct.get("done"):
        await Timer(interval_ns, unit="ns")
        t += interval_ns
        series.append((t, acct["w"] - len(captured), acct["w"], len(captured)))


def _save_plot(series, n_events, n_nulls, name):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                # noqa: BLE001 (optional dependency)
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return None
    if not series:
        return None

    ts  = [s[0] for s in series]
    occ = [s[1] for s in series]
    wr  = [s[2] for s in series]
    rd  = [s[3] for s in series]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)

    ax1.plot(ts, occ, color="tab:blue", lw=1.6)
    ax1.set_ylabel("FIFO occupancy (events)")
    ax1.set_title(
        f"ACLK readout: decode to FIFO  ({n_events} real events, {n_nulls} nulls dropped)"
    )
    ax1.set_ylim(bottom=0)
    ax1.grid(True, alpha=0.3)

    ax2.plot(ts, wr, color="tab:green",  lw=1.6, label="events into FIFO")
    ax2.plot(ts, rd, color="tab:orange", lw=1.6, label="events read out")
    ax2.set_xlabel("sim time (ns)")
    ax2.set_ylabel("cumulative events")
    ax2.legend(loc="upper left")
    ax2.grid(True, alpha=0.3)

    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "aclk_readout" / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


@cocotb.test()
async def test_decode_to_fifo_integrity(dut):
    """Stream a repeating mix of real and null ACLK frames through the decoder
    and out the FIFO. The reader stalls briefly first (burst tolerance), then
    drains. Real events must arrive in order with no loss; nulls must be
    dropped; overflow must stay low."""
    cocotb.start_soon(Clock(dut.CLK1, RX_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.rd_clk, RD_PERIOD_NS, unit="ns").start())
    await _reset(dut)

    real = [
        (0x0001, 0x1111222233334444),
        (0x00A5, 0xAAAABBBBCCCCDDDD),
        (0x1000, 0x0123456789ABCDEF),
        (0x3C00, 0xFEDCBA9876543210),
    ]
    stream_seq = [real[0], NULL_EVENT, real[1], real[2], NULL_EVENT, real[3]]
    nonnull = [f for f in stream_seq if (f[0] & 0xFF) != 0xFF]

    captured = []
    acct = {"w": 0, "nulls": 0}
    stop = {"done": False}
    series = []

    cocotb.start_soon(_rx_monitor(dut, acct))
    samp = cocotb.start_soon(_sampler(dut, acct, captured, series))
    drainer = cocotb.start_soon(_drain(dut, captured, stop, start_delay_ns=3000, throttle=2))

    await stream_frames(dut, stream_seq, repeat=30)
    await ClockCycles(dut.CLK1, 8)        # flush the last frames through the decoder
    stop["done"] = True
    await drainer
    acct["done"] = True
    acct["rx_done"] = True
    samp.cancel()

    caps = [_unpack(w) for w in captured]
    caps_ed = [(ev, da) for (ev, da, ts) in caps]
    ts_list = [ts for (ev, da, ts) in caps]

    assert int(dut.rx_aligned.value) == 1, "RX never aligned"
    assert int(dut.overflow.value) == 0, "events were dropped (FIFO overflowed)"
    assert caps, "no events reached the read side"

    for ev, da in caps_ed:
        assert (ev & 0xFF) != 0xFF, f"a null packet leaked through: 0x{ev:04X}"

    assert caps_ed[0] in nonnull, f"first capture {caps_ed[0]} is not one of the sent events"
    start = nonnull.index(caps_ed[0])
    for i, got in enumerate(caps_ed):
        exp = nonnull[(start + i) % len(nonnull)]
        assert got == exp, (
            f"order/data break at #{i}: got (0x{got[0]:04X}, 0x{got[1]:016X}), "
            f"expected (0x{exp[0]:04X}, 0x{exp[1]:016X})"
        )

    # Hardware timestamps must be strictly increasing in capture order.
    for i in range(1, len(ts_list)):
        assert ts_list[i] > ts_list[i - 1], (
            f"timestamp not monotonic at #{i}: {ts_list[i]} <= {ts_list[i - 1]}"
        )

    # Every real event the packer pushed must have been read back (no loss).
    assert len(caps) == acct["w"], (
        f"read {len(caps)} events but packer pushed {acct['w']}"
    )
    assert acct["nulls"] > 0, "test did not actually exercise null dropping"

    path = _save_plot(series, len(caps), acct["nulls"], "readout_events.png")
    if path:
        dut._log.info(f"readout plot written to {path}")
    dut._log.info(
        f"readout OK: {len(caps)} real events in order, "
        f"{acct['nulls']} nulls dropped, no loss, no overflow"
    )
