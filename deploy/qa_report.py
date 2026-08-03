#!/usr/bin/env python3
"""One-command health check for the published {TCLK} event stream.

Runs the whole QA battery and prints a single verdict, so a long capture can be checked
in one line instead of five separate invocations. Exits 0 on PASS and 1 on FAIL, so it
works from cron or a watchdog.

Five sections, each independent and each skipped cleanly when its input is absent:

  COVERAGE  span, event rate and per-code breakdown, from the archive CSVs (or from the
            live Redis STREAM when running off-board with no CSVs)
  LOSS      per-code counter streams must be contiguous. RecordBuilder assigns the count
            on the drain thread BEFORE the sink queue, so any event lost downstream
            leaves a hole (verify_no_loss)
  LEDGER    the hardware side: decoded == published + missed + queued + unsync, from the
            publisher's JSONL stats log (stats_report). This is the only section that
            can see loss UPSTREAM of the counter, e.g. a PL FIFO overflow
  CLOCK     stamp-clock frequency offset against TCLK's GPS marker $8F (gps_calibrate)
  ARCHIVE   holes in the archived timeline, which mean the archiver was down longer than
            Redis retention and the data is gone

Runs from either end. On the board it sees everything; from a PC it does COVERAGE and
LOSS over the network and skips the sections that need board-local files.

    python3 qa_report.py                                     # on the board, everything
    python3 qa_report.py --redis-host 10.200.12.100          # from a PC
    python3 qa_report.py --archive 'events-tclk-*.csv' --statlog stats-tclk.jsonl

A clock offset is reported as an ADVISORY, not a failure: the -3.48 ppm on this hardware
is a known condition of the free-running PPS source, not a fault in the capture."""
import csv
import glob
import sys
from collections import Counter, namedtuple

NS = 1_000_000_000
GPS_EVENT = 0x8F
DEFAULT_HOLE_S = 2.0          # a gap larger than this is a real archive hole
ADVISE_PPM = 0.1              # |offset| above this is worth mentioning
FAIL_PPM = 100.0              # ... and above this something is badly wrong

Coverage = namedtuple("Coverage", "n span_s rate per_code distinct first_ns last_ns")


def coverage(events):
    """[(ra_time_ns, code), ...] -> Coverage. Order independent; safe when empty."""
    if not events:
        return Coverage(0, 0.0, 0.0, Counter(), 0, None, None)
    times = [t for t, _ in events]
    first, last = min(times), max(times)
    span = (last - first) / NS
    per_code = Counter(c for _, c in events)
    return Coverage(n=len(events), span_s=span,
                    rate=(len(events) / span) if span > 0 else 0.0,
                    per_code=per_code, distinct=len(per_code),
                    first_ns=first, last_ns=last)


def find_holes(times_ns, min_gap_ns):
    """Gaps in a timeline STRICTLY larger than min_gap_ns -> [(start, end, gap), ...].

    A gap exactly at the threshold is not a hole, so a 1 Hz marker checked against a 1 s
    threshold does not report every interval."""
    t = sorted(times_ns)
    return [(a, b, b - a) for a, b in zip(t, t[1:]) if b - a > min_gap_ns]


class Verdict:
    """Accumulates per-section results. Failures set the exit code; advisories do not,
    because a known condition (the clock offset) should not fail an otherwise good run.
    A skipped section is neither: its input simply was not available here."""

    def __init__(self):
        self.results = []          # (kind, section, message)

    def ok(self, section, msg):
        self.results.append(("ok", section, msg))

    def fail(self, section, msg):
        self.results.append(("fail", section, msg))

    def advise(self, section, msg):
        self.results.append(("advisory", section, msg))

    def skip(self, section, msg):
        self.results.append(("skip", section, msg))

    def _of(self, kind):
        return [(s, m) for k, s, m in self.results if k == kind]

    @property
    def passed(self):
        return not self._of("fail")

    @property
    def exit_code(self):
        return 0 if self.passed else 1

    def summary(self):
        fails, advs, skips = self._of("fail"), self._of("advisory"), self._of("skip")
        lines = []
        if fails:
            lines.append("VERDICT: FAIL")
            lines += ["  FAIL     %-8s %s" % (s, m) for s, m in fails]
        else:
            lines.append("VERDICT: PASS")
        lines += ["  advisory %-8s %s" % (s, m) for s, m in advs]
        lines += ["  skipped  %-8s %s" % (s, m) for s, m in skips]
        return "\n".join(lines)


# ------------------------------------------------------------------ loaders

def load_archive(patterns):
    """Archive CSVs (id,sec,ns,event) -> [(ra_time_ns, code), ...]. Rows that do not
    parse are skipped rather than fatal: a capture in progress can have a torn last line."""
    events = []
    for pat in patterns:
        for path in sorted(glob.glob(pat)):
            with open(path, newline="") as f:
                r = csv.reader(f)
                next(r, None)                       # header
                for row in r:
                    if len(row) < 4:
                        continue
                    try:
                        events.append((int(row[1]) * NS + int(row[2]), int(row[3])))
                    except ValueError:
                        continue
    return events


def load_from_redis(client, base):
    """The live STREAM feed -> [(ra_time_ns, code), ...]. Only the retained window."""
    import ra_consumer as ra
    return [(ra.ra_time_from_id(eid), ra.decode_event_id(f))
            for eid, f in client.xrange(ra.stream_key(base))]


# ------------------------------------------------------------------ sections

def section_coverage(v, events, source):
    print("== COVERAGE ==")
    c = coverage(events)
    if not c.n:
        v.skip("coverage", "no events found (%s)" % source)
        print("  no events found (%s)\n" % source)
        return c
    print("  source     : %s" % source)
    print("  span       : %.1f s (%.2f h)" % (c.span_s, c.span_s / 3600.0))
    print("  events     : %d   %.1f ev/s" % (c.n, c.rate))
    print("  codes      : %d distinct" % c.distinct)
    top = "  ".join("%02X %.0f%%" % (code, 100.0 * n / c.n)
                    for code, n in c.per_code.most_common(6))
    print("  top codes  : %s" % top)
    v.ok("coverage", "%d events over %.2f h" % (c.n, c.span_s / 3600.0))
    print()
    return c


def section_loss(v, client, base):
    print("== LOSS (drain thread -> Redis) ==")
    if client is None:
        v.skip("loss", "no Redis connection")
        print("  skipped: needs Redis\n")
        return
    import verify_no_loss as vnl
    import ra_consumer as ra
    keys = vnl.counter_keys(client, base)
    if not keys:
        v.skip("loss", "no {%s}:<HEX>_C keys" % base)
        print("  skipped: no counter streams under {%s}\n" % base)
        return
    lost = restarts = dups = checked = 0
    worst = []
    for code, key in keys:
        r = vnl.analyse_counts(code, [ra.decode_int64(f) for _, f in client.xrange(key)])
        checked += r.n
        lost += r.lost
        restarts += r.restarts
        dups += r.duplicates
        if r.lost:
            worst.append(r)
    print("  codes      : %d   counter entries checked: %d" % (len(keys), checked))
    print("  restarts   : %d (counter reset to zero; not loss)" % restarts)
    if dups:
        print("  duplicates : %d" % dups)
    if lost:
        print("  MISSING    : %d event(s)" % lost)
        for r in worst[:5]:
            for a, b in r.gaps[:3]:
                print("     code %02X: %d -> %d, %d missing" % (r.code, a, b, b - a - 1))
        v.fail("loss", "%d event(s) never reached Redis" % lost)
    else:
        print("  MISSING    : 0   every counter sequence contiguous")
        v.ok("loss", "contiguous")
    print()


def section_ledger(v, statlog):
    print("== LEDGER (PL decoder -> publisher) ==")
    if not statlog:
        v.skip("ledger", "no stats log (running off-board?)")
        print("  skipped: needs the publisher's JSONL stats log\n")
        return
    import stats_report as sr
    snaps = sr.load_snapshots(statlog)
    if not snaps:
        v.skip("ledger", "stats log empty")
        print("  skipped: %s is empty\n" % statlog)
        return
    for src, group in sr.group_by_src(snaps).items():
        r = sr.reconcile(group)
        print(sr.format_report(r))
        if r["ledger_ok"]:
            v.ok("ledger", "%s reconciles (decoded=%d)" % (src, r["decoded"]))
        else:
            v.fail("ledger", "%s: decoded=%d != accounted=%d"
                   % (src, r["decoded"], r["accounted"]))
    print()


def section_clock(v, events):
    print("== CLOCK (stamp clock vs GPS $8F) ==")
    marks = sorted(t for t, c in events if c == GPS_EVENT)
    if len(marks) < 4:
        v.skip("clock", "fewer than 4 $8F markers")
        print("  skipped: need >= 4 $8F markers, found %d\n" % len(marks))
        return
    import gps_calibrate as gc
    try:
        cal = gc.calibrate(marks)
    except ValueError as e:
        v.skip("clock", str(e))
        print("  skipped: %s\n" % e)
        return
    drift_ms_h = abs(cal.ppm) * 3600.0 / 1000.0
    print("  markers    : %d over %.1f s" % (cal.n, cal.span_s))
    print("  offset     : %+.3f ppm   (%.1f ms/hour vs GPS)" % (cal.ppm, drift_ms_h))
    print("  fit resid  : sd %.0f ns" % cal.resid_sd)
    if abs(cal.ppm) > FAIL_PPM:
        v.fail("clock", "%+.1f ppm is far outside anything sane" % cal.ppm)
    elif abs(cal.ppm) > ADVISE_PPM:
        v.advise("clock", "%+.3f ppm vs GPS (%.1f ms/hour); PPS source is free-running"
                 % (cal.ppm, drift_ms_h))
    else:
        v.ok("clock", "%+.3f ppm" % cal.ppm)
    print()


def section_archive(v, events, hole_s):
    print("== ARCHIVE CONTINUITY ==")
    if not events:
        v.skip("archive", "no archived events")
        print("  skipped: no archive CSVs\n")
        return
    holes = find_holes([t for t, _ in events], int(hole_s * NS))
    if not holes:
        print("  no gaps > %.1f s" % hole_s)
        v.ok("archive", "continuous")
    else:
        total = sum(g for _, _, g in holes) / NS
        print("  %d hole(s), %.1f s total unarchived" % (len(holes), total))
        for a, b, g in holes[:5]:
            print("     %.1f s gap at t=%d" % (g / NS, a // NS))
        v.fail("archive", "%d timeline hole(s), %.1f s of events not archived"
               % (len(holes), total))
    print()


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="QA report for the published {TCLK} stream.")
    ap.add_argument("--redis-host", default="127.0.0.1")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--base", default="TCLK")
    ap.add_argument("--archive", nargs="*", default=["events-tclk-*.csv"],
                    help="archive CSV glob(s); pass none to use the live Redis stream")
    ap.add_argument("--statlog", default="stats-tclk.jsonl")
    ap.add_argument("--hole-seconds", type=float, default=DEFAULT_HOLE_S)
    ap.add_argument("--no-redis", action="store_true", help="skip every Redis section")
    args = ap.parse_args(argv)

    client = None
    if not args.no_redis:
        try:
            import redis
            from redis_sink import resolve_auth
            user, pw = resolve_auth(None, None)
            client = redis.Redis(host=args.redis_host, port=args.redis_port,
                                 username=user, password=pw, socket_connect_timeout=3.0)
            client.ping()
        except Exception as e:
            print("# no Redis at %s:%d (%s); Redis sections will be skipped\n"
                  % (args.redis_host, args.redis_port, e))
            client = None

    events = load_archive(args.archive)
    source = "archive %s" % ", ".join(args.archive)
    if not events and client is not None:
        events = load_from_redis(client, args.base)
        source = "live {%s}:STREAM (retained window only)" % args.base

    import os
    statlog = args.statlog if args.statlog and os.path.exists(args.statlog) else None

    print("=" * 70)
    print("QA REPORT   base {%s}   redis %s:%d" % (args.base, args.redis_host, args.redis_port))
    print("=" * 70)
    v = Verdict()
    section_coverage(v, events, source)
    section_loss(v, client, args.base)
    section_ledger(v, statlog)
    section_clock(v, events)
    section_archive(v, events, args.hole_seconds)
    print("=" * 70)
    print(v.summary())
    return v.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
