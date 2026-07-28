import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

# deploy/ holds the code-density calibration (fine_calibrate.py) exercised by the
# bin-0 characterization test below.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy"))
from fine_calibrate import calibrate_bins

PERIOD_PS = 5000          # 200 MHz
PHASE_PS = PERIOD_PS // 4 # 1.25 ns
PERIOD_NS = 5.0           # one coarse tick, ns (WR timebase advances 5 ns / clk_p0)


async def _start_phases(dut):
    # Start each phase clock PHASE_PS after the previous one so the four rising
    # edges land at true 0/90/180/270 degree offsets (0, 1250, 2500, 3750 ps)
    # within one 5 ns period. (A naive `Timer(PHASE_PS * ph)` per iteration is
    # a cumulative-delay bug: awaits stack, so ph=2/3 land at 2*1250+1250=3750
    # and 3*1250+3750=7500 instead of 2500/3750 - swapping p180 and p270's
    # effective sample order. Fixed here to a constant per-step delay.)
    cocotb.start_soon(Clock(dut.clk_p0, PERIOD_PS, unit="ps").start())
    for sig in (dut.clk_p90, dut.clk_p180, dut.clk_p270):
        await Timer(PHASE_PS, unit="ps")
        cocotb.start_soon(Clock(sig, PERIOD_PS, unit="ps").start())


@cocotb.test()
async def test_edge_sweep(dut):
    # WINDOW SHIFT (read before touching this test): the decoder's 5-sample
    # window is {previous period's delayed s270 (s270_prev), this period's
    # s0/s90/s180/s270}. That means fine_phase=0 corresponds to an edge landing
    # in the *boundary* quarter -- just after the previous period's s270 sample
    # instant (offset 3750..5000 ps below) -- not to an edge just after s0 as a
    # naive 4-sample decoder would report. Sweeping the offset upward from 0
    # therefore produces bins in the order 1, 2, 3, 0 (NOT 0, 1, 2, 3): the last
    # quarter's edge only resolves in the *next* decode window, one clk_p0 cycle
    # later than the other three quarters, because s270_prev needs one more
    # cycle to catch up to the straddled sample. So the phase sequence WRAPS
    # rather than increasing monotonically -- asserting `phases == sorted(phases)`
    # would be testing a fiction, not the design. The real contract: sweeping a
    # rising edge across one full period must produce all 4 bins {0,1,2,3},
    # each from one contiguous offset sub-range (no bin reappearing after
    # another bin has intervened), and nothing left unresolved -- i.e. the
    # aliased 4th quarter the 5-sample window exists to recover is, in fact,
    # recovered.
    dut.line.value = 0
    dut.rstn.value = 0
    await _start_phases(dut)
    await ClockCycles(dut.clk_p0, 5)
    dut.rstn.value = 1
    await ClockCycles(dut.clk_p0, 5)

    seen = []
    for off_ps in range(200, PERIOD_PS, 200):
        dut.line.value = 0
        # Drain the previous iteration's falling transition (its own decode
        # pulse, symmetric to a rising one) well past the pipeline's ~3-cycle
        # latency before arming the next sweep edge, so hits below are never
        # contaminated by stale pipeline state from the last iteration.
        await ClockCycles(dut.clk_p0, 6)

        await RisingEdge(dut.clk_p0)
        await Timer(off_ps, unit="ps")
        dut.line.value = 1                     # the sub-sample edge

        # Poll several cycles: quarters 0-2 resolve in the decode window that
        # directly covers this period; quarter 3 (the boundary quarter)
        # resolves one window later (see docstring above), so a single
        # fixed-delay check is not enough -- poll and collect every valid hit.
        hits = []
        for _ in range(8):
            await RisingEdge(dut.clk_p0)
            await Timer(1, unit="ns")
            if int(dut.fine_valid.value):
                hits.append(int(dut.fine_phase.value))

        assert len(hits) == 1, f"off={off_ps}: expected exactly one valid decode, got {hits}"
        seen.append((off_ps, hits[0]))

    phases = [p for _, p in seen]

    # Contract 1: all 4 bins are observed (the 4th quarter is no longer lost).
    assert set(phases) == {0, 1, 2, 3}, f"not all 4 bins observed: {sorted(set(phases))}: {seen}"

    # Contract 2: each bin comes from one contiguous run of offsets -- group
    # consecutive equal phases and confirm no bin value reappears in a later,
    # separate run (that would mean interleaving/noise, not a clean sub-bin).
    runs = []
    for _, p in seen:
        if runs and runs[-1][0] == p:
            runs[-1][1] += 1
        else:
            runs.append([p, 1])
    run_bins = [p for p, _ in runs]
    assert len(set(run_bins)) == len(run_bins), f"a bin reappeared non-contiguously: {runs}: {seen}"

    # Contract 3: exactly 4 contiguous runs over the swept range (one per bin;
    # no unresolved / dropped offsets and no extra runs from spurious noise).
    assert len(runs) == 4, f"expected 4 contiguous bin runs, got {len(runs)}: {runs}: {seen}"

    dut._log.info("offset(ps) -> fine_phase runs: %s", [(p, n) for p, n in runs])
    dut._log.info("full sweep: %s", seen)


@cocotb.test()
async def test_bin0_coarse_reconstruction(dut):
    """Characterize the boundary-bin (fine_phase==0) coarse latch and prove the
    reconstruction convention is self-consistent WITHOUT a per-tick "wrap".

    A single rising edge is swept across a full clk_p0 period while a free-running
    coarse counter runs on clk_p0. For each offset we capture the held
    edge_coarse/edge_phase -- the exact triple frozen_coarse/frozen_phase copy on
    ref_edge, i.e. the value the readout packs (USE_EXT_TS=1) and fine_calibrate
    later consumes. From that RTL-measured sweep this pins three facts:

      1. THE +1-TICK DISCONTINUITY IS REAL AND EXACTLY ONE clk_p0 PERIOD. Bins
         1,2,3 latch a constant coarse (the fixed decode-pipeline latency); bin 0
         (the boundary quarter, swept last -- order 1,2,3,0) latches EXACTLY one
         tick higher, because its edge_stb resolves one clk_p0 cycle later
         (s270_prev needs the extra delay; see the module header). RAW coarse is
         therefore NOT continuous across the bin-3 -> bin-0 boundary: two edges
         ~1.25 ns apart get coarse values a full tick apart.

      2. THE CURRENT CONVENTION ALREADY COMPENSATES IT. calibrate_bins() indexes
         offsets by fine_phase and cumulates from bin 0, so it hands the boundary
         bin the SMALLEST in-tick offset (~0.6 ns). Combined with refine()'s `+`
         and bin 0's +1 tick, coarse+offset[phase] is MONOTONIC across the whole
         period in ~1.25 ns steps -- the +1 tick and the small offset cancel. No
         wrap is needed or correct.

      3. A per-tick "wrap" (offset[0] -= period) BREAKS it: it double-subtracts
         the tick that was never spurious and drops bin-0 events ~one period
         early, making the reconstruction NON-monotonic. This test fails if such
         a wrap is ever folded into the calibration path.
    """
    dut.line.value = 0
    dut.rstn.value = 0
    dut.ref_edge.value = 0
    dut.coarse_in.value = 0
    await _start_phases(dut)
    await ClockCycles(dut.clk_p0, 5)
    dut.rstn.value = 1
    await ClockCycles(dut.clk_p0, 5)

    # Free-running coarse counter on clk_p0 (one count == one PERIOD_NS tick),
    # mirroring the WR ns timebase the board feeds into coarse_in.
    async def _coarse_counter():
        n = 0
        while True:
            await RisingEdge(dut.clk_p0)
            n += 1
            dut.coarse_in.value = n
    cocotb.start_soon(_coarse_counter())
    await ClockCycles(dut.clk_p0, 3)

    seen = []  # (off_ps, coarse_delta_ticks, phase)
    for off_ps in range(100, PERIOD_PS, 100):
        dut.line.value = 0
        await ClockCycles(dut.clk_p0, 8)         # drain prior transition; regs settle
        await RisingEdge(dut.clk_p0)
        await Timer(1, unit="ps")
        c_align = int(dut.coarse_in.value)       # counter at this tick boundary
        await Timer(off_ps, unit="ps")
        dut.line.value = 1                        # the sub-sample edge
        await ClockCycles(dut.clk_p0, 8)          # let it reach edge_coarse/edge_phase
        await Timer(1, unit="ps")
        assert int(dut.edge_valid.value) == 1, f"off={off_ps}: edge_valid=0 (no clean decode)"
        seen.append((off_ps, int(dut.edge_coarse.value) - c_align, int(dut.edge_phase.value)))

    dut._log.info("bin-0 sweep (off_ps, dcoarse, phase): %s", seen)

    phases = [p for _, _, p in seen]
    assert set(phases) == {0, 1, 2, 3}, f"not all 4 bins swept: {sorted(set(phases))}"

    # Fact 1: bins 1,2,3 share one coarse latency; bin 0 is exactly +1 tick.
    dc_nonboundary = {dc for _, dc, p in seen if p != 0}
    dc_boundary = {dc for _, dc, p in seen if p == 0}
    assert len(dc_nonboundary) == 1, f"bins 1-3 coarse latency not constant: {dc_nonboundary}"
    assert len(dc_boundary) == 1, f"bin 0 coarse latency not constant: {dc_boundary}"
    d_base = dc_nonboundary.pop()
    d_bin0 = dc_boundary.pop()
    disc = d_bin0 - d_base
    assert disc == 1, (
        f"bin-0 coarse discontinuity is {disc} ticks, expected exactly 1 clk_p0 period. "
        f"A wrap sized for 1 tick would be wrong -- STOP and reassess."
    )

    # Code-density offsets from the swept phase histogram (index-order, as the
    # real calibration produces them).
    offsets = calibrate_bins(phases, n_bins=4, period_ns=PERIOD_NS)

    def recon(off_table):
        return [dc * PERIOD_NS + off_table[p] for _, dc, p in seen]

    # Fact 2: current convention (no wrap) reconstructs monotonically. Steps are
    # 0 within a bin (same phase+coarse) and ~one bin-width (1.25 ns) at each bin
    # transition; crucially none go backward, and there is no ~one-period jump.
    r_nowrap = recon(offsets)
    steps = [b - a for a, b in zip(r_nowrap, r_nowrap[1:])]
    assert all(s >= -1e-9 for s in steps), (
        f"current (no-wrap) reconstruction is NOT monotonic across the period: "
        f"steps={[round(s, 3) for s in steps]}; recon={[round(x, 3) for x in r_nowrap]}"
    )
    assert max(steps) < 2.0 * (PERIOD_NS / 4), (
        f"current reconstruction has a >~2-bin gap ({max(steps):.3f} ns): the +1 tick "
        f"is not being compensated as expected"
    )
    assert r_nowrap[-1] - r_nowrap[0] < PERIOD_NS, (
        f"reconstruction spans a full period ({r_nowrap[-1] - r_nowrap[0]:.3f} ns) across a "
        f"single sub-tick sweep -- the boundary bin's +1 tick leaked in uncompensated"
    )

    # Fact 3: subtracting a period from bin 0 double-counts the tick and breaks it.
    wrapped = offsets.copy()
    wrapped[0] -= PERIOD_NS
    r_wrap = recon(wrapped)
    wrap_steps = [b - a for a, b in zip(r_wrap, r_wrap[1:])]
    assert any(s < 0 for s in wrap_steps), (
        f"a bin-0 period-wrap was expected to BREAK monotonicity but did not: "
        f"steps={[round(s, 3) for s in wrap_steps]}. The reconstruction convention may "
        f"have changed -- re-derive before trusting this test."
    )
    dut._log.info(
        "bin-0 OK: discontinuity = +1 tick (exactly one %.1f ns period); no-wrap "
        "reconstruction monotonic (max step %.3f ns); period-wrap breaks it -> "
        "current calibrate/refine convention is correct, NO wrap.",
        PERIOD_NS, max(steps),
    )


@cocotb.test()
async def test_glitch_flagged(dut):
    dut.line.value = 0
    dut.rstn.value = 0
    await _start_phases(dut)
    await ClockCycles(dut.clk_p0, 5)
    dut.rstn.value = 1
    await ClockCycles(dut.clk_p0, 5)

    # A narrow glitch (up then immediately down inside one period) is non-monotone.
    # The pulse must straddle a real sample edge to be seen at all (a transition
    # that falls entirely inside the 1250 ps gap between two adjacent phase-clock
    # edges is invisible to every register in the design - there is no clock edge
    # to capture it, by construction of 4-tap 90-degree quadrature sampling). So
    # bracket the clk_p90 sample instant (offset PHASE_PS from the clk_p0 edge)
    # with a pulse narrower than one phase step: line goes high before p90 samples
    # and low again before the next phase clock (p180) samples, so only the p90
    # tap reads 1 while its neighbors read 0 - a non-monotone thermometer code.
    got_invalid = False
    await RisingEdge(dut.clk_p0)
    await Timer(PHASE_PS - 300, unit="ps")
    dut.line.value = 1
    await Timer(600, unit="ps")                 # shorter than a phase step
    dut.line.value = 0
    for _ in range(6):
        await RisingEdge(dut.clk_p0)
        await Timer(1, unit="ns")
        if int(dut.edge_stb.value) and not int(dut.fine_valid.value):
            got_invalid = True
    assert got_invalid, "glitch did not raise edge_stb with fine_valid=0"


@cocotb.test()
async def test_ref_edge_freeze(dut):
    # Prove frozen_coarse/frozen_phase/frozen_valid latch the LAST CARRIER EDGE
    # seen before ref_edge, not whatever coarse_in happens to read at the
    # ref_edge instant. Drive a free-running coarse_in counter on clk_p0 (the
    # shared coarse timebase), a periodic line edge (as in the sweep test), and
    # pulse ref_edge at an arbitrary point mid-period -- nowhere near the edge
    # itself -- so a design that (incorrectly) samples coarse_in directly at
    # ref_edge instead of holding the last edge_stb's coarse/phase would read a
    # different, later coarse_in value and this test would catch it.
    dut.line.value = 0
    dut.rstn.value = 0
    dut.ref_edge.value = 0
    dut.coarse_in.value = 0
    await _start_phases(dut)
    await ClockCycles(dut.clk_p0, 5)
    dut.rstn.value = 1
    await ClockCycles(dut.clk_p0, 5)

    # Free-running coarse counter, incremented every clk_p0 edge.
    async def _coarse_counter():
        n = 0
        while True:
            await RisingEdge(dut.clk_p0)
            n += 1
            dut.coarse_in.value = n

    cocotb.start_soon(_coarse_counter())
    await ClockCycles(dut.clk_p0, 2)

    async def _capture_next_edge():
        # Poll for the next edge_stb and return the (coarse, phase, valid)
        # tuple the decoder produced for it -- coarse_in read at the exact
        # cycle edge_stb fires, same instant the DUT's edge_coarse register
        # (gated on edge_stb) samples it.
        for _ in range(8):
            await RisingEdge(dut.clk_p0)
            if int(dut.edge_stb.value):
                return int(dut.coarse_in.value), int(dut.fine_phase.value), int(dut.fine_valid.value)
        return None

    # --- First carrier edge: a single rising edge at a fixed sub-sample
    # offset (bin 1, well off the period boundary so it resolves promptly -
    # see sweep test docstring). The line is held at 1 afterward (no further
    # transition) until explicitly noted below, so nothing but this one edge
    # can perturb the "held last carrier edge" registers before the freeze.
    await RisingEdge(dut.clk_p0)
    await Timer(600, unit="ps")
    dut.line.value = 1

    edge1 = await _capture_next_edge()
    assert edge1 is not None, "first carrier edge never raised edge_stb"
    edge1_coarse, edge1_phase, edge1_valid = edge1

    # Let many more clk_p0 cycles pass with the line held steady (no further
    # transition -- no second edge can occur), so ref_edge lands well after
    # edge1's edge_stb, at an arbitrary point mid-stream, immune to exactly
    # when it lands within a carrier period.
    await ClockCycles(dut.clk_p0, 12)
    ref_instant_coarse = int(dut.coarse_in.value)
    assert ref_instant_coarse != edge1_coarse, (
        "test setup broken: coarse_in must have moved on since edge1's edge_stb "
        "for this test to distinguish 'held edge value' from 'sampled-at-ref_edge value'"
    )

    # Pulse ref_edge for one clk_40m (=clk_p0) cycle.
    dut.ref_edge.value = 1
    await RisingEdge(dut.clk_p0)
    dut.ref_edge.value = 0

    # Allow the 2-FF sync + edge-detect latency to settle.
    await ClockCycles(dut.clk_p0, 6)

    assert int(dut.frozen_valid.value) == edge1_valid, "frozen_valid did not match edge1's decode"
    assert int(dut.frozen_phase.value) == edge1_phase, "frozen_phase did not match edge1's decode"
    assert int(dut.frozen_coarse.value) == edge1_coarse, (
        f"frozen_coarse={int(dut.frozen_coarse.value)} != edge1_coarse={edge1_coarse} "
        f"(ref-instant coarse was {ref_instant_coarse}) -- frozen_coarse must be immune "
        "to exactly when ref_edge lands within a carrier period"
    )

    # --- Second carrier edge: the falling transition of the same pulse is
    # itself a real carrier edge (the decoder flags any transition, rising or
    # falling -- see test_glitch_flagged), so it must update the held
    # registers just like a rising edge would.
    await RisingEdge(dut.clk_p0)
    await Timer(600, unit="ps")
    dut.line.value = 0

    edge2 = await _capture_next_edge()
    assert edge2 is not None, "second carrier edge never raised edge_stb"
    edge2_coarse, edge2_phase, edge2_valid = edge2
    assert edge2_coarse != edge1_coarse, "test setup broken: edge2 coarse must differ from edge1"

    # frozen_* must still hold edge1's values -- untouched until the next ref_edge.
    await ClockCycles(dut.clk_p0, 6)
    assert int(dut.frozen_coarse.value) == edge1_coarse, "frozen_coarse drifted before the next ref_edge"

    dut.ref_edge.value = 1
    await RisingEdge(dut.clk_p0)
    dut.ref_edge.value = 0
    await ClockCycles(dut.clk_p0, 6)

    assert int(dut.frozen_valid.value) == edge2_valid, "frozen_valid did not update to edge2"
    assert int(dut.frozen_phase.value) == edge2_phase, "frozen_phase did not update to edge2"
    assert int(dut.frozen_coarse.value) == edge2_coarse, (
        f"frozen_coarse={int(dut.frozen_coarse.value)} != edge2_coarse={edge2_coarse}: "
        "frozen values did not update to the new reference edge"
    )
