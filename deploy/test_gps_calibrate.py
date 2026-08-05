import numpy as np
import pytest

from gps_calibrate import (NS, calibrate, calibrate_windowed, load_marker_ns,
                           marker_index, split_sessions, to_true_ns,
                           to_true_ns_tracked)


def _ruler(n, ppm, jitter_ns=0.0, seed=0):
    """Marker stamps from a timebase running `ppm` fast/slow vs the true 1 Hz."""
    rng = np.random.default_rng(seed)
    hw_per_marker = NS * (1.0 + ppm * 1e-6)
    t = np.arange(n) * hw_per_marker
    if jitter_ns:
        t = t + rng.normal(0.0, jitter_ns, n)
    return t


def test_marker_index_counts_dropped_markers():
    t = _ruler(20, 0.0)
    keep = np.ones(20, bool)
    keep[[5, 6, 12]] = False                 # 2 adjacent + 1 isolated dropout
    k = marker_index(t[keep], NS)
    assert k[0] == 0
    assert k[-1] == 19                       # index still spans the true 20 seconds
    assert len(k) == keep.sum()


def test_recovers_a_known_frequency_offset():
    cal = calibrate(_ruler(10_000, -3.48085))
    assert cal.ppm == pytest.approx(-3.48085, abs=1e-6)
    assert cal.n == 10_000
    assert cal.dropouts == 0


def test_missing_markers_do_not_bias_the_fit():
    t = _ruler(5_000, 2.0)
    keep = np.ones(5_000, bool)
    keep[1000:1003] = False                  # a 4 s hole
    cal = calibrate(t[keep])
    assert cal.ppm == pytest.approx(2.0, abs=1e-6)
    assert cal.dropouts == 1                 # one interval that was not 1 marker


def test_jitter_widens_the_residual_without_biasing_the_slope():
    cal = calibrate(_ruler(20_000, -3.48, jitter_ns=50.0, seed=1))
    assert cal.ppm == pytest.approx(-3.48, abs=0.01)
    assert 40.0 < cal.resid_sd < 60.0        # residual reports the input jitter


def test_a_displaced_marker_is_rejected_not_fitted():
    t = _ruler(4_000, -3.5)
    t[2000] += 5_000_000.0                   # one marker 5 ms late
    cal = calibrate(t)
    assert cal.ppm == pytest.approx(-3.5, abs=1e-6)
    assert cal.rejected >= 1
    assert cal.resid_sd < 1.0                # the outlier is out of the residual


def test_to_true_ns_undoes_the_scale():
    ppm = -3.48085
    true = np.arange(1_000, dtype=float) * float(NS)
    hw = true * (1.0 + ppm * 1e-6)
    assert np.allclose(to_true_ns(hw, ppm, t0_ns=0.0), true, atol=1e-3)


def test_to_true_ns_is_a_duration_correction_from_t0():
    ppm = -3.5
    hw = np.array([1.785e18, 1.785e18 + 1e9])
    out = to_true_ns(hw, ppm)
    assert out[0] == 0.0                     # relative to the first stamp
    # exact division, not the first-order (1 + ppm) approximation: they differ by
    # ppm^2 * 1e9 = 0.012 ns here, and the exact form is what the module applies.
    assert out[1] == pytest.approx(1e9 / (1.0 - 3.5e-6), rel=1e-12)


def test_windowed_calibration_tracks_a_drifting_offset():
    n = 40_000
    k = np.arange(n)
    inst = -3.5e-6 + 1.0e-6 * (k / n)        # ramps -3.5 -> -2.5 ppm
    hw = np.concatenate([[0.0], np.cumsum(NS * (1.0 + inst[1:]))])
    wins = calibrate_windowed(hw, window_s=5_000.0, min_markers=500)
    assert len(wins) >= 6
    ppms = np.array([w.ppm for w in wins])
    assert ppms[0] < ppms[-1]                # tracks the ramp
    assert ppms[0] == pytest.approx(-3.5, abs=0.1)
    assert ppms[-1] == pytest.approx(-2.5, abs=0.1)


def test_tracked_correction_beats_a_single_scale_under_wander():
    n = 40_000
    k = np.arange(n)
    inst = -3.5e-6 + 0.05e-6 * np.sin(2 * np.pi * k / n)
    hw = np.concatenate([[0.0], np.cumsum(NS * (1.0 + inst[1:]))])
    true = k.astype(float) * NS

    cal = calibrate(hw)
    err_global = np.abs(to_true_ns(hw, cal.ppm, t0_ns=hw[0]) - true).max()
    err_tracked = np.abs(to_true_ns_tracked(hw, hw, smooth_markers=2_000) - true).max()
    assert err_tracked < 0.25 * err_global


def test_calibrate_rejects_a_too_short_series():
    with pytest.raises(ValueError):
        calibrate(_ruler(3, 0.0))


def test_split_sessions_isolates_the_stale_fifo_block():
    """Real captures open with ~512 events drained from a FIFO that filled hours
    earlier, so the marker series starts with a small block then a huge gap."""
    stale = _ruler(6, -3.5)                              # the pre-capture block
    live = _ruler(20_000, -3.5) + 16_566.0 * NS          # the run itself
    sessions = split_sessions(np.concatenate([stale, live]), max_gap_s=60.0)
    assert len(sessions) == 2
    assert sessions[0].size == 6
    assert sessions[1].size == 20_000


def test_stale_block_does_not_corrupt_the_windowed_stability():
    """A window straddling the stale-block gap would report a wild ppm; after
    splitting, every window sees the true offset."""
    stale = _ruler(6, -3.5)
    live = _ruler(20_000, -3.5, jitter_ns=50.0, seed=2) + 16_566.0 * NS
    longest = max(split_sessions(np.concatenate([stale, live])),
                  key=lambda s: s[-1] - s[0])
    wins = calibrate_windowed(longest, window_s=3600.0)
    ppms = np.array([w.ppm for w in wins])
    assert len(wins) >= 5
    assert np.ptp(ppms) < 0.05                    # no straddling-window outlier
    assert ppms.mean() == pytest.approx(-3.5, abs=0.01)


def test_loader_survives_a_corruption_burst(tmp_path):
    """A real capture came back with a block scrambled in place, which puts
    non-UTF8 bytes mid-file. The loader must skip those rows, not die."""
    p = tmp_path / "flags-corrupt.csv"
    good = ["0,1785441088,100,143,1,1",
            "1,1785441089,120,143,2,1",
            "2,1785441090,140,143,0,1"]
    with open(p, "wb") as f:
        f.write(b"id,sec,ns,event,fine_phase,fine_valid\n")
        f.write((good[0] + "\n").encode())
        f.write(b"3,17854\x81\x9d088,6\x0c4,14\xb03,2,1\n")   # scrambled row
        f.write(("\n".join(good[1:]) + "\n").encode())
        f.write(b"5,0,0,143,1,1\n")                            # UNSYNC: sec==0
    t, skipped = load_marker_ns([str(p)], 0x8F)
    assert len(t) == 3                     # 3 good markers, corrupt + unsync excluded
    assert skipped == 1
    assert t[0] == 1785441088 * NS + 100
