#!/usr/bin/env python3
"""Publish WR-timestamped readout events to local Redis (KR260 namespace).

Drains one UIO readout (TCLK or ACLK), drops UNSYNC events (ts==0), and submits a
record per event to a background RedisSink. The sink writes:
  per event:                 XADD KR260:<src> <event-time-ms>-*
                                  {sec, ns, event, data, is_tclk, has_data, src}
  per event code, per batch: HSET KR260:event:<src>:0x<CODE> {sec, ns, utc, data}
                             HINCRBY KR260:event:<src>:0x<CODE> count <n-in-batch>
So consumers read the time-ordered stream OR look an event code up directly. (No
per-entry utc on the stream: derive it from sec/ns; the index hash keeps one.) The sink
also maintains KR260:status / KR260:watchdog liveness keys. Two threads: this (main)
thread drains the FIFO and enqueues; the sink's writer thread talks to Redis, so a Redis
stall never stalls the hardware FIFO drain.

    sudo python3 redis_publish.py /dev/uio4 --src tclk
    sudo python3 redis_publish.py /dev/uio5 --src aclk

Ctrl-C to stop (flushes the queue, prints final stats). Needs redis-py on the board
(pip install -r requirements-board.txt) and a running redis-server."""
import struct
import sys
import time
from functools import lru_cache

import readout_common as rc
from readout_common import say, wr_split, wr_utc, read_hw_counters
from redis_sink import RedisSink
from stats_log import StatsLog, build_snapshot, sw_counters, now_utc


class PublisherState:
    """Drain-side counters. note(ts) classifies one event: a real event increments
    `drained` and returns True (publish it); an UNSYNC event (ts==0, WR not locked when
    stamped) increments `unsync` and returns False (dropped by design, see
    should_publish)."""

    def __init__(self):
        self.drained = 0
        self.unsync = 0

    def note(self, ts):
        if should_publish(ts):
            self.drained += 1
            return True
        self.unsync += 1
        return False


@lru_cache(maxsize=None)
def _stream_key(ns, src):
    return "{%s}:%s" % (ns, src)


@lru_cache(maxsize=4096)
def _index_key(ns, src, event):
    return "{%s}:event:%s:0x%02X" % (ns, src, event)


def event_fields(event, flags, data, ts, src):
    """Map a decoded event to the Redis Stream field dict. Carries the mandatory
    RedisAdapter `_` primary payload (little-endian <IIIHB>: sec u32, ns u32, data
    u32, event u16, flags u8) plus the human-readable extras. A generic RedisAdapter
    consumer reads only `_`; our archiver/redis-cli read the text fields. No per-entry
    `utc`: it duplicated sec/ns on every entry and was pure hot-path cost; consumers
    derive UTC from sec/ns and the per-code index hash keeps a human-readable utc."""
    sec, ns = wr_split(ts)
    return {
        "_": struct.pack("<IIIHB", sec, ns, data, event, flags),
        "sec": str(sec), "ns": str(ns),
        "event": str(event), "data": str(data),
        "is_tclk": str((flags >> 1) & 1), "has_data": str(flags & 1),
        "src": src,
    }


def should_publish(ts):
    """UNSYNC events (ts==0, WR timebase not locked when stamped) are not published."""
    return ts != 0


def build_record(ns, src, event, flags, data, ts):
    """Build the sink record for one event: the per-source stream write plus the
    per-event-code index write, all under the `ns` namespace. The stream entry ID is
    the event time in ms (the sink applies the monotonic guard). Keys are lru_cached
    and the index shares the fields dict's strings: this runs per event on the drain
    thread's hot path."""
    sec, nsec = wr_split(ts)
    fields = event_fields(event, flags, data, ts, src)
    return {
        "stream":       _stream_key(ns, src),
        "id_ms":        sec * 1000 + nsec // 1_000_000,
        "fields":       fields,
        "index_key":    _index_key(ns, src, event),
        "index_fields": {"sec": fields["sec"], "ns": fields["ns"],
                         "utc": wr_utc(ts), "data": fields["data"]},
    }


def main(argv):
    rc.line_buffer_stdout()
    pos, flags = rc.parse_args(
        argv, value_flags=("--src", "--namespace", "--redis-host", "--redis-port",
                           "--maxlen", "--queue-size", "--statlog", "--snapshot-interval",
                           "--drop"))
    dev      = pos[0] if pos else "/dev/uio4"
    src      = flags.get("--src", "tclk")
    ns       = flags.get("--namespace", "KR260")
    host     = flags.get("--redis-host", "127.0.0.1")
    port     = int(flags.get("--redis-port", "6379"))
    maxlen   = int(flags.get("--maxlen", "1000000"))
    qsize    = int(flags.get("--queue-size", "100000"))
    statpath = flags.get("--statlog", "stats-%s.jsonl" % src)
    interval = float(flags.get("--snapshot-interval", "60"))

    io = rc.open_dev(dev)
    # Suppress flood codes (e.g. the 720 Hz 0x07) in the PL before they ever reach the FIFO,
    # so a sustained rate above the PS drain ceiling cannot overflow it. The drop-mask is a
    # hardware register cleared on every bitstream reload, so re-applying it here (once per
    # launch) makes the drop survive a PL reprogram with no manual step. See capture.md.
    rc.apply_drop_filter(io, rc.parse_drop_codes(flags.get("--drop", "")))
    sink = RedisSink(host=host, port=port, maxlen=maxlen, queue_size=qsize,
                     status_key="{%s}:status" % ns, watchdog_key="{%s}:watchdog" % ns)
    sink.start()
    stream = "%s:%s" % (ns, src)
    statlog = StatsLog(statpath)
    state = PublisherState()
    say("# publishing %s events from %s to Redis stream '%s' (%s:%d); stats -> %s every "
        "%gs. Ctrl-C to stop." % (src, dev, stream, host, port, statpath, interval))

    def on_event(e):
        if state.note(e["ts"]):
            sink.submit(build_record(ns, src, e["event"], e["flags"], e["data"], e["ts"]))

    # Runs on the drain thread (tick_cb): reads are cheap, but the statlog flush is on the
    # FIFO-drain critical path; a wedged disk could stall the drain (bounded, overflow-flagged).
    def snapshot():
        statlog.write(build_snapshot(
            now_utc(), time.monotonic(), src,
            read_hw_counters(io), sw_counters(state.drained, state.unsync, sink.stats())))

    def stats_line():
        s = sink.stats()
        say("[stats] drained=%d unsync=%d published=%d queued=%d queue_dropped=%d "
            "redis_dropped=%d reconnects=%d" % (
                state.drained, state.unsync, s["published"], s["queued"],
                s["queue_dropped"], s["redis_dropped"], s["reconnects"]))

    snapshot()                                     # baseline before draining
    try:
        rc.drain_events(io, on_event, idle_cb=stats_line,
                        tick_cb=snapshot, tick_s=interval)
    finally:
        say("\n# stopping; flushing queue ...")
        sink.stop(timeout=3.0)
        snapshot()                                 # final, post-flush
        statlog.close()
        stats_line()


if __name__ == "__main__":
    main(sys.argv[1:])
