"""Unit tests for plot_pps_log (no display; Agg only)."""
import numpy as np

from plot_pps_log import parse_log, sliding_ppm

SAMPLE = """\
#      UTC              COUNT  MISS  REJ   HW-sys (s)    ppm(60s)  ppm(run)
# 23:59:58     91272     0  223363     +0.004005         -         -
# 23:59:59     91273     0  223363     +0.004004    -0.044    -0.208
# 00:00:00     91274     0  223364     +0.004004    -0.036    -0.206
# 00:00:01     91275     0  223364      UNSYNC (PPS DEAD)
#      UTC              COUNT  MISS  REJ   HW-sys (s)    ppm(60s)  ppm(run)
# 00:10:00     91290     0  223364    -15.999500    -0.061    -0.210
# stopped: count 91290, missing 0, reject 223364
"""


def test_parse_log_unwraps_midnight_and_skips_unsync():
    r = parse_log(SAMPLE.splitlines())
    # 4 numeric samples survive (UNSYNC, headers, stopped dropped)
    assert len(r.t) == 4
    # midnight rollover: 00:00:00 lands AFTER 23:59:59, not 86399 s before
    assert r.t[2] - r.t[1] == 1.0
    assert r.t[3] - r.t[2] == 600.0
    assert r.rej[-1] == 223364
    # unwrapped phase rides through the -16 s relabel: fraction continuity
    # (.004004 -> .0005 within wrap_half of each other)
    assert abs((r.phase_us[3] - r.phase_us[2]) - (-3504.0)) < 1.0


def test_sliding_ppm_recovers_ramp():
    t = np.arange(0.0, 1200.0)
    phase_us = 0.22 * t                    # +0.22 ppm ramp
    p = sliding_ppm(t, phase_us, window_s=300.0)
    mid = p[len(p) // 2:]
    mid = mid[~np.isnan(mid)]
    assert np.allclose(mid, 0.22, atol=0.001)


def test_gap_break_cuts_on_seconds_even_when_plotting_hours():
    from plot_pps_log import _gap_break
    t_s = np.array([0.0, 60.0, 2800.0])          # a long hole after sample 2
    hours = t_s / 3600.0
    x, y = _gap_break(t_s, hours, np.array([1.0, 2.0, 3.0]))
    assert len(x) == 4 and np.isnan(y[2])        # NaN vertex inside the hole


def test_render_smoke(tmp_path):
    import matplotlib
    matplotlib.use("Agg")
    from plot_pps_log import render
    r = parse_log(SAMPLE.splitlines())
    out = tmp_path / "pps.png"
    render(r, str(out), window_s=60.0)
    assert out.exists() and out.stat().st_size > 10_000
