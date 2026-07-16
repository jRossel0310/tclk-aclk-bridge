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


def test_make_figure_two_axes():
    import matplotlib
    matplotlib.use("Agg")
    from supercycle_plot import make_figure
    rng = np.random.default_rng(1)
    off_t = rng.normal(10.0, 0.2, 200)
    row_t = rng.integers(0, 8, 200)
    off_r = np.tile(np.arange(60) + 0.5, 8)
    row_r = np.repeat(np.arange(8), 60)
    fig = make_figure(off_t, row_t, off_r, row_r, n_rows=8, median_len=60.0,
                      target=0x1E, refs=[0x8F], theme="default", bins=120)
    assert len(fig.axes) == 2


def test_main_reports_missing_target_with_available_codes(capsys=None):
    from supercycle_plot import main
    t, ev = _synthetic()
    with tempfile.TemporaryDirectory() as d:
        p = _write_csv(d, t, ev)
        rc = main([p, "--target", "AB", "-o", os.path.join(d, "x.png")])
        assert rc != 0                                     # 0xAB never occurs


def test_main_renders_png():
    from supercycle_plot import main
    t, ev = _synthetic()
    with tempfile.TemporaryDirectory() as d:
        p = _write_csv(d, t, ev)
        out = os.path.join(d, "sc.png")
        rc = main([p, "--target", "1E", "--ref", "8F", "-o", out])
        assert rc == 0 and os.path.exists(out) and os.path.getsize(out) > 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all supercycle_plot tests passed")
