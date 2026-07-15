"""Unit tests for stats_log (no hardware, no Redis, no matplotlib).
Run: python test_stats_log.py   or   pytest deploy -q"""
import json
import os
import tempfile

from stats_log import now_utc, sw_counters, build_snapshot, StatsLog


def test_now_utc_format():
    s = now_utc()
    assert s.endswith("Z") and "T" in s and s[4] == "-" and s[7] == "-"


def test_sw_counters_maps_sink_stats():
    stats = {"published": 100, "queued": 2, "queue_dropped": 3,
             "redis_dropped": 4, "reconnects": 5}
    sw = sw_counters(drained=90, unsync=10, sink_stats=stats)
    assert sw == {"drained": 90, "unsync": 10, "published": 100, "queued": 2,
                  "queue_dropped": 3, "redis_dropped": 4, "reconnects": 5}


def test_build_snapshot_schema_and_copies():
    hw = {"event_count": 10, "null_count": 0, "error_count": 1, "filtered_count": 0,
          "overflow": 0, "lock": 1, "heartbeat": 99}
    sw = {"drained": 9, "unsync": 1, "published": 9, "queued": 0,
          "queue_dropped": 0, "redis_dropped": 0, "reconnects": 0}
    rec = build_snapshot("2026-07-15T00:00:00Z", 12.5, "tclk", hw, sw)
    assert rec["utc"] == "2026-07-15T00:00:00Z"
    assert rec["mono"] == 12.5 and rec["src"] == "tclk"
    assert rec["hw"] == hw and rec["sw"] == sw
    hw["event_count"] = 999                      # build_snapshot must have copied hw
    assert rec["hw"]["event_count"] == 10


def test_statslog_roundtrip():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        log = StatsLog(path)
        r1 = build_snapshot("t1", 1.0, "tclk", {"a": 1}, {"b": 2})
        r2 = build_snapshot("t2", 2.0, "tclk", {"a": 3}, {"b": 4})
        log.write(r1)
        log.write(r2)
        log.close()
        with open(path) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert lines == [r1, r2]
    finally:
        os.remove(path)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all stats_log tests passed")
