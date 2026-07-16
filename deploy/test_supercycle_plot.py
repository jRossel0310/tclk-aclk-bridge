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
    assert row[tgt].min() >= 0 and row[tgt].max() < len(starts)
    # events inside the rejected (folded) window are masked out entirely
    in_rejected = (t >= 4 * 60.0) & (t < 6 * 60.0) & (ev == 0x1E)
    assert not mask[in_rejected].any()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all supercycle_plot tests passed")
