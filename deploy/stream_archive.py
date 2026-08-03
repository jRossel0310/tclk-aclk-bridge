#!/usr/bin/env python3
"""Archive the published {TCLK}:STREAM Redis feed to daily CSV files (board side).

The deployment trims event streams to ~10000 entries (~100 s at 99 ev/s); this process
follows the combined feed with batched XRANGE reads and appends one row per event to
events-<label>-YYYYMMDD.csv, resuming from archive-state.json across restarts, so
multi-day runs stay analyzable (see supercycle_plot.py, marker_timing.py).

Deliberately a SEPARATE process from the publisher: it only talks to redis-server,
never /dev/uio*, so it cannot affect the hardware FIFO drain. Worst-case Redis
backpressure lands in the publisher's bounded sink queue.

    python3 stream_archive.py                        # follow {TCLK}:STREAM
    python3 stream_archive.py --once -o tail.csv     # dump retention, exit

Row schema: id,sec,ns,event. sec/ns are derived from the entry's RA_Time stream ID,
which under a same-ns tie or a backward WR step can lead the true event time by a few
ns (the producer's documented monotonic bump). There is no data column: the {TCLK}
contract carries only the event code, so a data word would have to be invented.

Ctrl-C exits 0 (clean stop); a crash exits nonzero so the launcher's until-loop
restarts it."""
import argparse
import csv
import json
import os
import sys
import time

from ra_consumer import ra_time_from_id, decode_event_id, stream_key
from redis_sink import resolve_auth   # shared REDIS_USERNAME/REDIS_PASSWORD resolution

# Redis errors are retried in-process (see main's follow loop); anything else
# crashes so the launcher's until-loop restarts us. The guarded import keeps the
# module importable on machines without redis-py (PC unit tests inject a stub).
try:
    from redis.exceptions import RedisError
except ImportError:
    class RedisError(Exception):
        """Placeholder when redis-py is absent; production uses the real one."""

HEADER = ["id", "sec", "ns", "event"]


def _as_str(entry_id):
    return entry_id.decode() if isinstance(entry_id, (bytes, bytearray)) else entry_id


def row_from_entry(eid, fields):
    """One {TCLK}:STREAM entry -> one CSV row. The event code comes from the binary `_`
    payload; the timestamp comes from the stream ID (RA_Time, ns since the epoch)."""
    sec, ns = divmod(ra_time_from_id(eid), 1_000_000_000)
    return [_as_str(eid), str(sec), str(ns), str(decode_event_id(fields))]


class DailyCsv:
    """Append-only CSV sink with UTC-daily rotation; header on file creation."""

    def __init__(self, outdir, label, now=time.time):
        self.outdir = outdir
        self.label = label
        self.now = now
        self._f = None
        self._w = None
        self._day = None

    def _path(self, day):
        return os.path.join(self.outdir, "events-%s-%s.csv" % (self.label, day))

    def _roll(self):
        day = time.strftime("%Y%m%d", time.gmtime(self.now()))
        if day == self._day:
            return
        self.close()
        path = self._path(day)
        fresh = not os.path.exists(path)
        self._f = open(path, "a", newline="", buffering=1)
        self._w = csv.writer(self._f)
        if fresh:
            self._w.writerow(HEADER)
        self._day = day

    def write_rows(self, rows):
        self._roll()
        self._w.writerows(rows)
        self._f.flush()

    def close(self):
        if self._f is not None:
            try:
                self._f.close()
            except OSError:
                pass
        self._f = None
        self._w = None
        self._day = None


def drain_source(client, stream, last_id, sink, batch=10000):
    """XRANGE everything newer than last_id into sink(rows). Returns
    (new_last_id, n_rows). last_id None/'-' means the start of retention."""
    total = 0
    while True:
        lo = "-" if last_id in (None, "-") else "(" + last_id
        entries = client.xrange(stream, min=lo, max="+", count=batch)
        if not entries:
            return last_id, total
        sink([row_from_entry(e, f) for e, f in entries])
        last_id = _as_str(entries[-1][0])
        total += len(entries)
        if len(entries) < batch:
            return last_id, total


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def _stream_key(base):
    return stream_key(base)


def _default_connect(host, port, username=None, password=None):
    import redis   # lazy: module imports without redis-py (PC unit tests)
    # decode_responses stays FALSE: the `_` payload is raw bytes and decoding it as
    # text would corrupt it.
    return redis.Redis(host=host, port=port, username=username, password=password,
                       socket_connect_timeout=2.0, socket_timeout=5.0)


def main(argv, connect=None):
    ap = argparse.ArgumentParser(description="Archive the {TCLK}:STREAM feed to CSV.")
    ap.add_argument("--base", "--namespace", dest="base", default="TCLK",
                    help="Redis base key (braced automatically)")
    ap.add_argument("--src", dest="label", default="tclk",
                    help="label used in the output filename and state key")
    ap.add_argument("--redis-host", default="127.0.0.1")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--redis-username", default=None)
    ap.add_argument("--redis-password", default=None,
                    help="prefer the REDIS_PASSWORD env var (a CLI value shows in ps)")
    ap.add_argument("--poll", type=float, default=5.0)
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--batch", type=int, default=10000)
    ap.add_argument("--once", action="store_true",
                    help="dump the full retained stream to -o FILE and exit")
    ap.add_argument("-o", "--out", default=None, help="output file for --once")
    ap.add_argument("--max-loops", type=int, default=0,
                    help="follow mode: stop after N polls (0 = forever; tests only)")
    args = ap.parse_args(argv)
    if connect is None:
        # Bind the resolved creds into the (host, port) factory the loop calls, so the
        # injected-connect test path (a 2-arg lambda) stays untouched.
        user, pw = resolve_auth(args.redis_username, args.redis_password)
        connect = lambda h, p: _default_connect(h, p, username=user, password=pw)

    stream = _stream_key(args.base)
    if args.once:
        if not args.out:
            print("--once requires -o FILE", file=sys.stderr)
            return 2
        client = connect(args.redis_host, args.redis_port)
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            _, n = drain_source(client, stream, None, w.writerows, batch=args.batch)
        print("wrote %d events from %s to %s" % (n, stream, args.out))
        return 0

    state_path = os.path.join(args.outdir, "archive-state.json")
    state = load_state(state_path)
    writer = DailyCsv(args.outdir, args.label)
    client = None
    loops = 0
    print("# archiving %s under %s every %gs (state: %s). Ctrl-C to stop."
          % (stream, args.outdir, args.poll, state_path), flush=True)
    try:
        while True:
            try:
                if client is None:
                    client = connect(args.redis_host, args.redis_port)
                last, n = drain_source(client, stream, state.get(args.label),
                                       writer.write_rows, batch=args.batch)
                if n:
                    state[args.label] = last
                    save_state(state_path, state)
            except RedisError as e:   # Redis down/hiccup: log, back off, reconnect
                print("# archiver: redis error (%s); retrying" % e, flush=True)
                client = None
            loops += 1
            if args.max_loops and loops >= args.max_loops:
                return 0
            time.sleep(args.poll)
    except KeyboardInterrupt:
        return 0
    finally:
        writer.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
