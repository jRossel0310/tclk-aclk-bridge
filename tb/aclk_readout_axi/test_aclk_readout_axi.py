"""Cocotb tests for the full PL readout with its AXI4-Lite face
(tb_aclk_readout_axi_top): real ACLK_RCV decoder -> aclk_readout_axi.

A hand-rolled AXI4-Lite master (read + POP-write) stands in for the PS software.
The shared TX model injects real and null ACLK frames on the recovered-RX clock;
the master drains events over AXI on an independent PS clock. The tests prove:
  - events read over AXI match the decoded sequence in order, with hardware
    timestamps that strictly increase,
  - null / idle packets never appear, and overflow stays clear,
  - the EVENT_COUNT / NULL_COUNT / ERROR_COUNT registers track reality.

On completion an events-buffered vs events-read plot is written under
sim_build/aclk_readout_axi/plots/.
"""

import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from aclk_tx_model import stream_frames, MASK64
from axi_lite_bfm import axi_read, axi_write

RX_PERIOD_NS = 16     # recovered RX clock (CLK1)
AXI_PERIOD_NS = 10    # PS / AXI clock

# Register byte offsets (16-byte spacing — see aclk_readout_axi.sv).
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90
)

NULL_EVENT = (0xFFFF, MASK64)


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


# ---------------------------------------------------------------------------
# AXI4-Lite event read (built on the shared axi_read / axi_write BFM)
# ---------------------------------------------------------------------------

async def axi_read_event(dut):
    """Read one head event (EVENT/DATA/TS) and pop it. Returns (event, data, ts)."""
    ev = await axi_read(dut, EVENT) & 0xFFFF
    dhi = await axi_read(dut, DATA_HI)
    dlo = await axi_read(dut, DATA_LO)
    thi = await axi_read(dut, TS_HI)
    tlo = await axi_read(dut, TS_LO)
    await axi_write(dut, POP)
    return (ev, (dhi << 32) | dlo, (thi << 32) | tlo)


# ---------------------------------------------------------------------------
# Reset, monitor, sampler, plot
# ---------------------------------------------------------------------------

async def _reset(dut):
    dut.DATA_FROM_XCVR.value = 0
    dut.K_FROM_XCVR.value = 0
    dut.s_axi_awaddr.value = 0
    dut.s_axi_awvalid.value = 0
    dut.s_axi_wdata.value = 0
    dut.s_axi_wstrb.value = 0
    dut.s_axi_wvalid.value = 0
    dut.s_axi_bready.value = 0
    dut.s_axi_araddr.value = 0
    dut.s_axi_arvalid.value = 0
    dut.s_axi_rready.value = 0
    dut.RESETn.value = 0
    dut.s_axi_aresetn.value = 0
    await ClockCycles(dut.CLK1, 5)
    await ClockCycles(dut.s_axi_aclk, 5)
    await Timer(1, unit="ns")
    dut.RESETn.value = 1
    dut.s_axi_aresetn.value = 1
    await ClockCycles(dut.s_axi_aclk, 4)
    await ClockCycles(dut.CLK1, 4)


async def _rx_monitor(dut, acct):
    while not acct.get("rx_done"):
        await RisingEdge(dut.CLK1)
        await Timer(1, unit="ns")
        if _b(dut.aclk_valid) == 1:
            if _b(dut.dropped_null) == 1:
                acct["nulls"] += 1
            else:
                acct["w"] += 1


async def _idle_carrier(dut, stop):
    """Keep the link fed with valid idle/null frames so it stays aligned and the
    decoder never sees a frozen bus. Null frames are dropped by the packer, so
    they keep the link alive without filling the FIFO."""
    while not stop.get("done"):
        await stream_frames(dut, [NULL_EVENT], repeat=1)


async def _sampler(dut, acct, series, interval_ns=80):
    t = 0
    while not acct.get("done"):
        await Timer(interval_ns, unit="ns")
        t += interval_ns
        series.append((t, acct["w"], acct["read"]))


def _save_plot(series, n_events, name):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return None
    if not series:
        return None

    ts = [s[0] for s in series]
    w  = [s[1] for s in series]
    r  = [s[2] for s in series]
    occ = [a - b for a, b in zip(w, r)]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    ax1.plot(ts, occ, color="tab:blue", lw=1.6)
    ax1.set_ylabel("events buffered in FIFO")
    ax1.set_title(f"AXI-Lite readout: {n_events} events buffered then read over AXI")
    ax1.set_ylim(bottom=0)
    ax1.grid(True, alpha=0.3)
    ax2.plot(ts, w, color="tab:green",  lw=1.6, label="events into FIFO (decoder)")
    ax2.plot(ts, r, color="tab:orange", lw=1.6, label="events read over AXI")
    ax2.set_xlabel("sim time (ns)")
    ax2.set_ylabel("cumulative events")
    ax2.legend(loc="upper left")
    ax2.grid(True, alpha=0.3)

    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "aclk_readout_axi" / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_axi_event_readout(dut):
    """Stream real + null frames through the decoder, buffer them, then drain
    every event over AXI-Lite. Order, timestamps, no-null, no-overflow, and the
    EVENT/NULL/ERROR counters must all check out."""
    cocotb.start_soon(Clock(dut.CLK1, RX_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, AXI_PERIOD_NS, unit="ns").start())
    await _reset(dut)

    real = [
        (0x0001, 0x1111222233334444),
        (0x00A5, 0xAAAABBBBCCCCDDDD),
        (0x1000, 0x0123456789ABCDEF),
        (0x3C00, 0xFEDCBA9876543210),
    ]
    stream_seq = [real[0], NULL_EVENT, real[1], real[2], NULL_EVENT, real[3]]
    nonnull = [f for f in stream_seq if (f[0] & 0xFF) != 0xFF]

    acct = {"w": 0, "nulls": 0, "read": 0}
    series = []
    stop = {"done": False}
    cocotb.start_soon(_rx_monitor(dut, acct))
    cocotb.start_soon(_sampler(dut, acct, series))

    # Stream a bounded burst (under the FIFO depth) with the reader idle, so the
    # events buffer up. Then keep the link fed with idle null frames so it stays
    # aligned and the decoder never sees a frozen bus while we read out. Nulls are
    # dropped by the packer, so they do not fill the FIFO.
    await stream_frames(dut, stream_seq, repeat=10)
    carrier = cocotb.start_soon(_idle_carrier(dut, stop))
    await ClockCycles(dut.CLK1, 8)        # flush the last real frame through the decoder
    await ClockCycles(dut.s_axi_aclk, 6)  # let counters settle across the CDC

    assert int(dut.rx_aligned.value) == 1, "RX never aligned"
    status = await axi_read(dut, STATUS)
    assert (status >> 1) & 1 == 0, "overflow set: the FIFO dropped events"

    # Drain every buffered event over AXI (the idle carrier keeps the link valid).
    collected = []
    while True:
        status = await axi_read(dut, STATUS)
        if status & 0x1:                  # empty
            break
        collected.append(await axi_read_event(dut))
        acct["read"] = len(collected)

    acct["done"] = True
    acct["rx_done"] = True

    ed = [(ev, da) for (ev, da, ts) in collected]
    tslist = [ts for (ev, da, ts) in collected]

    assert collected, "no events read over AXI"
    for ev, da in ed:
        assert (ev & 0xFF) != 0xFF, f"a null packet leaked through: 0x{ev:04X}"

    assert ed[0] in nonnull, f"first event {ed[0]} is not one of the sent events"
    start = nonnull.index(ed[0])
    for i, got in enumerate(ed):
        exp = nonnull[(start + i) % len(nonnull)]
        assert got == exp, (
            f"order/data break at #{i}: got (0x{got[0]:04X}, 0x{got[1]:016X}), "
            f"expected (0x{exp[0]:04X}, 0x{exp[1]:016X})"
        )
    for i in range(1, len(tslist)):
        assert tslist[i] > tslist[i - 1], (
            f"timestamp not monotonic at #{i}: {tslist[i]} <= {tslist[i - 1]}"
        )

    ev_count  = await axi_read(dut, EVENT_COUNT)
    null_count = await axi_read(dut, NULL_COUNT)
    err_count = await axi_read(dut, ERROR_COUNT)
    assert ev_count == len(collected), \
        f"EVENT_COUNT {ev_count} != events read {len(collected)}"
    assert null_count > 0, "NULL_COUNT did not register the dropped nulls"
    assert err_count == 0, f"ERROR_COUNT {err_count} on a clean stream"

    stop["done"] = True                  # stop the idle carrier

    path = _save_plot(series, len(collected), "axi_readout.png")
    if path:
        dut._log.info(f"AXI readout plot written to {path}")
    dut._log.info(
        f"AXI readout OK: {len(collected)} events read in order with monotonic "
        f"timestamps; EVENT_COUNT={ev_count}, NULL_COUNT={null_count}, ERROR_COUNT={err_count}"
    )


@cocotb.test()
async def test_axi_error_count(dut):
    """A corrupted frame raises ACLK_ERROR in the decoder and must increment the
    ERROR_COUNT register read over AXI."""
    cocotb.start_soon(Clock(dut.CLK1, RX_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, AXI_PERIOD_NS, unit="ns").start())
    await _reset(dut)

    frames = [(0x0001, 0x1111222233334444), (0x00A5, 0xAAAABBBBCCCCDDDD)]
    # Corrupt frame index 8 (well after alignment) to force one ACLK_ERROR.
    await stream_frames(dut, frames, repeat=8, corrupt_at=8)
    await ClockCycles(dut.CLK1, 8)
    await ClockCycles(dut.s_axi_aclk, 6)

    err_count = await axi_read(dut, ERROR_COUNT)
    ev_count  = await axi_read(dut, EVENT_COUNT)
    assert err_count >= 1, f"ERROR_COUNT did not register the corrupted frame (got {err_count})"
    assert ev_count > 0, "no good events decoded around the corrupted frame"
    dut._log.info(f"error-count OK: ERROR_COUNT={err_count}, EVENT_COUNT={ev_count}")
