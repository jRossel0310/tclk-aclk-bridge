"""On-disk JSONL stats log for the KR260 capture publisher.

build_snapshot() assembles one snapshot record (pure); StatsLog appends records as JSON
lines, line-buffered and flushed so a crash never loses a completed snapshot. The report
tool (stats_report.py) reads these files back. Nothing here imports redis or matplotlib,
so it loads on any machine and the unit tests need no hardware."""
import json
import time


def now_utc():
    """Wall-clock UTC of the snapshot, as an ISO-8601 'Z' string (seconds resolution)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sw_counters(drained, unsync, sink_stats):
    """Software-side counters for a snapshot: the publisher's drained/unsync plus the
    RedisSink stats (published/queued/queue_dropped/redis_dropped/reconnects)."""
    return {
        "drained": drained, "unsync": unsync,
        "published": sink_stats["published"], "queued": sink_stats["queued"],
        "queue_dropped": sink_stats["queue_dropped"],
        "redis_dropped": sink_stats["redis_dropped"],
        "reconnects": sink_stats["reconnects"],
    }


def build_snapshot(utc, mono, src, hw, sw):
    """Assemble one snapshot record. hw/sw are copied so a later in-place mutation of the
    caller's counter dicts cannot rewrite an already-built record."""
    return {"utc": utc, "mono": mono, "src": src, "hw": dict(hw), "sw": dict(sw)}


class StatsLog:
    """Append-only JSONL sink. One record per line, flushed immediately so a completed
    snapshot survives a crash."""

    def __init__(self, path):
        self.path = path
        self._f = open(path, "a", buffering=1)      # line-buffered append

    def write(self, record):
        self._f.write(json.dumps(record) + "\n")
        self._f.flush()

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass
