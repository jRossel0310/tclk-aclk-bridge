"""Unit tests for supercycle_plot pure helpers (synthetic data, no matplotlib).
Run: python test_supercycle_plot.py   or   pytest deploy -q"""
import csv
import os
import tempfile

import numpy as np

from supercycle_plot import load_events, cycles_from_anchors, assign_offsets


def test_load_events_dedupes_and_sorts():
    with tempfile.TemporaryDirectory() as d:
        p1 = os.path.join(d, "a.csv")
        p2 = os.path.join(d, "b.csv")
        rows1 = [["id", "sec", "ns", "event", "data"],
                 ["2-0", "10", "500", "12", "0"],
                 ["1-0", "10", "100", "0", "0"]]
        rows2 = [["id", "sec", "ns", "event", "data"],
                 ["2-0", "10", "500", "12", "0"],          # duplicate id
                 ["3-0", "11", "0", "24", "0"]]
        for p, rows in ((p1, rows1), (p2, rows2)):
            with open(p, "w", newline="") as f:
                csv.writer(f).writerows(rows)
        t, ev = load_events([p1, p2])
        assert len(t) == 3                                  # dupe dropped
        assert list(ev) == [0, 12, 24]                      # time-sorted
        assert abs(t[0] - 10.0000001) < 1e-9


def _synthetic(n_cycles=10, length=60.0, missing_anchor=5):
    """Anchor every `length` s with anchor #missing_anchor removed (folds two
    cycles into one 2x-length window that must be REJECTED), a bimodal target
    (offset 10 s in even cycles, 20 s in odd), and a 1 Hz ref comb."""
    anchors = [i * length for i in range(n_cycles + 1)]
    del anchors[missing_anchor]
    t, ev = [], []
    for a in anchors:
        t.append(a); ev.append(0x00)
    for i in range(n_cycles):
        t.append(i * length + (10.0 if i % 2 == 0 else 20.0)); ev.append(0x1E)
        for k in range(int(length)):
            t.append(i * length + k + 0.5); ev.append(0x8F)
    o = np.argsort(t, kind="stable")
    return np.asarray(t)[o], np.asarray(ev)[o]


def test_cycles_reject_missed_anchor_window():
    t, ev = _synthetic()
    starts, ends, stats = cycles_from_anchors(t[ev == 0x00])
    assert stats["n_cycles"] == 9                # 10 anchors -> 9 windows
    assert stats["n_rejected"] == 1              # the folded 120 s window
    assert stats["n_kept"] == 8
    assert abs(stats["median_len"] - 60.0) < 1e-9
    assert np.allclose(ends - starts, 60.0)


def test_assign_offsets_masks_and_measures():
    t, ev = _synthetic()
    starts, ends, _ = cycles_from_anchors(t[ev == 0x00])
    mask, row, off = assign_offsets(t, starts, ends)
    tgt = mask & (ev == 0x1E)
    offs = np.sort(np.unique(np.round(off[tgt], 6)))
    assert list(offs) == [10.0, 20.0]            # the two modes survive
    # every kept cycle holds exactly ONE target event, and each row's offset
    # matches the even/odd schedule (10 s / 20 s): pins real attribution,
    # not just index bounds.
    assert np.array_equal(np.bincount(row[tgt], minlength=len(starts)),
                          np.ones(len(starts), dtype=np.int64))
    order = np.argsort(row[tgt], kind="stable")
    assert np.allclose(np.round(off[tgt][order], 6), [10.0, 20.0] * 4)
    # events inside the rejected (folded) window are masked out entirely
    in_rejected = (t >= 4 * 60.0) & (t < 6 * 60.0) & (ev == 0x1E)
    assert not mask[in_rejected].any()


def test_assign_offsets_no_kept_cycles_returns_all_false():
    anchors = np.array([0.0, 10.0, 1000.0])    # wildly uneven: every window rejected
    starts, ends, stats = cycles_from_anchors(anchors)
    assert stats["n_kept"] == 0
    t = np.array([1.0, 500.0])
    mask, row, off = assign_offsets(t, starts, ends)
    assert not mask.any()
    assert len(mask) == len(row) == len(off) == len(t)


def _write_csv(d, t, ev):
    p = os.path.join(d, "events-tclk-x.csv")
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "sec", "ns", "event", "data"])
        for i, (tt, e) in enumerate(zip(t, ev)):
            sec = int(tt)
            ns = int(round((tt - sec) * 1e9))
            w.writerow(["%d-%d" % (int(tt * 1000), i), str(sec), str(ns),
                        str(int(e)), "0"])
    return p


def test_comb_medians_recovers_teeth():
    from supercycle_plot import comb_medians
    rng = np.random.default_rng(2)
    # a 1 Hz comb at the WORST phase (teeth at half-pitch, where naive
    # round(off/pitch) splits every tooth across two period indices)
    teeth = np.arange(0.5, 60.0, 1.0)
    offs = np.repeat(teeth, 25) + rng.normal(0, 0.0005, 25 * len(teeth))
    got = comb_medians(offs, median_len=60.0, n_cycles=25)
    assert len(got) == len(teeth)
    assert np.allclose(np.sort(got), teeth, atol=0.002)
    assert len(comb_medians(np.array([]), 60.0, 25)) == 0    # empty: no teeth


def test_rel_deltas_nearest_signed():
    from supercycle_plot import rel_deltas
    ref = np.array([10.0, 20.0, 30.0])
    tgt = np.array([10.002, 19.999, 25.0, 31.0, 5.0])
    d = rel_deltas(tgt, ref)
    assert np.allclose(d, [0.002, -0.001, 5.0, 1.0, -5.0])
    assert len(rel_deltas(tgt, np.array([]))) == 0


def test_rel_mode_renders_and_reports():
    import io
    from contextlib import redirect_stdout, redirect_stderr
    from supercycle_plot import main
    # target 0x1E fires 5 ms after every 4th ref tooth; ref 0x8F is a 1 Hz comb
    t, ev = _synthetic()
    with tempfile.TemporaryDirectory() as d:
        p = _write_csv(d, t, ev)
        out = os.path.join(d, "rel.svg")
        cap = io.StringIO()
        with redirect_stdout(cap):
            rc = main([p, "--target", "1E", "--ref", "8F", "-o", out, "--rel"])
        assert rc == 0
        for suffix in ("_hist.svg", "_raster.svg"):
            assert os.path.getsize(os.path.join(d, "rel" + suffix)) > 0
        rep = cap.getvalue()
        assert "target $1E vs $8F:" in rep and "sigma" in rep
        # _synthetic: targets at X.0 s, nearest 0x8F tooth at X-0.5 or X+0.5:
        # every delta is exactly +-500 ms
        assert "span -500.000 to +500.000 ms" in rep or \
               "span +500.000 to +500.000 ms" in rep or \
               "span -500.000 to -500.000 ms" in rep
        # two refs must be rejected
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc2 = main([p, "--target", "1E", "--ref", "8F,00", "-o", out, "--rel"])
        assert rc2 == 2 and "exactly one" in err.getvalue()


def test_load_events_globs_patterns():
    with tempfile.TemporaryDirectory() as d:
        rows = [["id", "sec", "ns", "event", "data"], ["1-0", "5", "0", "7", "0"]]
        for name in ("events-tclk-20260716.csv", "events-tclk-20260717.csv"):
            with open(os.path.join(d, name), "w", newline="") as f:
                w = csv.writer(f)
                w.writerows([rows[0], ["%s-0" % name[-6:-4], "5", "0", "7", "0"]])
        t, ev = load_events([os.path.join(d, "events-tclk-2026071*.csv")])
        assert len(t) == 2                       # both files matched
        try:
            load_events([os.path.join(d, "nope-*.csv")])
            raised = False
        except FileNotFoundError:
            raised = True
        assert raised                            # unmatched pattern is an error


def test_cycles_limit_title_and_color():
    import io
    from contextlib import redirect_stdout
    from supercycle_plot import main
    t, ev = _synthetic()
    with tempfile.TemporaryDirectory() as d:
        p = _write_csv(d, t, ev)
        out = os.path.join(d, "lim.svg")
        cap = io.StringIO()
        with redirect_stdout(cap):
            rc = main([p, "--target", "1E", "--ref", "8F", "-o", out,
                       "--cycles", "5", "--title", "Custom Title",
                       "--color", "#2b8cc4", "--png", "--dpi", "72"])
        assert rc == 0
        rep = cap.getvalue()
        assert "limiting to the last 5 of 8 kept cycles" in rep
        assert "cycles: 5 kept" in rep
        with open(os.path.join(d, "lim_raster.svg")) as f:
            svg = f.read()
        assert "Custom Title" in svg          # title override reached the figure
        assert "#2b8cc4" in svg               # color override reached the dots
        for suffix in ("_hist.png", "_raster.png"):   # --png writes rasters too
            assert os.path.getsize(os.path.join(d, "lim" + suffix)) > 0


def test_segment_start_finds_last_gap():
    from supercycle_plot import segment_start
    t = np.array([0.0, 1.0, 2.0, 100.0, 101.0, 300.0, 301.0, 302.0])
    assert segment_start(t) == 5                     # after the 101 -> 300 gap
    assert segment_start(np.array([1.0, 1.5, 2.0])) == 0
    assert segment_start(np.array([])) == 0


def test_last_segment_flag_drops_pre_seam_cycles():
    import io
    from contextlib import redirect_stdout
    from supercycle_plot import main
    # segment A: 4 clean cycles, then a 10 min capture hole, then segment B
    # (the _synthetic set: 8 kept + 1 folded-rejected cycle)
    tb, evb = _synthetic()
    ta, eva = [], []
    for i in range(5):                                # 5 anchors -> 4 cycles
        ta += [i * 60.0, i * 60.0 + 10.0, i * 60.0 + 30.5]
        eva += [0x00, 0x1E, 0x8F]
    shift = ta[-1] + 600.0                            # the capture hole
    t = np.concatenate([np.asarray(ta), tb + shift])
    ev = np.concatenate([np.asarray(eva, dtype=np.int64), evb])
    o = np.argsort(t, kind="stable")
    t, ev = t[o], ev[o]
    with tempfile.TemporaryDirectory() as d:
        p = _write_csv(d, t, ev)
        out = os.path.join(d, "seg.svg")
        cap = io.StringIO()
        with redirect_stdout(cap):
            rc = main([p, "--target", "1E", "--ref", "8F", "-o", out,
                       "--last-segment"])
        assert rc == 0
        rep = cap.getvalue()
        assert "last-segment: dropped 15 earlier events" in rep
        # only segment B analyzed: its internal folded window still rejects,
        # but segment A's 4 cycles and the seam window are gone
        assert "cycles: 8 kept / 1 rejected" in rep


def test_window_zoom_sets_xlim_and_renders():
    import io
    from contextlib import redirect_stdout
    import matplotlib
    matplotlib.use("Agg")
    from supercycle_plot import main, make_hist_figure
    # unit: the hist axes really crop to the window
    fig = make_hist_figure(np.array([1.0, 2.0, 7.0]), [], n_rows=8,
                           median_len=60.0, target=0x1E, refs=[], bins=50,
                           window=(0.0, 5.0))
    assert fig.axes[0].get_xlim() == (0.0, 5.0)
    # integration: --window through the CLI still renders the SVG pair
    t, ev = _synthetic()
    with tempfile.TemporaryDirectory() as d:
        p = _write_csv(d, t, ev)
        out = os.path.join(d, "zoom.svg")
        with redirect_stdout(io.StringIO()):
            rc = main([p, "--target", "1E", "--ref", "8F", "-o", out,
                       "--window", "5,15"])
        assert rc == 0
        for suffix in ("_hist.svg", "_raster.svg"):
            assert os.path.getsize(os.path.join(d, "zoom" + suffix)) > 0
        rc_bad = main([p, "--target", "1E", "-o", out, "--window", "9,9"])
        assert rc_bad == 2                       # empty window rejected


def test_make_figures_single_axes_each():
    import matplotlib
    matplotlib.use("Agg")
    from supercycle_plot import make_hist_figure, make_raster_figure
    rng = np.random.default_rng(1)
    off_t = rng.normal(10.0, 0.2, 200)
    row_t = rng.integers(0, 8, 200)
    off_r = np.tile(np.arange(60) + 0.5, 8)
    row_r = np.repeat(np.arange(8), 60)
    fig_h = make_hist_figure(off_t, [off_r], n_rows=8, median_len=60.0,
                             target=0x1E, refs=[0x8F], theme="default", bins=120)
    fig_r = make_raster_figure(off_t, row_t, off_r, row_r, n_rows=8,
                               median_len=60.0, target=0x1E, refs=[0x8F])
    assert len(fig_h.axes) == 1
    assert len(fig_r.axes) == 1


def test_main_reports_missing_target_with_available_codes():
    import io
    from contextlib import redirect_stderr
    from supercycle_plot import main
    t, ev = _synthetic()
    with tempfile.TemporaryDirectory() as d:
        p = _write_csv(d, t, ev)
        err = io.StringIO()
        with redirect_stderr(err):
            rc = main([p, "--target", "AB", "-o", os.path.join(d, "x.png")])
        assert rc == 2                                     # 0xAB never occurs
        msg = err.getvalue()
        assert "Available codes" in msg and "$1E:" in msg  # listing really printed


def test_main_renders_svg_pair():
    import io
    from contextlib import redirect_stdout
    from supercycle_plot import main
    t, ev = _synthetic()
    with tempfile.TemporaryDirectory() as d:
        p = _write_csv(d, t, ev)
        out = os.path.join(d, "sc.svg")
        cap = io.StringIO()
        with redirect_stdout(cap):
            rc = main([p, "--target", "1E", "--ref", "8F", "-o", out])
        assert rc == 0
        for suffix in ("_hist.svg", "_raster.svg"):
            f = os.path.join(d, "sc" + suffix)
            assert os.path.exists(f) and os.path.getsize(f) > 0
        assert not [f for f in os.listdir(d) if f.endswith(".png")]   # SVG only
        report = cap.getvalue()
        assert "cycles: 8 kept / 1 rejected" in report
        assert "target $1E: 8 events; per cycle min/median/max = 1/1/1" in report
        assert "mode near" in report
        assert "sc_hist.svg" in report and "sc_raster.svg" in report


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all supercycle_plot tests passed")
