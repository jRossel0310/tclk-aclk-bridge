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
