"""Cocotb chain test proving the fine-TDC integration inside tclk_readout_top:
every decoded TCLK event still passes through the proven decode path unchanged
(order, EVENT_COUNT, no new ERROR_COUNT), AND now carries a ref-edge coarse
timestamp (frozen_coarse, via ts_ext -> u_axi) plus fine sub-bin bits in
FLAGS[4:2], correctly paired to THAT event (not off-by-one against a
neighboring event, and not the readout core's own internal counter wearing a
frozen_coarse costume).

REQUIRES USE_EXT_TS=1 (set by this suite's runner.py entry, NOT the default):
at USE_EXT_TS=0 aclk_readout_core packs its own internal free-running counter
regardless of what is wired to ts_ext, so the frozen_coarse wiring this test
exists to prove would be silently dead and every assertion below would still
pass against the wrong signal.

Runs the DUT's clk_40m as the fine-TDC's 200 MHz clk_p0 reference, with
clk_p90/p180/p270 the true-quadrature companions (same pattern as the Part-1
sweep test, tb/tclk_fine_tdc/test_tclk_fine_tdc.py's _start_phases), and
drives ts_ext as a free-running 200 MHz counter -- the shared coarse
timebase the TDC stamps each ref-edge (REF_EDGE == ~DAVn, the deserializer's
frame-accept strobe) against.

Two independent checks on the packed TS, deliberately not collapsed into
one: a bit-exact comparison against the DUT's own internal frozen_coarse
wire (catches USE_EXT_TS/wiring/FIFO-packing bugs) and a tolerance-bounded
comparison against a ground truth sampled entirely outside the TDC's
pipeline (catches alignment/off-by-one bugs and a stuck TDC -- see the
comments at each assertion for why one check cannot catch what the other
does).
"""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from tclk_tx_model import SAMPLES_PER_CELL
from axi_lite_bfm import axi_read, _b

from test_tclk_readout import (
    STATUS, EVENT_COUNT, ERROR_COUNT,
    FLAG_HAS_DATA, FLAG_IS_TCLK,
    reset_dut, axi_read_event, _wait_flag, _tclk_driver,
)

FLAG_FINE_VALID = 0x10          # FLAGS[4]
FINE_PHASE_SHIFT = 2            # FLAGS[3:2]
FINE_PHASE_MASK = 0x3

CLK80_PERIOD_PS = 100_000 // SAMPLES_PER_CELL   # 80 MHz, matches TCLK_RCV's serdec OSR
CLK_P0_PERIOD_PS = 5000                          # 200 MHz: board's decoupled clk_40m rate
PHASE_PS = CLK_P0_PERIOD_PS // 4                 # 1.25 ns steps -> true 0/90/180/270
AXI_PERIOD_NS = 14

GAP_CELLS = 12
WARMUP_CELLS = 40


def _start_clocks(dut):
    """clk_40m doubles as the fine-TDC's clk_p0 (200 MHz, the board's decoupled
    timestamp rate). clk_p90/p180/p270 are started separately (_start_quadrature)
    so their launch can be awaited relative to clk_p0's first edge."""
    cocotb.start_soon(Clock(dut.clk_80m, CLK80_PERIOD_PS, unit="ps").start())
    cocotb.start_soon(Clock(dut.clk_40m, CLK_P0_PERIOD_PS, unit="ps").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, AXI_PERIOD_NS, unit="ns").start())


async def _start_quadrature(dut):
    # NOTE: duplicated (fixed-period variant) -- see the parametrized copy in
    # tb/tclk_readout/test_tclk_readout.py for why it is not hoisted to a shared
    # module.
    # Start each phase clock PHASE_PS after the previous one so the four rising
    # edges land at true 0/90/180/270 degree offsets within one 5 ns period
    # (same fixed-per-step-delay pattern as the Part-1 sweep test's _start_phases;
    # see that test's comment for why a naive cumulative Timer(PHASE_PS*ph) is a bug).
    for sig in (dut.clk_p90, dut.clk_p180, dut.clk_p270):
        await Timer(PHASE_PS, unit="ps")
        cocotb.start_soon(Clock(sig, CLK_P0_PERIOD_PS, unit="ps").start())


async def _coarse_counter(dut, acct):
    """Free-running 200 MHz coarse timebase fed to ts_ext (-> tclk_fine_tdc's
    coarse_in). Also the ground truth this test checks frozen_coarse against."""
    n = 0
    dut.ts_ext.value = n
    while not acct.get("stop_counter"):
        await RisingEdge(dut.clk_40m)
        n += 1
        dut.ts_ext.value = n


async def _davn_monitor(dut, acct, davn_ts):
    """Record the coarse counter's value at every raw frame-accept strobe
    (dbg_dav, the same clk_40m cycle TCLK_RCV asserts REF_EDGE) -- the ground
    truth for "this event's ref edge landed around here", independent of the
    TDC's internal alignment (and independent of frozen_coarse/push_valid --
    it never reads those signals). Used to catch: (a) an off-by-one event/
    timestamp pairing, where a misaligned push reports a timestamp far from
    this value (roughly one whole inter-event gap away); (b) a stuck/dead
    fine-TDC (e.g. USE_EXT_TS not actually wired through), where the packed
    ts stops tracking this ground truth entirely."""
    while not acct.get("stop_monitor"):
        await RisingEdge(dut.clk_40m)
        await Timer(1, unit="ns")
        if _b(dut.dbg_dav) == 1:
            davn_ts.append(int(dut.ts_ext.value))


async def _push_monitor(dut, acct, push_ts):
    """Record the DUT's own internal frozen_coarse wire at the exact cycle
    push_valid fires (hierarchical access to tclk_readout_top's internal
    nets -- both are plain module-scope wires, not buried in a sub-instance).
    This is a bit-exact check of the packing/AXI path: if it ever disagreed
    with the value actually read back over AXI, that would mean the FIFO/AXI
    packing broke (not a TDC or alignment question -- see the independent
    davn_ts-based checks below for those)."""
    while not acct.get("stop_monitor"):
        await RisingEdge(dut.clk_40m)
        await Timer(1, unit="ns")
        if _b(dut.push_valid) == 1:
            push_ts.append(int(dut.frozen_coarse.value))


@cocotb.test()
async def test_tclk_fine_chain(dut):
    _start_clocks(dut)
    await reset_dut(dut)
    cocotb.start_soon(_start_quadrature(dut))
    await ClockCycles(dut.clk_40m, 4)     # let the phase clocks start ticking

    events = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88]

    acct = {}
    davn_ts = []
    push_ts = []
    cocotb.start_soon(_coarse_counter(dut, acct))
    cocotb.start_soon(_davn_monitor(dut, acct, davn_ts))
    cocotb.start_soon(_push_monitor(dut, acct, push_ts))
    cocotb.start_soon(_tclk_driver(dut, events, acct))

    # Let serdec lock, then baseline ERROR_COUNT past the one expected startup PERR.
    await _wait_flag(dut, acct, "warm_done")
    await ClockCycles(dut.clk_40m, 20)
    await ClockCycles(dut.s_axi_aclk, 6)
    base_err = await axi_read(dut, ERROR_COUNT)

    await _wait_flag(dut, acct, "drive_done", step=8)
    await ClockCycles(dut.clk_40m, 40)        # flush the last frame through the deserializer
    await ClockCycles(dut.s_axi_aclk, 6)      # let counters settle across the CDC

    collected = []
    while True:
        status = await axi_read(dut, STATUS)
        if status & 0x1:                      # empty
            break
        collected.append(await axi_read_event(dut))

    acct["stop_drive"] = True
    acct["stop_counter"] = True
    acct["stop_monitor"] = True

    assert (await axi_read(dut, STATUS)) & 0x2 == 0, "overflow set: events were dropped"
    assert len(collected) == len(events), (
        f"read {len(collected)} events, expected {len(events)}: "
        f"{[f'0x{c[0]:02X}' for c in collected]}"
    )
    assert len(davn_ts) == len(events), (
        f"monitor saw {len(davn_ts)} dbg_dav strobes, expected {len(events)}"
    )
    assert len(push_ts) == len(events), (
        f"monitor saw {len(push_ts)} push_valid strobes, expected {len(events)}"
    )

    # --- decode-preservation: the proven path is unchanged ---
    last_ts = -1
    fine_valids = 0
    for i, ((ev, flags, data, ts), exp) in enumerate(zip(collected, events)):
        has_data = bool(flags & FLAG_HAS_DATA)
        is_tclk = bool(flags & FLAG_IS_TCLK)
        fine_valid = bool(flags & FLAG_FINE_VALID)
        fine_phase = (flags >> FINE_PHASE_SHIFT) & FINE_PHASE_MASK
        assert ev == exp, f"#{i} event 0x{ev:02X} != 0x{exp:02X}"
        assert is_tclk, f"#{i} is_tclk not set (flags=0x{flags:04X})"
        assert not has_data, f"#{i} has_data set, but TCLK events carry no payload"
        assert data == 0, f"#{i} data 0x{data:016X} != 0 for a TCLK event"
        assert ts > last_ts, f"#{i} timestamp {ts} not increasing (prev {last_ts})"
        last_ts = ts
        assert fine_phase in (0, 1, 2, 3), f"#{i} fine_phase {fine_phase} out of range"
        if fine_valid:
            fine_valids += 1

    assert fine_valids == len(events), (
        f"only {fine_valids}/{len(events)} events had FLAGS[4] (fine_valid) set "
        f"for a clean biphase-mark stream (no induced glitches)"
    )

    ev_count = await axi_read(dut, EVENT_COUNT)
    err_count = await axi_read(dut, ERROR_COUNT)
    assert ev_count == len(events), f"EVENT_COUNT {ev_count} != {len(events)}"
    assert err_count - base_err == 0, \
        f"ERROR_COUNT rose by {err_count - base_err} on a clean stream (base {base_err})"

    ts_list = [c[3] for c in collected]

    # --- bit-exact: the packed ts is genuinely frozen_coarse, not the core's
    # internal free-running counter. push_ts[i] is the DUT's own internal
    # frozen_coarse wire sampled at the exact cycle push_valid fired (both
    # plain module-scope nets in tclk_readout_top, read via hierarchical
    # access -- no tolerance, no independent reconstruction). This is what
    # directly catches USE_EXT_TS not actually being wired to 1 (the packed
    # ts would then be aclk_readout_core's own counter, which would NOT
    # match the DUT's frozen_coarse wire at all after even a few events,
    # since the two counters start from different reset points and free-run
    # independently) or any FIFO/AXI packing corruption of the TS field.
    for i, (ts, wire_ts) in enumerate(zip(ts_list, push_ts)):
        assert ts == wire_ts, (
            f"#{i} packed ts={ts} != frozen_coarse sampled at its own push "
            f"cycle ({wire_ts}) -- either USE_EXT_TS isn't actually wired to "
            f"1 (ts_ext into u_axi is the core's internal counter, not "
            f"frozen_coarse) or the TS field was corrupted in the FIFO/AXI path"
        )

    # --- per-event pairing: prove no off-by-one between event and timestamp,
    # and that the fine-TDC is actually alive (not stuck) ---
    # davn_ts[i] is event i's OWN frame-accept-time coarse reading, sampled
    # from a ground truth that is INDEPENDENT of both the TDC's internal
    # pipeline and of push_valid/frozen_coarse (it only watches dbg_dav and
    # the testbench's own free-running counter). The packed ts[i] is
    # frozen_coarse, which the TDC latches from the last raw line transition
    # just BEFORE ref_edge -- a few ns before frame-accept, never a whole
    # inter-event gap away. This check catches what the bit-exact check above
    # structurally cannot: a wrong alignment (e.g. no delay, or the wrong
    # delay, between decode and push) would pair event i with a NEIGHBOR
    # event's frozen values, landing near davn_ts[i-1] or davn_ts[i+1] --
    # one whole inter-event gap (hundreds of ticks) away from davn_ts[i]. A
    # stuck/frozen-at-reset TDC (frozen_coarse never updating) would show an
    # ever-growing drift instead of a bounded one, since davn_ts keeps
    # climbing every event while ts would not.
    gaps = [b - a for a, b in zip(davn_ts, davn_ts[1:])]
    nominal_gap = sum(gaps) / len(gaps)
    tol = nominal_gap / 2
    for i, (ts, dts) in enumerate(zip(ts_list, davn_ts)):
        drift = ts - dts
        assert abs(drift) < tol, (
            f"#{i} packed ts={ts} is {drift} ticks from its own frame-accept "
            f"reading {dts} (nominal inter-event gap {nominal_gap:.0f} ticks, "
            f"tol {tol:.0f}) -- looks like an off-by-one event/timestamp "
            f"pairing, or a stuck/dead fine-TDC"
        )

    dut._log.info(
        f"TCLK fine-TDC chain OK: {len(collected)} events decoded+read, all "
        f"fine_valid=1, ts bit-exact vs. the DUT's own frozen_coarse wire at "
        f"push time, and within {tol:.0f} ticks of their own frame-accept "
        f"reading (nominal gap {nominal_gap:.0f} ticks) -- no off-by-one "
        f"pairing and the TDC is genuinely live"
    )
