#!/usr/bin/env python3
"""Plot a wr_pps_live.py log: phase wander and derived ppm over time.

    python3 plot_pps_log.py pps-live.log                # writes pps-live.png
    python3 plot_pps_log.py pps-live.log -o wander.png --window 600

Top panel: the unwrapped sub-second phase of the hardware clock against the
NTP-disciplined system clock, in microseconds relative to the first sample.
Whole-second relabels (re-arms, outage steps) are removed by the same
half-second unwrap the live monitor uses, so the trace is the oscillator's
physical phase walk. Rejected-edge events are ticked along the bottom.

Bottom panel: the sliding-window slope of that phase, i.e. the frequency
offset in ppm. The window (default 300 s) trades responsiveness for noise:
~0.05 ppm floor at 300 s, ~0.01 at 1800 s.

The log's own ppm columns are not re-plotted: they are anchored to monitor
restarts, while this recomputation is restart-proof (the phase is physical).
Timestamps are seconds-of-day and unwrap across midnight; plotting assumes
the log spans fewer than ~2 days."""
import argparse
import sys
from collections import namedtuple

import numpy as np

from wr_pps_live import wrap_half

GAP_SPLIT_S = 120.0     # break the drawn line across holes longer than this
MIN_SLOPE_SPAN = 10.0   # same floor as the live monitor's Walk.ppm

Run = namedtuple("Run", "t count miss rej phase_us")


def parse_log(lines):
    """Numeric samples from a wr_pps_live log; headers/UNSYNC/stop lines drop.

    Returns arrays with `t` in seconds unwrapped across midnight and
    `phase_us` the wrap_half-accumulated sub-second phase in microseconds,
    which rides through whole-second relabels."""
    t, count, miss, rej, frac = [], [], [], [], []
    day = 0.0
    prev_tod = None
    for line in lines:
        if not line.startswith("# ") or "UTC" in line or "stopped:" in line:
            continue
        tok = line[2:].split()
        if len(tok) < 5 or "UNSYNC" in line:
            continue
        try:
            hh, mm, ss = tok[0].split(":")
            tod = int(hh) * 3600 + int(mm) * 60 + int(ss)
            d = float(tok[4])
        except ValueError:
            continue
        if prev_tod is not None and tod < prev_tod - 43200:
            day += 86400.0
        prev_tod = tod
        t.append(day + tod)
        count.append(int(tok[1]))
        miss.append(int(tok[2]))
        rej.append(int(tok[3]))
        frac.append(d % 1.0)
    t = np.asarray(t, dtype=np.float64)
    frac = np.asarray(frac, dtype=np.float64)
    phase = np.zeros_like(frac)
    for i in range(1, len(frac)):
        phase[i] = phase[i - 1] + wrap_half(frac[i] - frac[i - 1])
    return Run(t=t, count=np.asarray(count), miss=np.asarray(miss),
               rej=np.asarray(rej), phase_us=phase * 1e6)


def sliding_ppm(t, phase_us, window_s=300.0):
    """Trailing-window slope of phase (us) vs t (s): ppm, NaN before span."""
    t = np.asarray(t, dtype=np.float64)
    p = np.asarray(phase_us, dtype=np.float64)
    out = np.full(len(t), np.nan)
    j = 0
    for i in range(len(t)):
        while t[i] - t[j] > window_s:
            j += 1
        if t[i] - t[j] >= MIN_SLOPE_SPAN:
            out[i] = (p[i] - p[j]) / (t[i] - t[j])
    return out


def robust_ylim(y, lo_pct=0.5, hi_pct=99.5, pad_frac=0.15):
    """Percentile-based axis limits so a few extreme samples cannot flatten
    the trace everyone actually wants to see. Returns (lo, hi, n_offscale),
    or None if there is nothing finite to scale to."""
    y = np.asarray(y, dtype=np.float64)
    y = y[np.isfinite(y)]
    if y.size == 0:
        return None
    lo, hi = np.percentile(y, [lo_pct, hi_pct])
    if hi <= lo:
        hi = lo + 1.0
    pad = (hi - lo) * pad_frac
    lo, hi = lo - pad, hi + pad
    return lo, hi, int(((y < lo) | (y > hi)).sum())


def _gap_break(t_s, x_plot, y):
    """Insert NaNs at holes so the plotted line breaks instead of bridging.

    Gaps are judged on `t_s` (seconds); the returned pair is (x_plot, y) with
    a NaN vertex added inside each hole, whatever units x_plot is drawn in."""
    cuts = np.flatnonzero(np.diff(t_s) > GAP_SPLIT_S)
    xi, yi = list(x_plot), list(y)
    for n, c in enumerate(cuts):
        xi.insert(c + 1 + n, (x_plot[c] + x_plot[c + 1]) / 2.0)
        yi.insert(c + 1 + n, np.nan)
    return np.asarray(xi), np.asarray(yi)


def render(run, out_path, window_s=300.0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    SURFACE, INK, INK2, BLUE, ORANGE = (
        "#fcfcfb", "#0b0b0b", "#52514e", "#2a78d6", "#eb6834")
    hours = (run.t - run.t[0]) / 3600.0
    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(10, 7), sharex=True, facecolor=SURFACE,
        gridspec_kw={"height_ratios": [3, 2, 1], "hspace": 0.14})

    phase = run.phase_us - run.phase_us[0]
    th, ph = _gap_break(run.t, hours, phase)
    ax1.plot(th, ph, color=BLUE, lw=1.4)
    lim = robust_ylim(phase)
    if lim:
        lo, hi, n_off = lim
        ax1.set_ylim(lo, hi)
        if n_off:
            ax1.text(0.99, 0.95,
                     "%d sample(s) off-scale (|max| %.1f ms)"
                     % (n_off, np.nanmax(np.abs(phase)) / 1000.0),
                     transform=ax1.transAxes, ha="right", va="top",
                     fontsize=8, color=INK2)
    ax1.set_ylabel("phase vs NTP (us)", color=INK)

    ppm = sliding_ppm(run.t, run.phase_us, window_s)
    tp, pp = _gap_break(run.t, hours, ppm)
    ax2.plot(tp, pp, color=BLUE, lw=1.4)
    ax2.axhline(0.0, color=INK2, lw=0.8, alpha=0.5)
    lim = robust_ylim(ppm)
    if lim:
        lo, hi, n_off = lim
        ax2.set_ylim(min(lo, -0.05), max(hi, 0.05))
        if n_off:
            ax2.text(0.99, 0.95, "%d sample(s) off-scale" % n_off,
                     transform=ax2.transAxes, ha="right", va="top",
                     fontsize=8, color=INK2)
    ax2.set_ylabel("ppm (%d s window)" % int(window_s), color=INK)

    # Rejects as a per-minute rate. Per-event ticks saturate into a solid bar
    # during a chatter storm (observed 2026-08-05: 1,762 in 45 min).
    edges = np.arange(run.t[0], run.t[-1] + 60.0, 60.0)
    idx = np.minimum(np.searchsorted(run.t, edges), len(run.t) - 1)
    rate = np.diff(run.rej[idx]).astype(float)
    ax3.step((edges[1:] - run.t[0]) / 3600.0, rate, where="pre",
             color=ORANGE, lw=1.2)
    ax3.set_ylabel("rejects/min", color=INK)
    ax3.set_ylim(bottom=0)
    ax3.set_xlabel("hours since first sample", color=INK)
    ax3.text(0.99, 0.90, "total +%d" % int(run.rej[-1] - run.rej[0]),
             transform=ax3.transAxes, ha="right", va="top",
             fontsize=8, color=INK2)

    for ax in (ax1, ax2, ax3):
        ax.set_facecolor(SURFACE)
        ax.grid(True, color=INK2, alpha=0.15, lw=0.6)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(colors=INK2, labelsize=9)
    span_h = (run.t[-1] - run.t[0]) / 3600.0
    ax1.set_title(
        "WR PPS vs NTP: %.1f h, net %+0.1f us (%+0.4f ppm mean)"
        % (span_h, run.phase_us[-1] - run.phase_us[0],
           (run.phase_us[-1] - run.phase_us[0]) / (run.t[-1] - run.t[0])),
        color=INK, fontsize=11, loc="left")
    fig.savefig(out_path, dpi=140, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log", help="pps-live.log from wr_pps_live.py")
    ap.add_argument("-o", "--out", default=None, help="output PNG path")
    ap.add_argument("--window", type=float, default=300.0,
                    help="ppm sliding window, seconds (default 300)")
    a = ap.parse_args(argv)
    with open(a.log, encoding="utf-8", errors="replace") as f:
        run = parse_log(f)
    if len(run.t) < 3:
        sys.exit("too few samples in %s" % a.log)
    out = a.out or (a.log.rsplit(".", 1)[0] + ".png")
    render(run, out, window_s=a.window)
    print("wrote %s  (%d samples, %.1f h)"
          % (out, len(run.t), (run.t[-1] - run.t[0]) / 3600.0))


if __name__ == "__main__":
    main()
