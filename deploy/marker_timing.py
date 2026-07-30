"""Timestamp-accuracy analysis from a periodic TCLK marker (default $02).

The $02 event is emitted every N TCLK cycles (default 50,000,000 at 10 MHz =
5.0 s nominal). Because the marker is *exactly* periodic in the TCLK domain,
the scatter of its White-Rabbit-timestamped inter-arrival times is a
ground-truth-free measure of timestamp precision, and the mean interval reveals
the TCLK-vs-GPS frequency offset. Slow structure in the fit residuals is real
clock/temperature wander.

Reads events-*.csv (id,sec,ns,event,data). All timing math is done in *integer*
nanoseconds (sec*1e9 + ns): epoch seconds are ~1.78e9, so sec+ns/1e9 in float64
silently loses the nanosecond digit -- do not do that here.

    python marker_timing.py ../../events-tclk-20260716.csv \
        ../../events-tclk-20260717.csv -o marker_timing.png
"""
import argparse
import csv

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

NS = 1_000_000_000
INK, MUTED, SURF = "#1b1b1b", "#6f6f6f", "#ffffff"
AZURE, WARM = "#2b8cc4", "#c4562b"
plt.rcParams.update({"font.family": "DejaVu Sans", "svg.fonttype": "none"})


def load_marker_ns(path, event):
    """Return sorted, de-duplicated int64 nanosecond stamps of `event`."""
    raw_ns, tns = [], []
    ev = str(event)
    with open(path, newline="") as f:
        r = csv.reader(f)
        next(r, None)  # header
        for row in r:
            if len(row) < 4 or row[3] != ev:
                continue
            s, n = int(row[1]), int(row[2])
            raw_ns.append(n)
            tns.append(s * NS + n)
    t = np.unique(np.array(tns, dtype=np.int64))  # sorted + dedup
    lsb = int(np.gcd.reduce(np.array(raw_ns, dtype=np.int64))) if raw_ns else 0
    return t, lsb


def analyze(tns, nominal_ns):
    """Fit t = a + period*k over marker cycle index; return the pieces."""
    d = np.diff(tns)                                   # int64 ns intervals
    cyc = np.rint(d / nominal_ns).astype(np.int64)     # marker-cycles per gap
    cyc = np.maximum(cyc, 1)
    single = cyc == 1                                  # clean back-to-back
    k = np.concatenate([[0], np.cumsum(cyc)])          # cumulative cycle index

    y = (tns - tns[0]).astype(np.float64)              # exact: < 2^52 ns
    A = np.vstack([np.ones_like(k), k]).astype(np.float64).T
    (a0, period), *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - (a0 + period * k)                      # ns, timestamp error
    return d, cyc, single, k, period, resid


def segments(single, min_markers=3):
    """Marker slices [i0, i1) of each maximal gap-free (all-single) run.

    `single[j]` is True when interval j spans exactly one marker cycle, so a run
    of True intervals ending just before position j covers markers [start, j].
    """
    segs, start = [], 0
    for j, ok in enumerate(np.append(single, False)):
        if not ok:
            if (j + 1 - start) >= min_markers:
                segs.append((start, j + 1))
            start = j + 1
    return segs


def detrend_segment(tns_seg):
    """Local linear fit within one gap-free run; return residual (ns) vs a line."""
    kk = np.arange(len(tns_seg), dtype=np.float64)
    y = (tns_seg - tns_seg[0]).astype(np.float64)
    (a0, b), *_ = np.linalg.lstsq(np.vstack([np.ones_like(kk), kk]).T, y, rcond=None)
    return y - (a0 + b * kk), b


def overlapping_adev(phase_s, tau0):
    """Overlapping Allan deviation from phase (time-error) samples, seconds."""
    x = np.asarray(phase_s, float)
    N = len(x)
    taus, devs = [], []
    m = 1
    while m <= (N - 1) // 2:
        d2 = x[2 * m:] - 2 * x[m:-m] + x[:-2 * m]
        devs.append(np.sqrt(np.mean(d2 ** 2) / 2) / (m * tau0))
        taus.append(m * tau0)
        m *= 2
    return np.array(taus), np.array(devs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="+")
    ap.add_argument("-o", "--out", default="marker_timing.png")
    ap.add_argument("--event", type=int, default=2, help="marker event code (decimal)")
    ap.add_argument("--cycles", type=int, default=50_000_000, help="TCLK cycles per marker")
    ap.add_argument("--tclk-hz", type=float, default=10e6, help="nominal TCLK frequency")
    a = ap.parse_args()

    nominal_ns = a.cycles / a.tclk_hz * NS             # 5.0e9 ns default
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.4), dpi=150, facecolor=SURF)

    print(f"marker $%02X, nominal %d cycles / %.6g Hz = %.9f s\n"
          % (a.event, a.cycles, a.tclk_hz, nominal_ns / NS))
    print(f"{'file':<34}{'markers':>9}{'gaps':>6}{'period (s)':>16}"
          f"{'offset (ppm)':>13}{'stamp jit (ns)':>16}{'wander p2p (us)':>17}")

    colors = [AZURE, WARM, "#2ba86f", "#8a2be2"]
    for idx, path in enumerate(a.csv):
        tns, lsb = load_marker_ns(path, a.event)
        if len(tns) < 3:
            print(f"{path:<34}  <3 markers, skipped")
            continue
        d, cyc, single, k, period, resid = analyze(tns, nominal_ns)
        segs = segments(single)

        ppm = (period / nominal_ns - 1) * 1e6           # global long-baseline fit
        # per-stamp short-term jitter: 2nd difference of stamps removes the smooth
        # drift locally; var(t_{k+1}-2t_k+t_{k-1}) = 6*var(stamp) for iid noise.
        d2 = np.concatenate([np.diff(np.diff(tns[i0:i1].astype(np.float64)))
                             for i0, i1 in segs]) if segs else np.array([0.0])
        stamp_jit = float(np.std(d2) / np.sqrt(6))
        # slow wander: worst peak-to-peak of the locally detrended residual
        wander = max((float(np.ptp(detrend_segment(tns[i0:i1])[0]))
                     for i0, i1 in segs), default=0.0)
        elapsed_h = (tns[-1] - tns[0]) / NS / 3600
        name = path.replace("\\", "/").split("/")[-1]
        print(f"{name:<34}{len(tns):>9}{int((cyc>1).sum()):>6}{period/NS:>16.9f}"
              f"{ppm:>13.3f}{stamp_jit:>16.1f}{wander/1e3:>17.1f}"
              f"   (LSB {lsb} ns, {elapsed_h:.1f} h)")

        c = colors[idx % len(colors)]
        th_all = (tns - tns[0]) / NS / 3600
        # (A) per-stamp second difference (drift removed locally), clipped to +-500
        axes[0].hist(np.clip(d2, -500, 500), bins=80, range=(-500, 500),
                     histtype="step", lw=1.8, color=c, label=name)
        # (B) locally-detrended residual vs elapsed hours (per gap-free segment)
        for i0, i1 in segs:
            rseg, _ = detrend_segment(tns[i0:i1])
            axes[1].plot(th_all[i0:i1], rseg / 1e3, ".", ms=2.5, color=c, alpha=0.6)
        # (C) Allan deviation over the longest gap-free segment
        i0, i1 = max(segs, key=lambda s: s[1] - s[0], default=(0, 0))
        if i1 - i0 > 8:
            rseg, _ = detrend_segment(tns[i0:i1])
            taus, devs = overlapping_adev(rseg / NS, period / NS)
            axes[2].loglog(taus, devs, "o-", ms=4, color=c, label=name)

    axes[0].set_title("Short-term stamp jitter", color=INK, fontweight="bold", loc="left")
    axes[0].set_xlabel("stamp 2nd difference  (ns)", color=MUTED)
    axes[0].set_ylabel("count", color=MUTED)
    axes[0].legend(fontsize=8, frameon=False)

    axes[1].set_title("TCLK-vs-GPS wander over the run", color=INK, fontweight="bold", loc="left")
    axes[1].set_xlabel("elapsed time  (hours)", color=MUTED)
    axes[1].set_ylabel("locally-detrended residual  (us)", color=MUTED)
    axes[1].axhline(0, color=MUTED, lw=0.6)

    axes[2].set_title("Allan deviation (longest gap-free run)", color=INK, fontweight="bold", loc="left")
    axes[2].set_xlabel("averaging time  (s)", color=MUTED)
    axes[2].set_ylabel("overlapping ADEV", color=MUTED)
    axes[2].legend(fontsize=8, frameon=False)
    axes[2].grid(True, which="both", alpha=0.25)

    fig.tight_layout()
    fig.savefig(a.out, dpi=150, facecolor=SURF)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
