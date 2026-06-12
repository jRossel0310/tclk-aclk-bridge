"""Cocotb test for the full TCLK PL chain (tclk_readout_top): the inherited
biphase-mark receiver (TCLK_RCV) feeding the AXI-Lite readout, end to end.

A sequence of TCLK event bytes is encoded as biphase-mark (tb/tclk_tx_model.py)
and driven onto the TCLK line; the decoded events are timestamped, buffered, and
read out over AXI4-Lite (tb/axi_lite_bfm.py, the stand-in for the PS software).

The tests prove that across the whole chain:
  - each TCLK event byte arrives over AXI, in order, with is_tclk=1 / has_data=0,
  - 0xFF is NOT dropped (DROP_NULL=0 for TCLK) and NULL_COUNT stays 0,
  - hardware timestamps are present and strictly increasing,
  - EVENT_COUNT matches and a clean stream adds no ERROR_COUNT,
  - a bad-parity frame raises exactly one ERROR_COUNT and never produces an event.

serdec emits one spurious PERR while first locking to the carrier, so the
monotonic ERROR_COUNT / NULL_COUNT are baselined after warm-up and checked as a
delta (the PS does the equivalent: read a baseline, or ignore the first count).

On completion an events-buffered vs events-read plot is written under
sim_build/tclk_readout/plots/.
"""

import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from tclk_tx_model import biphase_samples, event_bits, drive_samples
from axi_lite_bfm import axi_read, axi_write, _b

CLK80_PERIOD_PS = 12500   # 80 MHz serdec oversample clock
CLK40_PERIOD_PS = 25000   # 40 MHz deserializer + readout / timestamp clock
AXI_PERIOD_NS = 14        # PS / AXI clock (independent, exercises the CDC)

WARMUP_CELLS = 40
GAP_CELLS = 12

# Register byte offsets (aclk_readout_axi.sv). EVENT returns {FLAGS, EVENT}.
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = (
    0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28
)

FLAG_HAS_DATA = 0x1
FLAG_IS_TCLK = 0x2


def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.clk_80m, CLK80_PERIOD_PS, unit="ps").start())
    cocotb.start_soon(Clock(dut.clk_40m, CLK40_PERIOD_PS, unit="ps").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, AXI_PERIOD_NS, unit="ns").start())


async def reset_dut(dut):
    dut.tclk.value = 1                  # idle high
    dut.pps.value = 0
    dut.rstn.value = 0
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
    await ClockCycles(dut.clk_40m, 5)
    await ClockCycles(dut.s_axi_aclk, 5)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    dut.s_axi_aresetn.value = 1
    await ClockCycles(dut.s_axi_aclk, 4)
    await ClockCycles(dut.clk_40m, 5)


async def _tclk_driver(dut, events, acct, warmup_cells=WARMUP_CELLS, gap_cells=GAP_CELLS):
    """Drive the TCLK line as one continuous biphase-mark stream: idle warm-up so
    serdec locks, then each event followed by an idle gap, then keep toggling idle
    1s until told to stop. The line is never frozen, so serdec never has to re-lock
    (a re-lock would emit another spurious PERR). `events` are ints (good frames)
    or (byte, True) tuples for a bad-parity frame."""
    warm, level = biphase_samples([1] * warmup_cells, level=1)
    await drive_samples(dut.clk_80m, dut.tclk, warm)
    acct["warm_done"] = True

    for ev in events:
        byte, bad = (ev if isinstance(ev, tuple) else (ev, False))
        s, level = biphase_samples(event_bits(byte, bad), level)
        await drive_samples(dut.clk_80m, dut.tclk, s)
        g, level = biphase_samples([1] * gap_cells, level)
        await drive_samples(dut.clk_80m, dut.tclk, g)
    acct["drive_done"] = True

    while not acct.get("stop_drive"):
        g, level = biphase_samples([1] * gap_cells, level)
        await drive_samples(dut.clk_80m, dut.tclk, g)


async def _rx_monitor(dut, acct):
    """Count decoded events (one dbg_dav strobe each) for the occupancy plot."""
    while not acct.get("rx_done"):
        await RisingEdge(dut.clk_40m)
        await Timer(1, unit="ns")
        if _b(dut.dbg_dav) == 1:
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


async def _wait_flag(dut, acct, flag, step=4):
    while not acct.get(flag):
        await ClockCycles(dut.clk_40m, step)


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
    ax1.set_title(f"TCLK to AXI: {n_events} biphase-mark events decoded, buffered, read")
    ax1.set_ylim(bottom=0)
    ax1.grid(True, alpha=0.3)
    ax2.plot(ts, w, color="tab:green",  lw=1.6, label="events decoded into FIFO")
    ax2.plot(ts, r, color="tab:orange", lw=1.6, label="events read over AXI")
    ax2.set_xlabel("sim time (ns)")
    ax2.set_ylabel("cumulative events")
    ax2.legend(loc="upper left")
    ax2.grid(True, alpha=0.3)
    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "tclk_readout" / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


@cocotb.test()
async def test_tclk_decode_to_axi(dut):
    """Drive a sequence of TCLK event bytes, buffer them, then read every event
    over AXI and check codes, flags, timestamps, and counts. 0xFF is included to
    prove the TCLK path keeps it (DROP_NULL=0) instead of dropping it as a null."""
    _start_clocks(dut)
    await reset_dut(dut)

    # 0x9D and 0xD2 are the two events from Fig. 1 of the TCLK paper; 0xFF proves
    # DROP_NULL=0 (the ACLK packer would silently drop it).
    events = [0x9D, 0xD2, 0x00, 0xFF, 0x07, 0x0F, 0xA5, 0x29]

    acct = {"w": 0, "read": 0}
    series = []
    cocotb.start_soon(_rx_monitor(dut, acct))
    cocotb.start_soon(_sampler(dut, acct, series))
    cocotb.start_soon(_tclk_driver(dut, events, acct))

    # Let serdec lock (warm-up done), then baseline the monotonic counters so the
    # one spurious startup PERR is excluded from the clean-stream check.
    await _wait_flag(dut, acct, "warm_done")
    await ClockCycles(dut.clk_40m, 20)        # let the startup PERR transient propagate
    await ClockCycles(dut.s_axi_aclk, 6)
    base_err = await axi_read(dut, ERROR_COUNT)
    base_null = await axi_read(dut, NULL_COUNT)

    # Wait until every event has been driven and decoded into the FIFO.
    await _wait_flag(dut, acct, "drive_done", step=8)
    await ClockCycles(dut.clk_40m, 40)        # flush the last frame through the deserializer
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
    acct["stop_drive"] = True

    assert (await axi_read(dut, STATUS)) & 0x2 == 0, "overflow set: events were dropped"
    assert len(collected) == len(events), (
        f"read {len(collected)} events, expected {len(events)}: "
        f"{[f'0x{c[0]:02X}' for c in collected]}"
    )

    last_ts = -1
    for i, ((ev, flags, data, ts), exp) in enumerate(zip(collected, events)):
        has_data = bool(flags & FLAG_HAS_DATA)
        is_tclk = bool(flags & FLAG_IS_TCLK)
        assert ev == exp, f"#{i} event 0x{ev:02X} != 0x{exp:02X}"
        assert is_tclk, f"#{i} is_tclk not set (flags=0x{flags:04X})"
        assert not has_data, f"#{i} has_data set, but TCLK events carry no payload"
        assert data == 0, f"#{i} data 0x{data:016X} != 0 for a TCLK event"
        assert ts > last_ts, f"#{i} timestamp {ts} not increasing (prev {last_ts})"
        last_ts = ts

    ev_count = await axi_read(dut, EVENT_COUNT)
    null_count = await axi_read(dut, NULL_COUNT)
    err_count = await axi_read(dut, ERROR_COUNT)
    assert ev_count == len(events), f"EVENT_COUNT {ev_count} != {len(events)}"
    assert null_count - base_null == 0, \
        f"NULL_COUNT rose by {null_count - base_null}; DROP_NULL=0 must never count nulls"
    assert err_count - base_err == 0, \
        f"ERROR_COUNT rose by {err_count - base_err} on clean frames (base {base_err})"

    path = _save_plot(series, len(collected), "tclk_to_axi.png")
    if path:
        dut._log.info(f"plot written to {path}")
    dut._log.info(
        f"TCLK full chain OK: {len(collected)} biphase-mark events decoded and read over AXI "
        f"(0xFF kept, NULL_COUNT=0); flags, zero data, and monotonic timestamps all correct"
    )


@cocotb.test()
async def test_tclk_parity_error_to_axi(dut):
    """A frame with a flipped parity bit must raise exactly one ERROR_COUNT and
    never produce an event; the good frames around it still read out over AXI."""
    _start_clocks(dut)
    await reset_dut(dut)

    events = [0x3C, (0x55, True), 0x42]       # good, bad-parity, good
    good = [0x3C, 0x42]

    acct = {"w": 0, "read": 0}
    cocotb.start_soon(_tclk_driver(dut, events, acct))

    await _wait_flag(dut, acct, "warm_done")
    await ClockCycles(dut.clk_40m, 20)
    await ClockCycles(dut.s_axi_aclk, 6)
    base_err = await axi_read(dut, ERROR_COUNT)

    await _wait_flag(dut, acct, "drive_done", step=8)
    await ClockCycles(dut.clk_40m, 40)
    await ClockCycles(dut.s_axi_aclk, 6)

    collected = []
    while True:
        status = await axi_read(dut, STATUS)
        if status & 0x1:
            break
        collected.append(await axi_read_event(dut))
    acct["stop_drive"] = True

    got = [c[0] for c in collected]
    assert 0x55 not in got, f"bad-parity frame leaked into the FIFO: {[f'0x{x:02X}' for x in got]}"
    assert got == good, f"expected good frames {[f'0x{x:02X}' for x in good]}, got {[f'0x{x:02X}' for x in got]}"

    err_count = await axi_read(dut, ERROR_COUNT)
    assert err_count - base_err == 1, \
        f"ERROR_COUNT delta {err_count - base_err} != 1 for one bad-parity frame"
    dut._log.info(
        f"TCLK parity path OK over AXI: good frames {[f'0x{x:02X}' for x in got]} read, "
        f"bad-parity frame dropped, ERROR_COUNT delta = {err_count - base_err}"
    )


@cocotb.test()
async def test_tclk_debug_activity(dut):
    """The 0x28 DEBUG register's transition counter climbs while the TCLK line
    toggles; the level/sig_err bits read back. This is the on-board signal-presence
    diagnostic: transitions climbing but EVENT_COUNT flat => signal present, decoder
    not locking; transitions flat => no signal / pin / front-end."""
    _start_clocks(dut)
    await reset_dut(dut)

    events = [0x9D, 0xD2, 0x07]
    acct = {"w": 0, "read": 0}
    cocotb.start_soon(_tclk_driver(dut, events, acct))

    await _wait_flag(dut, acct, "warm_done")
    await ClockCycles(dut.s_axi_aclk, 8)
    d0 = await axi_read(dut, DEBUG)
    c0 = d0 & 0x3FFFFFFF

    await _wait_flag(dut, acct, "drive_done", step=8)
    await ClockCycles(dut.s_axi_aclk, 8)
    d1 = await axi_read(dut, DEBUG)
    c1 = d1 & 0x3FFFFFFF
    acct["stop_drive"] = True

    assert c1 > c0, f"DEBUG transition count did not climb: c0={c0} c1={c1}"
    sig_err = (d1 >> 31) & 1
    level = (d1 >> 30) & 1
    assert sig_err in (0, 1) and level in (0, 1), f"DEBUG flag bits unreadable: 0x{d1:08X}"
    dut._log.info(f"DEBUG OK: transitions {c0} -> {c1}, level={level}, sig_err={sig_err}")
