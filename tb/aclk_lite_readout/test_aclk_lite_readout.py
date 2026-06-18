"""Cocotb test for the full Manchester PL chain (aclk_lite_readout_top): the
Manchester ACLK-Lite decoder feeding the AXI-Lite readout, end to end.

A mix of 8 / 16 / 80-bit frames is driven onto the Manchester line; the events
are buffered, then read out over AXI4-Lite (the stand-in for the PS software).
The test proves that across the whole chain:
  - each event's id and (when present) 64-bit data arrive correctly, in order,
  - the FLAGS in the EVENT register report has_data and is_tclk correctly,
  - hardware timestamps are present and strictly increasing,
  - EVENT_COUNT matches and ERROR_COUNT stays zero.

On completion an events-buffered vs events-read plot is written under
sim_build/aclk_lite_readout/plots/.
"""

import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from manchester_tx_model import send_frame
from axi_lite_bfm import axi_read, axi_write, _b

RX_PERIOD_NS = 10     # recovered-RX / oversampling clock
AXI_PERIOD_NS = 14    # PS / AXI clock (independent, exercises the CDC)

MASK64 = (1 << 64) - 1

# Register byte offsets (16-byte spacing — see aclk_readout_axi.sv). EVENT now returns {FLAGS, EVENT}.
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90
)

FLAG_HAS_DATA = 0x1
FLAG_IS_TCLK = 0x2

DEBUG, LOCK = 0xA0, 0xC0


async def reset_dut(dut):
    dut.line.value = 1
    dut.mmcm_locked.value = 1
    dut.rx_rstn.value = 0
    dut.s_axi_aresetn.value = 0
    dut.s_axi_awaddr.value = 0
    dut.s_axi_awvalid.value = 0
    dut.s_axi_wdata.value = 0
    dut.s_axi_wstrb.value = 0
    dut.s_axi_wvalid.value = 0
    dut.s_axi_bready.value = 0
    dut.s_axi_araddr.value = 0
    dut.s_axi_arvalid.value = 0
    dut.s_axi_rready.value = 0
    await ClockCycles(dut.rx_clk, 5)
    await ClockCycles(dut.s_axi_aclk, 5)
    await Timer(1, unit="ns")
    dut.rx_rstn.value = 1
    dut.s_axi_aresetn.value = 1
    await ClockCycles(dut.s_axi_aclk, 4)
    await ClockCycles(dut.rx_clk, 5)


async def _rx_monitor(dut, acct):
    while not acct.get("rx_done"):
        await RisingEdge(dut.rx_clk)
        await Timer(1, unit="ns")
        if _b(dut.dbg_event_valid) == 1:
            acct["w"] += 1


async def _sampler(dut, acct, series, interval_ns=200):
    t = 0
    while not acct.get("done"):
        await Timer(interval_ns, unit="ns")
        t += interval_ns
        series.append((t, acct["w"], acct["read"]))


async def axi_read_event(dut):
    """Read one head event over AXI and pop it -> (event, flags, data, ts)."""
    ev_reg = await axi_read(dut, EVENT)
    event = ev_reg & 0xFFFF
    flags = (ev_reg >> 16) & 0xFFFF
    dhi = await axi_read(dut, DATA_HI)
    dlo = await axi_read(dut, DATA_LO)
    thi = await axi_read(dut, TS_HI)
    tlo = await axi_read(dut, TS_LO)
    await axi_write(dut, POP)
    return (event, flags, (dhi << 32) | dlo, (thi << 32) | tlo)


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
    ax1.set_title(f"Manchester to AXI: {n_events} events decoded, buffered, read")
    ax1.set_ylim(bottom=0)
    ax1.grid(True, alpha=0.3)
    ax2.plot(ts, w, color="tab:green",  lw=1.6, label="events decoded into FIFO")
    ax2.plot(ts, r, color="tab:orange", lw=1.6, label="events read over AXI")
    ax2.set_xlabel("sim time (ns)")
    ax2.set_ylabel("cumulative events")
    ax2.legend(loc="upper left")
    ax2.grid(True, alpha=0.3)
    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "aclk_lite_readout" / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


@cocotb.test()
async def test_manchester_to_axi(dut):
    """Drive a mix of 8 / 16 / 80-bit Manchester frames, buffer them, then read
    every event over AXI and check id, data, flags, timestamps, and counts."""
    cocotb.start_soon(Clock(dut.rx_clk, RX_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, AXI_PERIOD_NS, unit="ns").start())
    await reset_dut(dut)

    # (encoder payload, length) -> expected (event_id, data_or_None, is_tclk)
    sends = [
        (0x12, 8),
        (0x00A5, 16),
        ((0x1000 << 64) | 0xCAFEF00DDEADBEEF, 80),
        (0x34, 8),
        (0x3C01, 16),
        ((0x2222 << 64) | 0x0123456789ABCDEF, 80),
    ]
    expected = [
        (0x0012, None, 1),
        (0x00A5, None, 0),
        (0x1000, 0xCAFEF00DDEADBEEF, 0),
        (0x0034, None, 1),
        (0x3C01, None, 0),
        (0x2222, 0x0123456789ABCDEF, 0),
    ]

    acct = {"w": 0, "read": 0}
    series = []
    cocotb.start_soon(_rx_monitor(dut, acct))
    cocotb.start_soon(_sampler(dut, acct, series))

    # Buffer all events (reader idle). The Manchester decoder's idle is steady
    # high, so leaving the line idle between/after frames produces no garbage.
    for payload, length in sends:
        await send_frame(dut.rx_clk, dut.line, payload, length, idle_before=4, idle_after=32)
    await ClockCycles(dut.rx_clk, 40)         # flush the last frame end
    await ClockCycles(dut.s_axi_aclk, 6)      # let counters settle across the CDC

    # Drain every buffered event over AXI.
    collected = []
    while True:
        status = await axi_read(dut, STATUS)
        if status & 0x1:                      # empty
            break
        collected.append(await axi_read_event(dut))
        acct["read"] = len(collected)

    acct["done"] = True
    acct["rx_done"] = True

    assert (await axi_read(dut, STATUS)) & 0x2 == 0, "overflow set: events were dropped"
    assert len(collected) == len(expected), \
        f"read {len(collected)} events, expected {len(expected)}: {collected}"

    last_ts = -1
    for i, ((ev, flags, data, ts), (exp_ev, exp_data, exp_tclk)) in enumerate(zip(collected, expected)):
        has_data = bool(flags & FLAG_HAS_DATA)
        is_tclk = bool(flags & FLAG_IS_TCLK)
        assert ev == exp_ev, f"#{i} event 0x{ev:04X} != 0x{exp_ev:04X}"
        assert is_tclk == bool(exp_tclk), f"#{i} is_tclk {is_tclk} != {bool(exp_tclk)}"
        assert has_data == (exp_data is not None), f"#{i} has_data {has_data} wrong"
        if exp_data is not None:
            assert data == exp_data, f"#{i} data 0x{data:016X} != 0x{exp_data:016X}"
        assert ts > last_ts, f"#{i} timestamp {ts} not increasing (prev {last_ts})"
        last_ts = ts

    ev_count = await axi_read(dut, EVENT_COUNT)
    err_count = await axi_read(dut, ERROR_COUNT)
    assert ev_count == len(expected), f"EVENT_COUNT {ev_count} != {len(expected)}"
    assert err_count == 0, f"ERROR_COUNT {err_count} on clean frames"

    lock = await axi_read(dut, LOCK)
    assert lock & 0x1 == 1, f"LOCK 0x{lock:08X} did not reflect mmcm_locked=1"
    dbg = await axi_read(dut, DEBUG)
    edges = dbg & 0x3FFFFFFF
    assert edges > 0, f"DEBUG edge count {edges} did not climb while the line toggled"

    path = _save_plot(series, len(collected), "manchester_to_axi.png")
    if path:
        dut._log.info(f"plot written to {path}")
    dut._log.info(
        f"full chain OK: {len(collected)} events (8/16/80-bit) decoded and read over AXI; "
        f"flags, data, and monotonic timestamps all correct"
    )
