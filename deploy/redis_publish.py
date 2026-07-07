#!/usr/bin/env python3
"""Publish WR-timestamped readout events to local Redis (KR260 namespace).

Drains one UIO readout (TCLK or ACLK), drops UNSYNC events (ts==0), and submits a
record per event to a background RedisSink. Per event the sink writes:
  XADD KR260:<src> <event-time-ms>-* {sec, ns, utc, event, data, is_tclk, has_data, src}
  HSET KR260:event:<src>:0x<CODE> {sec, ns, utc, data}   (latest event for that code)
  HINCRBY KR260:event:<src>:0x<CODE> count 1
So consumers read the time-ordered stream OR look an event code up directly. The sink
also maintains KR260:status / KR260:watchdog liveness keys. Two threads: this (main)
thread drains the FIFO and enqueues; the sink's writer thread talks to Redis, so a Redis
stall never stalls the hardware FIFO drain.

    sudo python3 redis_publish.py /dev/uio4 --src tclk
    sudo python3 redis_publish.py /dev/uio5 --src aclk

Ctrl-C to stop (flushes the queue, prints final stats). Needs redis-py on the board
(pip install -r requirements-board.txt) and a running redis-server."""
import sys

import readout_common as rc
from readout_common import say, wr_split, wr_utc
from redis_sink import RedisSink


def event_fields(event, flags, data, ts, src):
    """Map a decoded event to the Redis Stream field dict (all string values)."""
    sec, ns = wr_split(ts)
    return {
        "sec": str(sec), "ns": str(ns), "utc": wr_utc(ts),
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
    the event time in ms (the sink applies the monotonic guard)."""
    sec, nsec = wr_split(ts)
    return {
        "stream":       "%s:%s" % (ns, src),
        "id_ms":        sec * 1000 + nsec // 1_000_000,
        "fields":       event_fields(event, flags, data, ts, src),
        "index_key":    "%s:event:%s:0x%02X" % (ns, src, event),
        "index_fields": {"sec": str(sec), "ns": str(nsec),
                         "utc": wr_utc(ts), "data": str(data)},
    }


def main(argv):
    rc.line_buffer_stdout()
    pos, flags = rc.parse_args(
        argv, value_flags=("--src", "--namespace", "--redis-host", "--redis-port",
                           "--maxlen", "--queue-size"))
    dev    = pos[0] if pos else "/dev/uio4"
    src    = flags.get("--src", "tclk")
    ns     = flags.get("--namespace", "KR260")
    host   = flags.get("--redis-host", "127.0.0.1")
    port   = int(flags.get("--redis-port", "6379"))
    maxlen = int(flags.get("--maxlen", "1000000"))
    qsize  = int(flags.get("--queue-size", "100000"))

    io = rc.open_dev(dev)
    sink = RedisSink(host=host, port=port, maxlen=maxlen, queue_size=qsize,
                     status_key="%s:status" % ns, watchdog_key="%s:watchdog" % ns)
    sink.start()
    stream = "%s:%s" % (ns, src)
    say("# publishing %s events from %s to Redis stream '%s' (%s:%d). Ctrl-C to stop."
        % (src, dev, stream, host, port))

    drained = [0]

    def on_event(e):
        if not should_publish(e["ts"]):        # UNSYNC: dropped by design
            return
        drained[0] += 1
        sink.submit(build_record(ns, src, e["event"], e["flags"], e["data"], e["ts"]))

    def stats_line():
        s = sink.stats()
        say("[stats] drained=%d published=%d queued=%d queue_dropped=%d "
            "redis_dropped=%d reconnects=%d" % (
                drained[0], s["published"], s["queued"], s["queue_dropped"],
                s["redis_dropped"], s["reconnects"]))

    try:
        rc.drain_events(io, on_event, idle_cb=stats_line)
    finally:
        say("\n# stopping; flushing queue ...")
        sink.stop(timeout=3.0)
        stats_line()


if __name__ == "__main__":
    main(sys.argv[1:])
