#!/usr/bin/env python3
"""Publish WR-timestamped TCLK readout events to Redis under the {TCLK} base key.

Drains one UIO readout, drops UNSYNC events (ts==0), and submits a record per event to
a background RedisSink. This follows the lab redis-clock-server deployment's contract
(RedisAdapter Protocol v1.0 key schema; see deploy/redis.md), so its redis-pvxs-ioc
serves our events as EPICS_REDIS_TCLK:RT:* PVs with no producer-specific code.

Three stream writes per event, all raw little-endian bytes in the single field `_`:

    XADD {TCLK}:<HEX>     _ = <q>  RA_Time of this occurrence (ns since the epoch)
    XADD {TCLK}:<HEX>_C   _ = <q>  occurrences of <HEX> since this process started
    XADD {TCLK}:STREAM    _ = <H>  the event code (the combined feed)

<HEX> is the event code as two uppercase zero-padded hex digits. Every entry ID is the
event's own RA_Time encoded as <ms>-<ns_within_ms>, so entries correlate across streams
by accelerator time; the sink bumps a tie or a backward step to last+1 ns so Redis's
strictly-increasing rule holds. Liveness is a field in the {TCLK}:watchdog hash with a
short expiry, refreshed while this process lives.

    sudo python3 redis_publish.py /dev/uio4 --src tclk

Ctrl-C to stop (flushes the queue, prints final stats). Needs redis-py >= 5.1 on the
board (pip install -r requirements-board.txt) and a reachable redis-server >= 7.4."""
import struct
import sys
import time

import readout_common as rc
from readout_common import say, wr_split, read_hw_counters
from redis_sink import RedisSink, resolve_auth
from stats_log import StatsLog, build_snapshot, sw_counters, now_utc

# Contract defaults for the lab deployment (bidaqt-tclk). {TCLK} is the base key that
# deployment reserves for this decoder; it trims event streams to ~10000 entries and
# expects each producer's watchdog field to expire 1 s after it stops refreshing.
DEFAULT_BASE = "TCLK"
DEFAULT_MAXLEN = 10_000
DEFAULT_WATCHDOG_TTL = 1                 # seconds; field expiry in the watchdog hash
DEFAULT_WATCHDOG_PERIOD = 0.9            # seconds between refreshes (must beat the TTL)
DEFAULT_WATCHDOG_FIELD = "kr260-tclk"    # this producer's field in the watchdog hash
BUILD_VERSION = "kr260-readout/1.0"      # the field's value: this producer's build

_PACK_I64 = struct.Struct("<q").pack     # {TCLK}:<HEX> and {TCLK}:<HEX>_C payloads
_PACK_U16 = struct.Struct("<H").pack     # {TCLK}:STREAM payload


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


def should_publish(ts):
    """UNSYNC events (ts==0, WR timebase not locked when stamped) are not published."""
    return ts != 0


class RecordBuilder:
    """Turns one decoded event into the sink record for its three stream writes.

    Owns the per-code occurrence counters, so counts are exact and in event order (this
    runs on the drain thread, which sees every event once). A fresh instance starts every
    counter at zero, which is the restart signal the deployment's consumers expect: a
    counter going backwards means the producer restarted, not corruption.

    Key strings are cached per code because this is the FIFO-drain hot path; the cache is
    bounded by the number of distinct event codes seen (<= 256 for TCLK)."""

    def __init__(self, base):
        self.base = base
        self._counts = {}                # event code -> occurrences so far
        self._keys = {}                  # event code -> (ts_key, count_key)
        self._stream_key = "{%s}:STREAM" % base

    def _code_keys(self, event):
        keys = self._keys.get(event)
        if keys is None:
            keys = ("{%s}:%02X" % (self.base, event),
                    "{%s}:%02X_C" % (self.base, event))
            self._keys[event] = keys
        return keys

    def build(self, event, ts):
        """One event -> {ra_time, writes}. `ts` is the raw WR stamp ((sec<<32)|ns)."""
        sec, nsec = wr_split(ts)
        ra = sec * 1_000_000_000 + nsec
        count = self._counts.get(event, 0) + 1
        self._counts[event] = count
        ts_key, count_key = self._code_keys(event)
        return {
            "ra_time": ra,
            "writes": [
                (ts_key,           {"_": _PACK_I64(ra)}),
                (count_key,        {"_": _PACK_I64(count)}),
                (self._stream_key, {"_": _PACK_U16(event)}),
            ],
        }


def main(argv):
    rc.line_buffer_stdout()
    pos, flags = rc.parse_args(
        argv, value_flags=("--src", "--namespace", "--base", "--redis-host", "--redis-port",
                           "--redis-username", "--redis-password",
                           "--maxlen", "--queue-size", "--statlog", "--snapshot-interval",
                           "--drop", "--watchdog-field", "--build-version"))
    dev      = pos[0] if pos else "/dev/uio4"
    src      = flags.get("--src", "tclk")
    # --base is the contract's term; --namespace is the older spelling kept for launchers.
    base     = flags.get("--base") or flags.get("--namespace") or DEFAULT_BASE
    host     = flags.get("--redis-host", "127.0.0.1")
    port     = int(flags.get("--redis-port", "6379"))
    # Auth: prefer the REDIS_PASSWORD / REDIS_USERNAME env vars (a --redis-password on the
    # command line would be visible in `ps`); the flags exist for manual use and win if set.
    user, pw = resolve_auth(flags.get("--redis-username"), flags.get("--redis-password"))
    maxlen   = int(flags.get("--maxlen", str(DEFAULT_MAXLEN)))
    qsize    = int(flags.get("--queue-size", "100000"))
    statpath = flags.get("--statlog", "stats-%s.jsonl" % src)
    interval = float(flags.get("--snapshot-interval", "60"))
    wd_field = flags.get("--watchdog-field", DEFAULT_WATCHDOG_FIELD)
    version  = flags.get("--build-version", BUILD_VERSION)

    io = rc.open_dev(dev)
    # Suppress flood codes (e.g. the 720 Hz 0x07) in the PL before they ever reach the FIFO,
    # so a sustained rate above the PS drain ceiling cannot overflow it. The drop-mask is a
    # hardware register cleared on every bitstream reload, so re-applying it here (once per
    # launch) makes the drop survive a PL reprogram with no manual step. See capture.md.
    rc.apply_drop_filter(io, rc.parse_drop_codes(flags.get("--drop", "")))
    sink = RedisSink(host=host, port=port, maxlen=maxlen, queue_size=qsize,
                     watchdog_key="{%s}:watchdog" % base,
                     watchdog_field=wd_field, watchdog_value=version,
                     watchdog_ttl=DEFAULT_WATCHDOG_TTL,
                     watchdog_period=DEFAULT_WATCHDOG_PERIOD,
                     username=user, password=pw)
    sink.start()
    builder = RecordBuilder(base)
    statlog = StatsLog(statpath)
    state = PublisherState()
    auth = "user=%s password=set" % (user or "default") if pw else "none"
    say("# publishing %s events from %s to {%s}:<HEX>|<HEX>_C|STREAM (%s:%d, auth=%s, "
        "maxlen=%d); stats -> %s every %gs. Ctrl-C to stop."
        % (src, dev, base, host, port, auth, maxlen, statpath, interval))

    def on_event(e):
        if state.note(e["ts"]):
            sink.submit(builder.build(e["event"], e["ts"]))

    # Runs on the drain thread (tick_cb): reads are cheap, but the statlog flush is on the
    # FIFO-drain critical path; a wedged disk could stall the drain (bounded, overflow-flagged).
    def snapshot():
        statlog.write(build_snapshot(
            now_utc(), time.monotonic(), src,
            read_hw_counters(io), sw_counters(state.drained, state.unsync, sink.stats())))

    def stats_line():
        s = sink.stats()
        say("[stats] drained=%d unsync=%d published=%d queued=%d queue_dropped=%d "
            "redis_dropped=%d reconnects=%d%s" % (
                state.drained, state.unsync, s["published"], s["queued"],
                s["queue_dropped"], s["redis_dropped"], s["reconnects"],
                (" last_error=%s" % s["last_error"]) if s["last_error"] else ""))

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
