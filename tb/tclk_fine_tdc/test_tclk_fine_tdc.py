import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

PERIOD_PS = 5000          # 200 MHz
PHASE_PS = PERIOD_PS // 4 # 1.25 ns


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
