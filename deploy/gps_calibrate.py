#!/usr/bin/env python3
"""Calibrate the White-Rabbit-derived stamp clock against TCLK's GPS marker.

WHY THIS EXISTS. The PL timebase counts WR PPS edges (wr_timebase.sv: `sec` is
loaded once at arm, then incremented once per PPS forever), so a HW second IS a
PPS period by construction -- `CELLS_LAST` reads exactly 10,000,000 every
interval. That makes the timebase perfectly uniform but only as *accurate* as
the PPS source, which is a free-running oscillator rather than a disciplined one.
The offset it contributes is per-boot, not a property of the installation, so do
not bake a constant in: measure it per run from that run's own markers. This
module does that measurement and removes it.

THE REFERENCE. TCLK's $8F is the GPS-locked 1 Hz event, and it is the only
absolute time reference in the chain. Do NOT use $02 for frequency: it is a
supercycle marker whose period genuinely varies (measured -3.89 ppm against
$8F's -3.48, with 2 s excursions in its fit residual).

WHAT IT CANNOT DO. This is a *duration* correction, computed from marker
intervals. It rescales elapsed time; it cannot recover an absolute epoch offset,
because nothing here knows the true UTC of the first stamp.

    python3 gps_calibrate.py flags-*.csv                  # health check, one run
    python3 gps_calibrate.py events-tclk-*.csv --event 02  # any id,sec,ns,event,... CSV
"""
import argparse
import csv
import glob
import sys
from collections import namedtuple

import numpy as np

NS = 1_000_000_000
GPS_EVENT = 0x8F          # TCLK 1 Hz GPS marker: the frequency reference
REJECT_NS = 1_000_000.0   # a marker this far off its slot is an outlier, not a datum

Calibration = namedtuple(
    "Calibration",
    "ppm hw_ns_per_marker n span_s resid resid_sd dropouts rejected intercept")
Window = namedtuple("Window", "t_center_ns ppm resid_sd n")


def marker_index(t_ns, nominal_ns=NS):
    """Integer marker slot for each stamp, tolerant of dropped markers.

    A missing marker shows up as an interval of ~k*nominal, so rounding each
    interval to whole slots and cumulating keeps later markers on their true
    index instead of sliding the whole series by one."""
    t = np.asarray(t_ns, dtype=np.float64)
    step = np.maximum(np.rint(np.diff(t) / nominal_ns), 1.0)
    return np.concatenate([[0.0], np.cumsum(step)])


def split_sessions(t_ns, max_gap_s=60.0):
    """Split marker stamps into contiguous capture sessions.

    Every capture here starts with a stale block: the readout FIFO fills while
    no reader owns it, so the first ~512 rows drained are events from whenever
    the previous reader stopped, hours earlier. That leaves one enormous gap
    near the head of the file. It is not missing data and it must not be fitted
    or windowed as though the run spanned it."""
    t = np.asarray(t_ns, dtype=np.float64)
    cuts = np.flatnonzero(np.diff(t) > max_gap_s * NS) + 1
    return [s for s in np.split(t, cuts) if s.size]


def _outlier_mask(t_ns, nominal_ns):
    """Markers adjacent to an interval that is not close to a whole slot."""
    t = np.asarray(t_ns, dtype=np.float64)
    d = np.diff(t)
    step = np.maximum(np.rint(d / nominal_ns), 1.0)
    bad = np.abs(d - step * nominal_ns) > REJECT_NS
    drop = np.zeros(len(t), dtype=bool)
    idx = np.flatnonzero(bad)
    drop[idx] = True
    drop[idx + 1] = True
    return drop, int((step != 1).sum())


def calibrate(t_ns, nominal_ns=NS):
    """Least-squares fit of marker stamps against their true slot index.

    Returns a Calibration whose `ppm` is the stamp clock's fractional frequency
    error: negative means the timebase runs slow, so a measured duration is
    SHORTER than the true elapsed time. Markers on either side of a
    non-integer-slot interval are excluded from the fit (a late marker would
    otherwise tilt the slope and swamp the residual)."""
    t = np.asarray(t_ns, dtype=np.float64)
    if t.size < 4:
        raise ValueError("calibrate: need at least 4 markers, got %d" % t.size)
    k = marker_index(t, nominal_ns)
    drop, dropouts = _outlier_mask(t, nominal_ns)
    keep = ~drop
    if keep.sum() < 4:
        raise ValueError("calibrate: fewer than 4 usable markers after rejection")
    slope, intercept = np.polyfit(k[keep], t[keep], 1)
    resid = t[keep] - (slope * k[keep] + intercept)
    return Calibration(
        ppm=(slope / nominal_ns - 1.0) * 1e6,
        hw_ns_per_marker=slope,
        n=int(t.size),
        span_s=float(t[-1] - t[0]) / NS,
        resid=resid,
        resid_sd=float(resid.std()),
        dropouts=dropouts,
        rejected=int(drop.sum()),
        intercept=float(intercept),
    )


def calibrate_windowed(t_ns, nominal_ns=NS, window_s=3600.0, min_markers=100):
    """Per-window calibration, so slow oscillator wander is visible rather than
    averaged away. The spread of these is the honest stability figure."""
    t = np.asarray(t_ns, dtype=np.float64)
    w = window_s * NS
    out = []
    for i in range(int((t[-1] - t[0]) // w) + 1):
        lo = t[0] + i * w
        sel = (t >= lo) & (t < lo + w)
        if sel.sum() < min_markers:
            continue
        try:
            c = calibrate(t[sel], nominal_ns)
        except ValueError:
            continue
        out.append(Window(t_center_ns=float(lo + w / 2), ppm=c.ppm,
                          resid_sd=c.resid_sd, n=int(sel.sum())))
    return out


def to_true_ns(hw_ns, ppm, t0_ns=None):
    """Rescale HW-timebase stamps into true elapsed nanoseconds since t0.

    A timebase running `ppm` slow under-reports elapsed time, so the correction
    divides by (1 + ppm*1e-6). Output is a duration from t0, NOT an absolute
    epoch: the absolute offset is unknowable from marker intervals alone."""
    hw = np.asarray(hw_ns, dtype=np.float64)
    t0 = float(hw[0]) if t0_ns is None else float(t0_ns)
    return (hw - t0) / (1.0 + ppm * 1e-6)


def to_true_ns_tracked(hw_ns, marker_ns, nominal_ns=NS, smooth_markers=3600):
    """Like to_true_ns, but follows slow wander using the markers as a ruler.

    A single scale factor leaves the oscillator's drift in the result, since even
    a fraction of a ppm of wander accumulates over a long capture. This subtracts a
    boxcar-smoothed version of the marker fit residual, so long-term wander is
    removed while per-marker jitter is NOT injected into every timestamp."""
    hw = np.asarray(hw_ns, dtype=np.float64)
    m = np.asarray(marker_ns, dtype=np.float64)
    cal = calibrate(m, nominal_ns)
    k = marker_index(m, nominal_ns)
    resid = m - (cal.hw_ns_per_marker * k + cal.intercept)
    win = max(1, min(int(smooth_markers), len(resid)))
    kern = np.ones(win) / win
    smooth = np.convolve(resid, kern, mode="same")
    edge = np.convolve(np.ones_like(resid), kern, mode="same")   # un-bias the ends
    smooth = smooth / edge
    corr = np.interp(hw, m, smooth)
    return (hw - corr - cal.intercept) / cal.hw_ns_per_marker * nominal_ns


def load_marker_ns(paths, event):
    """int64 ns stamps of `event` from any id,sec,ns,event,... CSV.

    Integer ns throughout: epoch seconds are ~1.79e9, so sec + ns/1e9 in float64
    loses the nanosecond digit.

    Corrupt rows are skipped, not fatal. A capture has twice come back with a
    block scrambled in place, which puts arbitrary bytes mid-file; the default
    locale codec raises UnicodeDecodeError on those, so read as latin-1 (total,
    never throws) and reject any row whose numeric fields are not pure digits."""
    want = str(int(event))
    out, skipped = [], 0
    for p in paths:
        with open(p, newline="", encoding="latin-1") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if len(row) < 4 or not (row[1].isdigit() and row[2].isdigit()
                                        and row[3].isdigit()):
                    skipped += 1
                    continue
                if row[3] != want:
                    continue
                s, n = int(row[1]), int(row[2])
                if s:                       # sec==0 is the strict-zero UNSYNC stamp
                    out.append(s * NS + n)
    return np.unique(np.array(out, dtype=np.int64)), skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv", nargs="+", help="capture CSVs (globs ok)")
    ap.add_argument("--event", default="8F", help="marker event code, hex (default 8F)")
    ap.add_argument("--nominal", type=float, default=1.0,
                    help="nominal marker period, seconds (default 1.0)")
    ap.add_argument("--window", type=float, default=3600.0,
                    help="stability window, seconds (default 3600)")
    ap.add_argument("--max-gap", type=float, default=60.0,
                    help="gap that splits capture sessions, seconds (default 60); the "
                         "leading stale-FIFO block is separated this way")
    args = ap.parse_args(argv)

    paths = [q for p in args.csv for q in (glob.glob(p) or [p])]
    code = int(args.event, 16)
    nominal = args.nominal * NS
    t_all, skipped = load_marker_ns(sorted(paths), code)
    if t_all.size < 4:
        print("!! only %d $%02X markers found in %d file(s)" % (t_all.size, code, len(paths)))
        return 2

    sessions = split_sessions(t_all, args.max_gap)
    t = max(sessions, key=lambda s: s[-1] - s[0] if s.size > 1 else 0)
    cal = calibrate(t, nominal)

    print("# files            : %d   (%d unreadable rows skipped)" % (len(paths), skipped))
    print("# marker           : $%02X, nominal %g s" % (code, args.nominal))
    if len(sessions) > 1:
        print("# sessions         : %d separated by >%.0f s gaps; fitting the longest"
              % (len(sessions), args.max_gap))
        for s in sessions:
            span = (s[-1] - s[0]) / NS
            print("#     %6d markers over %10.1f s%s"
                  % (s.size, span, "   <-- used" if s is t else "   (skipped)"))
    print("# markers found    : %d over %.1f s (%.3f h)" % (cal.n, cal.span_s, cal.span_s / 3600))
    print("# expected at rate : %.0f   (missing %.0f)"
          % (cal.span_s / args.nominal + 1, cal.span_s / args.nominal + 1 - cal.n))
    print("# dropped intervals: %d   markers rejected from fit: %d" % (cal.dropouts, cal.rejected))
    print("#")
    print("# mean interval    : %.9f s" % (cal.hw_ns_per_marker / NS))
    print("# FREQUENCY OFFSET : %+.5f ppm  (stamp clock vs GPS)" % cal.ppm)
    print("# scale factor     : x %.12f   (true = measured / (1 + ppm*1e-6))"
          % (1.0 / (1.0 + cal.ppm * 1e-6)))

    wins = calibrate_windowed(t, nominal, window_s=args.window)
    if len(wins) >= 2:
        p = np.array([w.ppm for w in wins])
        print("#")
        print("# stability over %d windows of %.0f s:" % (len(p), args.window))
        print("#   ppm  mean %+.5f  sd %.5f  min %+.5f  max %+.5f  ptp %.5f"
              % (p.mean(), p.std(), p.min(), p.max(), np.ptp(p)))
        print("#")
        print("# residual after a SINGLE scale factor : sd %.1f us  ptp %.1f us"
              % (cal.resid_sd / 1e3, np.ptp(cal.resid) / 1e3))
        print("#   ^ this is accumulated oscillator wander, not jitter. to_true_ns_tracked()")
        print("#     follows it out; to_true_ns() with the constant above leaves it in.")
        print("# short-term marker jitter (per-window resid): sd %.1f ns"
              % np.median([w.resid_sd for w in wins]))
    else:
        print("# fit residual     : sd %.1f ns   ptp %.1f ns" % (cal.resid_sd, np.ptp(cal.resid)))

    print("#")
    if abs(cal.ppm) < 0.01:
        print("# VERDICT: reference is disciplined (|offset| < 0.01 ppm).")
    else:
        print("# VERDICT: reference is NOT GPS-disciplined (%+.2f ppm). Expect free-running"
              % cal.ppm)
        print("#          oscillator behaviour; apply the scale factor above, or slave the")
        print("#          WR node to a GPS-locked grandmaster to remove it in hardware.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
