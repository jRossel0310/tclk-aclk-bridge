#!/usr/bin/env python3
"""Publish WR-timestamped readout events to a local Redis Stream.

Drains one UIO readout (TCLK or ACLK), drops UNSYNC events (ts==0), and submits the
rest to a background RedisSink that XADDs them to `--stream`. Two threads: this
(main) thread drains the FIFO and enqueues; the sink's writer thread talks to Redis,
so a Redis stall never stalls the hardware FIFO drain.

    sudo python3 redis_publish.py /dev/uio4 --stream events:tclk --src tclk
    sudo python3 redis_publish.py /dev/uio5 --stream events:aclk --src aclk

Ctrl-C to stop (flushes the queue, prints final stats).

Register block base 0x8000_0000 (TCLK) / 0x8001_0000 (ACLK); read via the UIO
mapping, same as tclk_read.py / aclkgt_read.py. Needs redis-py on the board
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


def main(argv):
    rc.line_buffer_stdout()
    pos, flags = rc.parse_args(
        argv, value_flags=("--stream", "--src", "--redis-host", "--redis-port",
                           "--maxlen", "--queue-size"))
    dev    = pos[0] if pos else "/dev/uio4"
    stream = flags.get("--stream", "events:tclk")
    src    = flags.get("--src", "tclk")
    host   = flags.get("--redis-host", "127.0.0.1")
    port   = int(flags.get("--redis-port", "6379"))
    maxlen = int(flags.get("--maxlen", "1000000"))
    qsize  = int(flags.get("--queue-size", "100000"))

    io = rc.open_dev(dev)
    sink = RedisSink(stream, host=host, port=port, maxlen=maxlen, queue_size=qsize)
    sink.start()
    say("# publishing %s events from %s to Redis stream '%s' (%s:%d). Ctrl-C to stop."
        % (src, dev, stream, host, port))

    drained = [0]

    def on_event(e):
        if not should_publish(e["ts"]):        # UNSYNC: dropped by design
            return
        drained[0] += 1
        sink.submit(event_fields(e["event"], e["flags"], e["data"], e["ts"], src))

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
