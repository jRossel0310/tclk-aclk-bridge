"""Unit tests for stats_report reconciliation (no hardware, no Redis).
Run: python test_stats_report.py   or   pytest deploy -q"""
import json
import os
import tempfile

from stats_report import load_snapshots, group_by_src, reconcile, format_report


def _snap(src, mono, ev, err=0, nul=0, filt=0, ovf=0, lock=1,
          drained=0, unsync=0, published=0, qd=0, rd=0, rec=0, queued=0):
    return {"utc": "t", "mono": mono, "src": src,
            "hw": {"event_count": ev, "null_count": nul, "error_count": err,
                   "filtered_count": filt, "overflow": ovf, "lock": lock, "heartbeat": 0},
            "sw": {"drained": drained, "unsync": unsync, "published": published,
                   "queued": queued, "queue_dropped": qd, "redis_dropped": rd, "reconnects": rec}}


def test_reconcile_basic_deltas():
    snaps = [
        _snap("tclk", 0.0, ev=100, err=1),                              # baseline
        _snap("tclk", 60.0, ev=1100, err=4, nul=0, filt=2,
              drained=980, unsync=18, published=975, qd=1, rd=1, rec=2),
    ]
    r = reconcile(snaps)
    assert r["src"] == "tclk" and r["snapshots"] == 2 and r["duration_s"] == 60.0
    assert r["decoded"] == 1000                    # 1100 - 100
    assert r["failed_crc"] == 3                     # 4 - 1
    assert r["filtered"] == 2 and r["nulls"] == 0
    assert r["published"] == 975
    assert r["missed_hw"] == 1000 - 980 - 18        # decodedDelta - drained - unsync = 2
    assert r["missed_pub"] == 2                      # qd + rd
    assert r["reconnects"] == 2 and r["unsync"] == 18
    assert r["overflow_ever"] == 0 and r["lock_lost"] == 0
    assert "clean" in r["xcheck"]                   # missed_hw within FIFO residual


def test_reconcile_overflow_crosscheck_flags():
    snaps = [
        _snap("aclk", 0.0, ev=0),
        _snap("aclk", 10.0, ev=5000, ovf=1, drained=4000, unsync=0, published=4000),
    ]
    r = reconcile(snaps)
    assert r["overflow_ever"] == 1
    assert r["missed_hw"] == 1000                    # 5000 - 4000 - 0
    assert "overflow" in r["xcheck"]                 # bit set: loss expected


def test_reconcile_warns_missed_without_overflow():
    snaps = [
        _snap("tclk", 0.0, ev=0),
        _snap("tclk", 10.0, ev=5000, ovf=0, drained=4000, unsync=0, published=4000),
    ]
    r = reconcile(snaps)
    assert r["overflow_ever"] == 0 and r["missed_hw"] == 1000
    assert r["xcheck"].startswith("WARN")            # loss but overflow bit never set


def test_reconcile_lock_lost_if_any_snapshot_unlocked():
    snaps = [_snap("tclk", 0.0, ev=0, lock=1),
             _snap("tclk", 5.0, ev=10, lock=0),
             _snap("tclk", 10.0, ev=20, lock=1)]
    assert reconcile(snaps)["lock_lost"] == 1


def test_group_by_src_splits_and_preserves_order():
    snaps = [_snap("tclk", 0.0, ev=0), _snap("aclk", 0.0, ev=0),
             _snap("tclk", 1.0, ev=5)]
    g = group_by_src(snaps)
    assert set(g) == {"tclk", "aclk"}
    assert [s["mono"] for s in g["tclk"]] == [0.0, 1.0]


def test_load_snapshots_roundtrip():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        rows = [_snap("tclk", 0.0, ev=0), _snap("tclk", 1.0, ev=9)]
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
            f.write("\n")                            # blank line must be skipped
        assert load_snapshots(path) == rows
    finally:
        os.remove(path)


def test_format_report_is_readable():
    r = reconcile([_snap("tclk", 0.0, ev=0),
                   _snap("tclk", 60.0, ev=600, drained=600, published=600)])
    text = format_report(r)
    assert "tclk" in text and "published" in text and "failed CRC" in text


def test_reconcile_reanchors_to_most_recent_run():
    # Run 1: drained climbs 0 -> 490 while event_count goes 100 -> 600.
    # Run 2 (appended, sw counters reset): drained 0 -> 295 while event_count 600 -> 900.
    snaps = [
        _snap("tclk", 0.0, ev=100, drained=0),
        _snap("tclk", 60.0, ev=600, drained=490),
        _snap("tclk", 61.0, ev=600, drained=0),      # run 2 baseline: drained reset
        _snap("tclk", 121.0, ev=900, drained=295),
    ]
    r = reconcile(snaps)
    assert r["runs_in_log"] == 2
    assert r["decoded"] == 300          # 900 - 600 (run 2 only), NOT 900 - 100
    assert r["missed_hw"] == 5          # 300 - 295 - 0
    assert r["snapshots"] == 2          # only the last run's snapshots


def test_reconcile_surfaces_queued_and_ledger_ok():
    # Redis backlog leaves 400 events in the queue at the last snapshot.
    snaps = [
        _snap("tclk", 0.0, ev=0),
        _snap("tclk", 60.0, ev=1000, drained=1000, published=600, queued=400),
    ]
    r = reconcile(snaps)
    assert r["queued"] == 400
    assert r["ledger_ok"] is True       # 600 + 0 + 400 + 0 + 0 == 1000 decoded
    text = format_report(r)
    assert "undelivered" in text and "ledger check" in text and "OK" in text


def test_reconcile_ledger_mismatch_flagged():
    # Counters that do not close (600 events unaccounted, beyond the 512 tolerance).
    snaps = [
        _snap("tclk", 0.0, ev=0),
        _snap("tclk", 60.0, ev=1000, drained=1000, published=300, queued=100),
    ]
    r = reconcile(snaps)
    assert r["ledger_ok"] is False      # 300 + 0 + 100 + 0 + 0 = 400 vs decoded 1000
    assert "MISMATCH" in format_report(r)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all stats_report tests passed")
