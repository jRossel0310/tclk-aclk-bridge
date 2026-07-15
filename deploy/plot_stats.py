#!/usr/bin/env python3
"""Plot a KR260 capture stats log (JSONL) as time-series PNGs. Runs on the PC (matplotlib
lives here, not on the board). Copy the logs over first, e.g.:
    scp ubuntu@kr260:~/aclk_pipeline/stats-*.jsonl .
    python plot_stats.py stats-tclk.jsonl stats-aclk.jsonl

Per source it derives per-interval rates from consecutive-snapshot deltas divided by the
monotonic-time delta, and saves plot-<src>.png with four stacked panels: event rate,
CRC-error rate, cumulative missed (HW overflow + publisher drops), and WR-lock/overflow."""
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load_snapshots(path):
    snaps = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                snaps.append(json.loads(line))
    return snaps


def group_by_src(snaps):
    groups = {}
    for s in snaps:
        groups.setdefault(s["src"], []).append(s)
    return groups


def series(snaps):
    """Build plot arrays from time-ordered snapshots of one source. Rates are per-interval
    deltas / dt; the first point (no predecessor) is dropped for rate panels."""
    t0 = snaps[0]["mono"]
    hrs, ev_rate, err_rate, missed, lock, ovf = [], [], [], [], [], []
    for i in range(1, len(snaps)):
        a, b = snaps[i - 1], snaps[i]
        dt = b["mono"] - a["mono"]
        if dt <= 0:
            continue
        hrs.append((b["mono"] - t0) / 3600.0)
        ev_rate.append((b["hw"]["event_count"] - a["hw"]["event_count"]) / dt)
        err_rate.append((b["hw"]["error_count"] - a["hw"]["error_count"]) / dt)
        decoded = b["hw"]["event_count"] - snaps[0]["hw"]["event_count"]
        missed.append(decoded - b["sw"]["drained"] - b["sw"]["unsync"]
                      + b["sw"]["queue_dropped"] + b["sw"]["redis_dropped"])
        lock.append(b["hw"]["lock"])
        ovf.append(b["hw"]["overflow"])
    return hrs, ev_rate, err_rate, missed, lock, ovf


def plot_src(src, snaps):
    hrs, ev_rate, err_rate, missed, lock, ovf = series(snaps)
    if not hrs:
        print("skip %s: need at least two snapshots" % src)
        return
    fig, ax = plt.subplots(4, 1, figsize=(10, 11), sharex=True)
    fig.suptitle("KR260 capture: %s" % src)
    ax[0].plot(hrs, ev_rate, color="tab:blue")
    ax[0].set_ylabel("events/s")
    ax[0].grid(True, alpha=0.3)
    ax[1].plot(hrs, err_rate, color="tab:red")
    ax[1].set_ylabel("CRC errors/s")
    ax[1].grid(True, alpha=0.3)
    ax[2].plot(hrs, missed, color="tab:orange")
    ax[2].set_ylabel("cumulative missed")
    ax[2].grid(True, alpha=0.3)
    ax[3].plot(hrs, lock, label="WR lock", color="tab:green")
    ax[3].plot(hrs, ovf, label="overflow", color="tab:red", linestyle="--")
    ax[3].set_ylabel("status")
    ax[3].set_ylim(-0.1, 1.1)
    ax[3].set_xlabel("hours since start")
    ax[3].legend(loc="center right")
    ax[3].grid(True, alpha=0.3)
    out = "plot-%s.png" % src
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print("wrote " + out)


def main(argv):
    if not argv:
        print("usage: plot_stats.py stats-tclk.jsonl [stats-aclk.jsonl ...]")
        return
    all_snaps = []
    for path in argv:
        all_snaps.extend(load_snapshots(path))
    for src, snaps in sorted(group_by_src(all_snaps).items()):
        plot_src(src, snaps)


if __name__ == "__main__":
    main(sys.argv[1:])
