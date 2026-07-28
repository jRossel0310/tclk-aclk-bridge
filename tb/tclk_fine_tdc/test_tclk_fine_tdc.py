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
