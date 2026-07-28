import numpy as np
from fine_calibrate import calibrate_bins, apply


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
