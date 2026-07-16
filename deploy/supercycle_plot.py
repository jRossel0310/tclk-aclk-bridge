#!/usr/bin/env python3
"""Folded supercycle raster + distribution shape for one TCLK event code (PC side).

Reads the CSVs written by stream_archive.py, anchors every event to the
preceding $00 (supercycle reset), folds all supercycles onto one time axis,
and renders TWO separate SVGs (no PNGs): <stem>_hist.svg, the folded offset
histogram of the target code (the distribution shape), and <stem>_raster.svg,
one row per supercycle with target events as dots (the per-cycle evidence:
slot changes, drift, anomalous cycles). Reference-comb events render faint
behind both. Cycles whose length deviates from the median by more than --tol
are rejected (a missed anchor would fold two cycles).

    python supercycle_plot.py events-tclk-*.csv --target 1E --ref 0C,BA
    python supercycle_plot.py tail.csv --target 1F --theme poster -o bes
"""
import argparse
import csv
import sys
import time

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


def segment_start(t, gap=10.0):
    """Index where the LAST continuous capture segment begins. Healthy capture
    never has inter-event gaps beyond tens of ms (the 15/20 Hz combs always
    run), so any gap larger than `gap` seconds marks a capture seam (a run
    restart, a Redis wipe, an outage). t must be time-sorted (load_events is)."""
    if len(t) == 0:
        return 0
    cuts = np.where(np.diff(t) > gap)[0]
    return 0 if len(cuts) == 0 else int(cuts[-1]) + 1


def rel_deltas(t_tgt, t_ref):
    """Signed time from each target event to its NEAREST reference event, in
    seconds. Positive = target after the ref. This cancels wander common to
    both signals (e.g. line-frequency drift vs the $00 anchor) and leaves the
    genuine target-to-ref phase relationship."""
    t_tgt = np.asarray(t_tgt, dtype=np.float64)
    t_ref = np.asarray(t_ref, dtype=np.float64)
    if len(t_ref) == 0 or len(t_tgt) == 0:
        return np.zeros(0, dtype=np.float64)
    i = np.searchsorted(t_ref, t_tgt)
    lo = np.clip(i - 1, 0, len(t_ref) - 1)
    hi = np.clip(i, 0, len(t_ref) - 1)
    d_lo = t_tgt - t_ref[lo]
    d_hi = t_tgt - t_ref[hi]
    return np.where(np.abs(d_lo) <= np.abs(d_hi), d_lo, d_hi)


# blue-and-white theme (matches the other poster figures)
INK, MUTED, FAINT, SURF = "#1b1b1b", "#6f6f6f", "#dfe6ee", "#ffffff"
C_TARGET, C_REF = "#1b5a8f", "#9aa7b4"


def _hex(code):
    return "0x%02X" % code if code <= 0xFF else "0x%04X" % code


def _new_fig(theme, figsize_default, figsize_poster, target, n_rows, median_len,
             subtitle_suffix):
    """Shared figure scaffold: Agg backend, theme rcParams, title + subtitle.
    Returns (fig, plt)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    poster = (theme == "poster")
    plt.rcParams.update({"font.family": "DejaVu Sans",
                         "font.size": 14 if poster else 11,
                         "svg.fonttype": "none"})
    fig = plt.figure(figsize=figsize_poster if poster else figsize_default,
                     dpi=300, facecolor=SURF)
    fig.suptitle("Event %s within the TCLK supercycle" % _hex(target),
                 x=0.085, y=0.945, ha="left", fontsize=19, fontweight="bold",
                 color=INK)
    fig.text(0.085, 0.865,
             "%d supercycles folded on $00, median length %.3f s%s"
             % (n_rows, median_len, subtitle_suffix),
             ha="left", fontsize=12, color=MUTED)
    return fig, plt


def comb_medians(offsets, median_len, n_cycles):
    """Median position of each tooth of ONE periodic reference comb. The comb's
    period is estimated from the data (pitch = median_len / events-per-cycle)
    and each event is assigned to its nearest period index, so the per-tooth
    medians stay correct even when the comb's phase relative to the anchor
    wanders by tens of ms deep into the cycle (the 15/20 Hz codes are
    line-frequency-locked, the supercycle anchor is not exactly so)."""
    offsets = np.asarray(offsets, dtype=np.float64)
    if len(offsets) == 0 or n_cycles <= 0:
        return np.asarray([], dtype=np.float64)
    per_cycle = max(1, int(round(len(offsets) / n_cycles)))
    pitch = median_len / per_cycle
    # Anchor the period grid on the comb's own phase (circular mean of offset
    # mod pitch) so period boundaries land between teeth for ANY comb phase;
    # a plain round(offset/pitch) splits teeth sitting near half-pitch.
    ang = offsets * (2.0 * np.pi / pitch)
    phi = np.arctan2(np.mean(np.sin(ang)), np.mean(np.cos(ang))) * pitch / (2.0 * np.pi)
    k = np.round((offsets - phi) / pitch).astype(np.int64)
    return np.asarray([float(np.median(offsets[k == kk]))
                       for kk in np.unique(k)])


def _window_suffix(window, median_len):
    lo, hi = window
    if lo <= 0.0 and hi >= median_len:
        return ""
    return ", window %g-%g s" % (lo, hi)


def make_hist_figure(off_t, ref_offs, n_rows, median_len, target, refs,
                     theme="default", bins=600, window=None):
    """The distribution 'shape': target offsets folded across all kept cycles.
    References are NOT histogrammed (their per-bin counts would dominate the
    y axis and squash the target): each ref comb tooth is drawn as a thin gray
    full-height line at its median position, in axes coordinates, so the y
    scale is set by the target alone. ref_offs is a list of offset arrays,
    one per code in refs (clustered per code: interleaved combs would merge).
    window=(lo, hi) crops the x axis; `bins` always spans the WINDOW, so
    zooming in refines the binning automatically."""
    window = (0.0, median_len) if window is None else window
    lo, hi = window
    fig, _ = _new_fig(theme, (11, 4.2), (12.5, 4.8), target, n_rows, median_len,
                      _window_suffix(window, median_len))
    ax = fig.add_axes([0.085, 0.17, 0.89, 0.60])

    teeth = np.concatenate([comb_medians(o, median_len, n_rows)
                            for o in ref_offs]) if ref_offs else np.asarray([])
    teeth = teeth[(teeth >= lo) & (teeth <= hi)]
    if len(teeth):
        ax.vlines(teeth, 0, 1, transform=ax.get_xaxis_transform(),
                  color=C_REF, alpha=0.35, linewidth=0.6, zorder=1,
                  label="ref %s (tooth medians)"
                        % ", ".join(_hex(r) for r in refs))
    edges = np.linspace(lo, hi, bins + 1)
    ax.hist(off_t, bins=edges, color=C_TARGET, zorder=3,
            label="target " + _hex(target))
    # legend ABOVE the axes (right-aligned, one row) so it never sits on data
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.02), ncol=2,
              fontsize=10, frameon=False, borderaxespad=0.0)
    ax.set_xlim(lo, hi)
    ax.set_xlabel("offset into supercycle (s)", fontsize=11, color=MUTED)
    ax.set_ylabel("events / bin", fontsize=10, color=MUTED)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    return fig


def make_raster_figure(off_t, row_t, off_r, row_r, n_rows, median_len,
                       target, refs, theme="default", window=None):
    """The per-cycle evidence: one row per supercycle (oldest at top), target
    events as dots, reference events as a faint backdrop. window=(lo, hi)
    crops the x axis (events outside are dropped, which also keeps the SVG
    small on tight zooms)."""
    window = (0.0, median_len) if window is None else window
    lo, hi = window
    keep_t = (off_t >= lo) & (off_t <= hi)
    keep_r = (off_r >= lo) & (off_r <= hi)
    off_t, row_t = off_t[keep_t], row_t[keep_t]
    off_r, row_r = off_r[keep_r], row_r[keep_r]
    fig, _ = _new_fig(theme, (11, 6.0), (12.5, 7.0), target, n_rows, median_len,
                      ", cycle by cycle" + _window_suffix(window, median_len))
    ax = fig.add_axes([0.085, 0.10, 0.89, 0.68])

    if len(off_r):
        ax.scatter(off_r, row_r, s=2, color=C_REF, alpha=0.25,
                   linewidths=0, zorder=2,
                   label="ref " + ", ".join(_hex(r) for r in refs))
    ax.scatter(off_t, row_t, s=14, color=C_TARGET, linewidths=0, zorder=3,
               label="target " + _hex(target))
    # legend ABOVE the axes (right-aligned, one row) so it never sits on data
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.02), ncol=2,
              fontsize=10, frameon=False, borderaxespad=0.0,
              markerscale=2.5)
    ax.set_xlim(lo, hi)
    ax.set_ylim(-0.5, n_rows - 0.5)
    ax.invert_yaxis()                         # first cycle at the top
    ax.set_xlabel("offset into supercycle (s)", fontsize=11, color=MUTED)
    ax.set_ylabel("supercycle (time order)", fontsize=11, color=MUTED)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    return fig


def _rel_range(d_ms, window):
    if window is not None:
        return window
    lo, hi = np.percentile(d_ms, [0.1, 99.9])
    pad = max(0.05 * (hi - lo), 0.01)
    return (float(lo - pad), float(hi + pad))


def make_rel_hist_figure(d_ms, n_rows, median_len, target, ref,
                         theme="default", bins=600, window=None):
    """Distribution of the target-to-nearest-ref delta (ms), folded across all
    kept cycles: the phase-lock quality between the two signals."""
    lo, hi = _rel_range(d_ms, window)
    fig, _ = _new_fig(theme, (11, 4.2), (12.5, 4.8), target, n_rows, median_len,
                      ", relative to nearest %s" % _hex(ref))
    ax = fig.add_axes([0.085, 0.17, 0.89, 0.60])
    ax.axvline(0.0, color=C_REF, alpha=0.8, linewidth=1.0, zorder=1,
               label="%s (ref = 0)" % _hex(ref))
    ax.hist(d_ms, bins=np.linspace(lo, hi, bins + 1), color=C_TARGET, zorder=3,
            label="target " + _hex(target))
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.02), ncol=2,
              fontsize=10, frameon=False, borderaxespad=0.0)
    ax.set_xlim(lo, hi)
    ax.set_xlabel("t(%s) - t(nearest %s)  (ms)" % (_hex(target), _hex(ref)),
                  fontsize=11, color=MUTED)
    ax.set_ylabel("events / bin", fontsize=10, color=MUTED)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    return fig


def make_rel_raster_figure(d_ms, row_t, n_rows, median_len, target, ref,
                           theme="default", window=None):
    """Cycle-by-cycle view of the target-to-nearest-ref delta (ms)."""
    lo, hi = _rel_range(d_ms, window)
    keep = (d_ms >= lo) & (d_ms <= hi)
    fig, _ = _new_fig(theme, (11, 6.0), (12.5, 7.0), target, n_rows, median_len,
                      ", relative to nearest %s, cycle by cycle" % _hex(ref))
    ax = fig.add_axes([0.085, 0.10, 0.89, 0.68])
    ax.axvline(0.0, color=C_REF, alpha=0.8, linewidth=1.0, zorder=1,
               label="%s (ref = 0)" % _hex(ref))
    ax.scatter(d_ms[keep], row_t[keep], s=14, color=C_TARGET, linewidths=0,
               zorder=3, label="target " + _hex(target))
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.02), ncol=2,
              fontsize=10, frameon=False, borderaxespad=0.0, markerscale=2.5)
    ax.set_xlim(lo, hi)
    ax.set_ylim(-0.5, n_rows - 0.5)
    ax.invert_yaxis()
    ax.set_xlabel("t(%s) - t(nearest %s)  (ms)" % (_hex(target), _hex(ref)),
                  fontsize=11, color=MUTED)
    ax.set_ylabel("supercycle (time order)", fontsize=11, color=MUTED)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
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
    ap.add_argument("--bins", type=int, default=600,
                    help="histogram bins ACROSS THE WINDOW (zooming refines them)")
    ap.add_argument("--window", default=None,
                    help="x-axis zoom in seconds, e.g. 0,5 (default: whole cycle)")
    ap.add_argument("--last-segment", action="store_true",
                    help="analyze only the final continuous capture segment "
                         "(drop everything before the last >10 s event gap)")
    ap.add_argument("--rel", action="store_true",
                    help="plot t(target) - t(nearest ref) in ms instead of the "
                         "offset from $00 (requires exactly one --ref; --window "
                         "is then in ms of delta)")
    ap.add_argument("--theme", choices=("default", "poster"), default="default")
    ap.add_argument("--topn-report", type=int, default=5)
    ap.add_argument("-o", "--out", default="supercycle.svg",
                    help="output basename; writes <stem>_hist.svg + <stem>_raster.svg")
    args = ap.parse_args(argv)

    target = int(args.target, 16)
    refs = _parse_codes(args.ref)
    anchor = int(args.anchor, 16)

    t, ev = load_events(args.csvs)
    if args.last_segment:
        i0 = segment_start(t)
        if i0:
            print("last-segment: dropped %d earlier events; analyzing %d events "
                  "from %s UTC"
                  % (i0, len(t) - i0,
                     time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t[i0]))))
            t, ev = t[i0:], ev[i0:]
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
    ref_offs = [off[mask & (ev == r)] for r in refs]   # per code, for tooth medians

    window = None
    if args.window:
        try:
            lo, hi = (float(x) for x in args.window.split(","))
        except ValueError:
            print("bad --window %r; expected LO,HI (e.g. 0,5)"
                  % args.window, file=sys.stderr)
            return 2
        if not args.rel:                      # cycle-offset mode: clip to the cycle
            lo = max(0.0, lo)
            hi = min(stats["median_len"], hi)
        if not lo < hi:
            print("empty --window %r" % args.window, file=sys.stderr)
            return 2
        window = (lo, hi)

    # Two separate SVGs: the folded distribution (the 'shape') and the per-cycle
    # raster (the evidence: slot changes, drift, anomalous cycles). SVG only:
    # they scale losslessly and drop straight into the poster/docs.
    stem = args.out.rsplit(".", 1)[0] if "." in args.out.rsplit("/", 1)[-1] else args.out
    out_hist = stem + "_hist.svg"
    out_raster = stem + "_raster.svg"
    lens = ends - starts
    print("cycles: %d kept / %d rejected (median %.6f s, sigma %.6f s)"
          % (stats["n_kept"], stats["n_rejected"], stats["median_len"],
             float(np.std(lens))))

    if args.rel:
        if len(refs) != 1:
            print("--rel needs exactly one --ref code", file=sys.stderr)
            return 2
        t_ref = t[mask & (ev == refs[0])]
        if len(t_ref) == 0:
            print("no ref events %s inside kept cycles" % _hex(refs[0]),
                  file=sys.stderr)
            return 2
        d_ms = rel_deltas(t[is_t], t_ref) * 1e3
        fig_h = make_rel_hist_figure(d_ms, stats["n_kept"], stats["median_len"],
                                     target, refs[0], theme=args.theme,
                                     bins=args.bins, window=window)
        fig_h.savefig(out_hist, facecolor=SURF, bbox_inches="tight")
        fig_r = make_rel_raster_figure(d_ms, row[is_t], stats["n_kept"],
                                       stats["median_len"], target, refs[0],
                                       theme=args.theme, window=window)
        fig_r.savefig(out_raster, facecolor=SURF, bbox_inches="tight")
        print("target %s vs %s: %d events; delta mean %+.3f ms, sigma %.3f ms, "
              "span %+.3f to %+.3f ms"
              % (_hex(target), _hex(refs[0]), len(d_ms), float(np.mean(d_ms)),
                 float(np.std(d_ms)), float(np.min(d_ms)), float(np.max(d_ms))))
    else:
        fig_h = make_hist_figure(off[is_t], ref_offs, n_rows=stats["n_kept"],
                                 median_len=stats["median_len"], target=target,
                                 refs=refs, theme=args.theme, bins=args.bins,
                                 window=window)
        fig_h.savefig(out_hist, facecolor=SURF, bbox_inches="tight")
        fig_r = make_raster_figure(off[is_t], row[is_t], off[is_r], row[is_r],
                                   n_rows=stats["n_kept"],
                                   median_len=stats["median_len"],
                                   target=target, refs=refs, theme=args.theme,
                                   window=window)
        fig_r.savefig(out_raster, facecolor=SURF, bbox_inches="tight")
        per_cycle = np.bincount(row[is_t], minlength=stats["n_kept"])
        hist, edges = np.histogram(
            off[is_t], bins=np.linspace(0, stats["median_len"], args.bins + 1))
        top = np.argsort(hist)[::-1][:args.topn_report]
        top = [i for i in top if hist[i] > 0]
        print("target %s: %d events; per cycle min/median/max = %d/%d/%d"
              % (_hex(target), int(is_t.sum()), per_cycle.min(),
                 int(np.median(per_cycle)), per_cycle.max()))
        for i in sorted(top, key=lambda i: edges[i]):
            print("  mode near %8.3f s: %d events" % (edges[i], int(hist[i])))
    print("wrote %s and %s" % (out_hist, out_raster))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
