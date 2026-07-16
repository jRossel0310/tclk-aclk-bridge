#!/usr/bin/env python3
"""Folded supercycle raster + distribution shape for one TCLK event code (PC side).

Reads the CSVs written by stream_archive.py, anchors every event to the
preceding $00 (supercycle reset), folds all supercycles onto one time axis,
and renders: a marginal histogram of the target code's offsets (the shape) on
top of a raster (one row per supercycle, reference-comb events as faint dots,
target events as colored dots). Cycles whose length deviates from the median
by more than --tol are rejected (a missed anchor would fold two cycles).

    python supercycle_plot.py events-tclk-*.csv --target 1E --ref 0C,BA
    python supercycle_plot.py tail.csv --target 1F --theme poster -o bes.png
"""
import argparse
import csv
import sys

import numpy as np


def load_events(paths):
    """CSV file(s) -> (t seconds float64, event int), deduped by stream id,
    stably time-sorted."""
    seen = set()
    t, ev = [], []
    for p in paths:
        with open(p, newline="") as f:
            for row in csv.DictReader(f):
                eid = row["id"]
                if eid in seen:
                    continue
                seen.add(eid)
                t.append(int(row["sec"]) + int(row["ns"]) * 1e-9)
                ev.append(int(row["event"]))
    t = np.asarray(t, dtype=np.float64)
    ev = np.asarray(ev, dtype=np.int64)
    order = np.argsort(t, kind="stable")
    return t[order], ev[order]


def cycles_from_anchors(anchor_t, tol=0.01):
    """Consecutive-anchor windows filtered to |len - median| <= tol*median.
    Returns (starts, ends, stats). Raises ValueError with a clear message when
    fewer than 2 anchors exist."""
    if len(anchor_t) < 2:
        raise ValueError("need at least 2 anchor events to form a cycle "
                         "(got %d)" % len(anchor_t))
    lens = np.diff(anchor_t)
    med = float(np.median(lens))
    keep = np.abs(lens - med) <= tol * med
    stats = {"median_len": med, "n_cycles": int(len(lens)),
             "n_kept": int(keep.sum()), "n_rejected": int((~keep).sum())}
    return anchor_t[:-1][keep], anchor_t[1:][keep], stats


def assign_offsets(t, starts, ends):
    """Per event: (mask in-a-kept-cycle, dense row index, offset seconds).
    row/off are full-length arrays, meaningful only where mask is True."""
    if len(starts) == 0:                     # no usable cycles: nothing maps
        return (np.zeros(len(t), dtype=bool),
                np.zeros(len(t), dtype=np.int64),
                np.zeros(len(t), dtype=np.float64))
    idx = np.searchsorted(starts, t, side="right") - 1
    idx_c = np.clip(idx, 0, len(starts) - 1)
    mask = (idx >= 0) & (t < ends[idx_c])
    off = t - starts[idx_c]
    return mask, idx_c, off


# blue-and-white theme (matches the other poster figures)
INK, MUTED, FAINT, SURF = "#1b1b1b", "#6f6f6f", "#dfe6ee", "#ffffff"
C_TARGET, C_REF = "#1b5a8f", "#9aa7b4"


def _hex(code):
    return "0x%02X" % code if code <= 0xFF else "0x%04X" % code


def make_figure(off_t, row_t, off_r, row_r, n_rows, median_len,
                target, refs, theme="default", bins=600):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    poster = (theme == "poster")
    plt.rcParams.update({"font.family": "DejaVu Sans",
                         "font.size": 14 if poster else 11,
                         "svg.fonttype": "none"})
    fig = plt.figure(figsize=(12.5, 7.5) if poster else (11, 6.5),
                     dpi=300, facecolor=SURF)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.0, 2.9], hspace=0.07,
                          left=0.085, right=0.975, top=0.86, bottom=0.10)
    ax_h = fig.add_subplot(gs[0])
    ax_r = fig.add_subplot(gs[1], sharex=ax_h)

    edges = np.linspace(0.0, median_len, bins + 1)
    if len(off_r):
        ax_h.hist(off_r, bins=edges, color=C_REF, alpha=0.45, zorder=2,
                  label="ref " + ", ".join(_hex(r) for r in refs))
    ax_h.hist(off_t, bins=edges, color=C_TARGET, zorder=3,
              label="target " + _hex(target))
    ax_h.legend(loc="upper right", fontsize=10, frameon=False)
    ax_h.set_ylabel("events / bin", fontsize=10, color=MUTED)
    ax_h.tick_params(labelbottom=False, length=0)
    for sp in ("top", "right"):
        ax_h.spines[sp].set_visible(False)

    if len(off_r):
        ax_r.scatter(off_r, row_r, s=2, color=C_REF, alpha=0.25,
                     linewidths=0, zorder=2)
    ax_r.scatter(off_t, row_t, s=14, color=C_TARGET, linewidths=0, zorder=3)
    ax_r.set_xlim(0.0, median_len)
    ax_r.set_ylim(-0.5, n_rows - 0.5)
    ax_r.invert_yaxis()                       # first cycle at the top
    ax_r.set_xlabel("offset into supercycle (s)", fontsize=11, color=MUTED)
    ax_r.set_ylabel("supercycle (time order)", fontsize=11, color=MUTED)
    for sp in ("top", "right"):
        ax_r.spines[sp].set_visible(False)

    fig.suptitle("Event %s within the TCLK supercycle" % _hex(target),
                 x=0.085, y=0.965, ha="left", fontsize=19, fontweight="bold",
                 color=INK)
    fig.text(0.085, 0.895,
             "%d supercycles folded on $00, median length %.3f s"
             % (n_rows, median_len), ha="left", fontsize=12, color=MUTED)
    return fig


def _parse_codes(s):
    return [int(c, 16) for c in s.split(",") if c.strip()]


def main(argv):
    ap = argparse.ArgumentParser(description="Supercycle folded raster + shape.")
    ap.add_argument("csvs", nargs="+")
    ap.add_argument("--target", required=True, help="event code, hex (e.g. 1E)")
    ap.add_argument("--ref", default="0C,BA", help="reference codes, hex CSV")
    ap.add_argument("--anchor", default="00", help="cycle anchor code, hex")
    ap.add_argument("--tol", type=float, default=0.01)
    ap.add_argument("--bins", type=int, default=600)
    ap.add_argument("--theme", choices=("default", "poster"), default="default")
    ap.add_argument("--topn-report", type=int, default=5)
    ap.add_argument("-o", "--out", default="supercycle.png")
    args = ap.parse_args(argv)

    target = int(args.target, 16)
    refs = _parse_codes(args.ref)
    anchor = int(args.anchor, 16)

    t, ev = load_events(args.csvs)
    for code, what in [(anchor, "anchor"), (target, "target")]:
        if not (ev == code).any():
            uniq, cnt = np.unique(ev, return_counts=True)
            avail = "  ".join("%s:%d" % (_hex(int(u)), int(c))
                              for u, c in zip(uniq, cnt))
            print("no %s events %s in the data. Available codes:\n%s"
                  % (what, _hex(code), avail), file=sys.stderr)
            return 2

    try:
        starts, ends, stats = cycles_from_anchors(t[ev == anchor], tol=args.tol)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if stats["n_kept"] < 5:
        print("only %d usable cycles (need >= 5); capture longer or check the "
              "anchor code" % stats["n_kept"], file=sys.stderr)
        return 2

    mask, row, off = assign_offsets(t, starts, ends)
    is_t = mask & (ev == target)
    is_r = mask & np.isin(ev, refs)

    fig = make_figure(off[is_t], row[is_t], off[is_r], row[is_r],
                      n_rows=stats["n_kept"], median_len=stats["median_len"],
                      target=target, refs=refs, theme=args.theme,
                      bins=args.bins)
    fig.savefig(args.out, dpi=300, facecolor=SURF, bbox_inches="tight")
    svg = args.out.rsplit(".", 1)[0] + ".svg"
    fig.savefig(svg, facecolor=SURF, bbox_inches="tight")

    lens = ends - starts
    per_cycle = np.bincount(row[is_t], minlength=stats["n_kept"])
    hist, edges = np.histogram(off[is_t],
                               bins=np.linspace(0, stats["median_len"], args.bins + 1))
    top = np.argsort(hist)[::-1][:args.topn_report]
    top = [i for i in top if hist[i] > 0]
    print("cycles: %d kept / %d rejected (median %.6f s, sigma %.6f s)"
          % (stats["n_kept"], stats["n_rejected"], stats["median_len"],
             float(np.std(lens))))
    print("target %s: %d events; per cycle min/median/max = %d/%d/%d"
          % (_hex(target), int(is_t.sum()), per_cycle.min(),
             int(np.median(per_cycle)), per_cycle.max()))
    for i in sorted(top, key=lambda i: edges[i]):
        print("  mode near %8.3f s: %d events" % (edges[i], int(hist[i])))
    print("wrote %s and %s" % (args.out, svg))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
