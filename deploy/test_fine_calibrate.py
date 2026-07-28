import numpy as np
from fine_calibrate import calibrate_bins, apply, refine


def test_uniform_bins_recover_even_spacing():
    rng = np.random.default_rng(0)
    # asynchronous edge: fine_phase uniform over 4 equal bins
    fp = rng.integers(0, 4, size=200_000)
    off = calibrate_bins(fp, n_bins=4, period_ns=5.0)
    # equal bins -> centers at 0.625, 1.875, 3.125, 4.375 ns
    assert np.allclose(off, [0.625, 1.875, 3.125, 4.375], atol=0.05)


def test_nonuniform_bins_recover_widths():
    # bin 0 twice as wide as the others (occupancy 2:1:1:1)
    fp = np.concatenate([np.zeros(4000), np.ones(2000), np.full(2000, 2), np.full(2000, 3)]).astype(int)
    off = calibrate_bins(fp, n_bins=4, period_ns=5.0)
    assert off[0] < off[1] < off[2] < off[3]
    assert abs(off[0] - 1.0) < 0.1          # wide first bin -> center ~1.0 ns


def test_apply_refines_within_coarse_tick():
    off = np.array([0.625, 1.875, 3.125, 4.375])
    coarse = np.array([100.0, 100.0])
    ref = apply(coarse, np.array([0, 3]), off)
    assert ref[1] - ref[0] > 3.0            # bin 3 later than bin 0 within the tick


def test_refine_applies_only_when_valid():
    off = np.array([0.625, 1.875, 3.125, 4.375])
    coarse = np.array([100.0, 100.0, 100.0])
    fp = np.array([0, 3, 2])
    fv = np.array([1, 1, 0])                      # 3rd event: fine invalid -> fallback
    out = refine(coarse, fp, fv, off)
    assert np.isclose(out[0], 100.625)
    assert np.isclose(out[1], 104.375)
    assert np.isclose(out[2], 100.0)             # unchanged: coarse only


def test_refine_tightens_periodic_spacing():
    # a periodic marker: true period exactly 5000.0 ns; coarse is quantized to 5 ns,
    # fine recovers the sub-tick, so refined spacing std < coarse spacing std.
    n = 500
    true_t = np.arange(n) * 5000.37             # sub-tick drift
    coarse = np.floor(true_t / 5.0) * 5.0        # 5 ns quantized (floor, not round; fine_phase uses floor convention)
    fp = ((true_t % 5.0) / 1.25).astype(int) % 4
    off = calibrate_bins(fp, n_bins=4, period_ns=5.0)
    ref = refine(coarse, fp, np.ones(n, int), off)
    assert np.std(np.diff(ref)) < np.std(np.diff(coarse))


def test_refine_boundary_bin_no_wrap_is_correct():
    # Faithful model of the RTL boundary-bin latch (proven against real RTL in
    # tb/tclk_fine_tdc/test_tclk_fine_tdc.py::test_bin0_coarse_reconstruction):
    # the RTL sweep emits phases in order 1,2,3,0 as the sub-tick offset grows,
    # and the boundary quarter (fine_phase==0) latches its coarse EXACTLY ONE tick
    # (period) higher than bins 1-3. calibrate_bins hands bin 0 the smallest
    # in-tick offset, so refine()'s `+` reconstructs it correctly with NO wrap.
    # A period-wrap on bin 0 double-counts the tick and REGRESSES the jitter; this
    # test is the regression guard that keeps such a wrap out of the calibration.
    rng = np.random.default_rng(3)
    n = 40_000
    period = 5.0
    true_t = np.arange(n) * (5000.0 + 1.7) + rng.normal(0, 0.12, n)   # marker w/ sub-tick walk
    tick = np.floor(true_t / period)
    phi = true_t - tick * period                                      # ns in [0, period)
    phase = np.where(phi < 1.25, 1, np.where(phi < 2.5, 2, np.where(phi < 3.75, 3, 0)))
    dcoarse = np.where(phase == 0, 1.0, 0.0)                          # boundary bin: +1 tick
    coarse = (tick + dcoarse) * period
    off = calibrate_bins(phase, n_bins=4, period_ns=period)

    ref_nowrap = refine(coarse, phase, np.ones(n, int), off)
    wrapped = off.copy()
    wrapped[0] -= period                                             # the tempting-but-wrong wrap
    ref_wrap = refine(coarse, phase, np.ones(n, int), wrapped)

    # current `+` convention (no wrap) tightens jitter well below the coarse spacing
    assert np.std(np.diff(ref_nowrap)) < np.std(np.diff(coarse))
    # ...and reconstructs the true edge times to within a bin half-width
    resid = ref_nowrap - true_t
    resid -= np.median(resid)                                        # remove the fixed pipeline latency
    assert np.max(np.abs(resid)) < period / 4                        # < 1.25 ns
    # a bin-0 period-wrap makes it strictly WORSE (double-counts the +1 tick)
    assert np.std(np.diff(ref_wrap)) > np.std(np.diff(ref_nowrap))
