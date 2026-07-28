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
    dut.line.value = 0
    dut.rstn.value = 0
    await _start_phases(dut)
    await ClockCycles(dut.clk_p0, 5)
    dut.rstn.value = 1
    await ClockCycles(dut.clk_p0, 5)

    # Align to a clk_p0 rising edge, then place a rising line edge at a controlled
    # sub-period offset; the reported fine_phase must be non-decreasing across the
    # offset sweep (finer than one 12.5 ns decode sample).
    seen = []
    for off_ps in range(200, PERIOD_PS, 400):
        dut.line.value = 0
        await RisingEdge(dut.clk_p0)
        await Timer(off_ps, unit="ps")
        dut.line.value = 1                     # the sub-sample edge
        # wait for the edge to propagate through the synchronizers + decode
        await ClockCycles(dut.clk_p0, 4)
        if int(dut.fine_valid.value):
            seen.append((off_ps, int(dut.fine_phase.value)))
        await ClockCycles(dut.clk_p0, 2)

    phases = [p for _, p in seen]
    assert len(seen) >= 3, f"too few valid captures: {seen}"
    assert phases == sorted(phases), f"fine_phase not monotone across offset sweep: {seen}"
    assert phases[0] != phases[-1], f"fine_phase did not resolve sub-sample motion: {seen}"


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
