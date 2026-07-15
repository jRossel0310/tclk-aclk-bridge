#!/usr/bin/env python3
"""Reconcile a KR260 capture stats log (JSONL) into a per-source report.

Pure reader: reads stats-*.jsonl only, never opens /dev/uio*, so it is safe to run while
the publishers are still live. Hardware counters are reconciled as baseline(first) to
last deltas; software counters are the last snapshot's cumulative totals.

    sudo python3 stats_report.py stats-tclk.jsonl stats-aclk.jsonl

For each source it prints: decoded (good events the PL enqueued), published, failed CRCs,
nulls/filtered, events missed at the hardware (FIFO overflow) and at the publisher
(queue+redis drops), reconnects, WR-lock health, and an overflow cross-check."""
import json
import sys

FIFO_RESIDUAL = 512     # FIFO depth (ADDR_WIDTH=9): tolerated |missed_hw| from residual.
                        # A run that STARTS against a full FIFO drains up to DEPTH events that
                        # were already counted before the baseline snapshot, so missed_hw can
                        # legitimately read as low as -DEPTH (a recovered backlog, not a loss).


def load_snapshots(path):
    """Read a JSONL stats log into a list of records (blank lines skipped)."""
    snaps = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                snaps.append(json.loads(line))
    return snaps


def group_by_src(snaps):
    """Group snapshots by their 'src' field, preserving per-source order."""
    groups = {}
    for s in snaps:
        groups.setdefault(s["src"], []).append(s)
    return groups


def _run_start(snaps):
    """Index where the most recent capture run begins in a possibly-appended log.
    Hardware counters keep climbing across a publisher restart but the software counters
    (drained/published/...) reset to 0, so a drop in `drained` between consecutive
    snapshots marks a new run. Returns the last such index (0 for a single-run log)."""
    start = 0
    for i in range(1, len(snaps)):
        if snaps[i]["sw"]["drained"] < snaps[i - 1]["sw"]["drained"]:
            start = i
    return start


def reconcile(snaps):
    """Reconcile time-ordered snapshots for ONE source into a summary dict. If the log
    holds several capture runs (appended together), only the MOST RECENT run is
    reconciled: its software counters reset at the run boundary, so mixing them with an
    older run's hardware baseline would report a phantom loss."""
    start = _run_start(snaps)
    run = snaps[start:]
    runs = sum(1 for i in range(1, len(snaps))
               if snaps[i]["sw"]["drained"] < snaps[i - 1]["sw"]["drained"]) + 1
    f, l = run[0], run[-1]
    hwf, hwl, swl = f["hw"], l["hw"], l["sw"]
    decoded = hwl["event_count"] - hwf["event_count"]
    missed_hw = decoded - swl["drained"] - swl["unsync"]
    missed_pub = swl["queue_dropped"] + swl["redis_dropped"]
    queued = swl["queued"]
    overflow_ever = 1 if any(s["hw"]["overflow"] for s in run) else 0
    lock_lost = 1 if any(not s["hw"]["lock"] for s in run) else 0

    if overflow_ever:
        xcheck = "overflow bit set: hardware confirms FIFO loss"
    elif missed_hw > FIFO_RESIDUAL:
        xcheck = "WARN: missed_hw=%d but overflow bit never set" % missed_hw
    elif missed_hw < -FIFO_RESIDUAL:
        xcheck = "WARN: missed_hw=%d negative beyond FIFO residual" % missed_hw
    else:
        xcheck = "clean (loss within FIFO residual, overflow bit clear)"

    # Ledger identity: every decoded event ends up published, dropped by the publisher,
    # still queued, dropped as UNSYNC, or lost to a FIFO overflow. At a clean final
    # snapshot (writer thread stopped) this closes exactly; allow FIFO_RESIDUAL slack for
    # an unclean kill mid-flight.
    accounted = swl["published"] + missed_pub + queued + swl["unsync"] + missed_hw
    return {
        "src": l["src"], "snapshots": len(run), "runs_in_log": runs,
        "duration_s": l["mono"] - f["mono"],
        "decoded": decoded, "published": swl["published"],
        "failed_crc": hwl["error_count"] - hwf["error_count"],
        "nulls": hwl["null_count"] - hwf["null_count"],
        "filtered": hwl["filtered_count"] - hwf["filtered_count"],
        "missed_hw": missed_hw, "missed_pub": missed_pub, "queued": queued,
        "reconnects": swl["reconnects"], "unsync": swl["unsync"],
        "overflow_ever": overflow_ever, "lock_lost": lock_lost,
        "accounted": accounted, "ledger_ok": abs(decoded - accounted) <= FIFO_RESIDUAL,
        "xcheck": xcheck,
    }


def format_report(r):
    """Human-readable per-source reconciliation block."""
    dur = r["duration_s"]
    rate = r["decoded"] / dur if dur > 0 else 0.0
    errpct = 100.0 * r["failed_crc"] / r["decoded"] if r["decoded"] else 0.0
    lines = [
        "=== %s ===" % r["src"],
        "  snapshots     : %d over %.0f s (%.2f h)" % (r["snapshots"], dur, dur / 3600.0),
        "  decoded (good): %d  (%.1f ev/s)" % (r["decoded"], rate),
        "  published     : %d" % r["published"],
        "  failed CRC    : %d  (%.3f%% of decoded)" % (r["failed_crc"], errpct),
        "  nulls/filtered: %d / %d" % (r["nulls"], r["filtered"]),
        "  missed @ HW   : %d  (FIFO overflow loss)" % r["missed_hw"],
        "  missed @ pub  : %d  (queue + redis drops)" % r["missed_pub"],
        "  undelivered   : %d  (still queued at stop; Redis backlog/down)" % r["queued"],
        "  reconnects    : %d" % r["reconnects"],
        "  unsync drops  : %d  (WR not locked when stamped)" % r["unsync"],
        "  WR lock lost  : %s" % ("YES" if r["lock_lost"] else "no"),
        "  overflow bit  : %s" % ("SET" if r["overflow_ever"] else "clear"),
        "  cross-check   : %s" % r["xcheck"],
        "  ledger check  : decoded=%d vs accounted=%d  (%s)" % (
            r["decoded"], r["accounted"], "OK" if r["ledger_ok"] else "MISMATCH"),
    ]
    if r["runs_in_log"] > 1:
        lines.append("  NOTE: log holds %d capture runs; reporting the most recent only."
                     % r["runs_in_log"])
    if r["snapshots"] < 2:
        lines.append("  NOTE: only one snapshot; run longer for a real delta.")
    return "\n".join(lines)


def main(argv):
    if not argv:
        print("usage: stats_report.py stats-tclk.jsonl [stats-aclk.jsonl ...]")
        return
    all_snaps = []
    for path in argv:
        all_snaps.extend(load_snapshots(path))
    if not all_snaps:
        print("no snapshots found in: " + " ".join(argv))
        return
    groups = group_by_src(all_snaps)
    for src in sorted(groups):
        print(format_report(reconcile(groups[src])))
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
