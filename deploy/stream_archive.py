#!/usr/bin/env python3
"""Archive the published KR260 Redis streams to daily CSV files (board side).

The Redis streams retain only ~1M entries (~2.8 h at 99 ev/s); this process
follows them with batched XRANGE reads and appends one row per event to
events-<src>-YYYYMMDD.csv, resuming from archive-state.json across restarts,
so multi-day runs stay analyzable (see supercycle_plot.py).

Deliberately a SEPARATE process from the publishers: it only talks to
redis-server, never /dev/uio*, so it cannot affect the hardware FIFO drain.
Worst-case Redis backpressure lands in the publishers' bounded sink queue.

    python3 stream_archive.py                              # follow tclk+aclk
    python3 stream_archive.py --once --src tclk -o tail.csv  # dump retention, exit

Row schema: id,sec,ns,event,data (values exactly as published).
Ctrl-C exits 0 (clean stop); a crash exits nonzero so the launcher's
until-loop restarts it."""
import argparse
import csv
import json
import os
import sys
import time

# Redis errors are retried in-process (see main's follow loop); anything else
# crashes so the launcher's until-loop restarts us. The guarded import keeps the
# module importable on machines without redis-py (PC unit tests inject a stub).
try:
    from redis.exceptions import RedisError
except ImportError:
    class RedisError(Exception):
        """Placeholder when redis-py is absent; production uses the real one."""

HEADER = ["id", "sec", "ns", "event", "data"]


def row_from_entry(eid, fields):
    """One published stream entry -> one CSV row (missing fields never crash)."""
    return [eid, fields.get("sec", "0"), fields.get("ns", "0"),
            fields.get("event", ""), fields.get("data", "0")]


class DailyCsv:
    """Append-only CSV sink with UTC-daily rotation; header on file creation."""

    def __init__(self, outdir, src, now=time.time):
        self.outdir = outdir
        self.src = src
        self.now = now
        self._f = None
        self._w = None
        self._day = None

    def _path(self, day):
        return os.path.join(self.outdir, "events-%s-%s.csv" % (self.src, day))

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
        last_id = entries[-1][0]
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


def _default_connect(host, port):
    import redis   # lazy: module imports without redis-py (PC unit tests)
    return redis.Redis(host=host, port=port, decode_responses=True,
                       socket_connect_timeout=2.0, socket_timeout=5.0)


def main(argv, connect=None):
    ap = argparse.ArgumentParser(description="Archive KR260 Redis streams to CSV.")
    ap.add_argument("--src", nargs="+", default=["tclk", "aclk"])
    ap.add_argument("--namespace", default="KR260")
    ap.add_argument("--redis-host", default="127.0.0.1")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--poll", type=float, default=5.0)
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--batch", type=int, default=10000)
    ap.add_argument("--once", action="store_true",
                    help="dump the full retained stream to -o FILE and exit")
    ap.add_argument("-o", "--out", default=None, help="output file for --once")
    ap.add_argument("--max-loops", type=int, default=0,
                    help="follow mode: stop after N polls (0 = forever; tests only)")
    args = ap.parse_args(argv)
    connect = connect or _default_connect

    if args.once:
        if len(args.src) != 1 or not args.out:
            print("--once requires exactly one --src and -o FILE", file=sys.stderr)
            return 2
        client = connect(args.redis_host, args.redis_port)
        stream = "%s:%s" % (args.namespace, args.src[0])
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            _, n = drain_source(client, stream, None, w.writerows, batch=args.batch)
        print("wrote %d events from %s to %s" % (n, stream, args.out))
        return 0

    state_path = os.path.join(args.outdir, "archive-state.json")
    state = load_state(state_path)
    writers = {s: DailyCsv(args.outdir, s) for s in args.src}
    client = None
    loops = 0
    print("# archiving %s under %s every %gs (state: %s). Ctrl-C to stop."
          % (",".join(args.src), args.outdir, args.poll, state_path), flush=True)
    try:
        while True:
            try:
                if client is None:
                    client = connect(args.redis_host, args.redis_port)
                for s in args.src:
                    stream = "%s:%s" % (args.namespace, s)
                    last, n = drain_source(client, stream, state.get(s),
                                           writers[s].write_rows, batch=args.batch)
                    if n:
                        state[s] = last
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
        for w in writers.values():
            w.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
