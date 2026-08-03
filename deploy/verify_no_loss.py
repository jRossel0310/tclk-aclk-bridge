#!/usr/bin/env python3
"""Prove every drained event reached Redis, using the per-code counter streams.

RecordBuilder assigns an event its occurrence number on the DRAIN thread, before it is
handed to the sink queue. So {TCLK}:<HEX>_C should read 1, 2, 3, ... with no holes, and
any event lost downstream of that point leaves a visible gap:

    queue overflow (queue_dropped)   -> gap
    Redis error dropping a batch     -> gap
    an XADD that silently failed     -> gap

That makes loss self-detecting with no external reference: the sequence is its own
checksum. It cannot see loss UPSTREAM of the counter (a PL FIFO overflow, a CRC-failed
frame, a code suppressed by the drop-mask); for that side run stats_report.py, which
reconciles the hardware counters against `published`. The two together cover the chain.

The retained window rarely starts at 1 because MAXLEN trims, so a high `first` is normal
and is not loss. A counter going backwards is a publisher restart, which the deployment
documents as the intended restart signal, not corruption.

    python3 verify_no_loss.py --redis-host 10.200.12.100
    python3 verify_no_loss.py --redis-host 10.200.12.100 --base TCLK --top 12

Exit 0 when nothing is missing, 1 when something is."""
import sys
from collections import namedtuple

import ra_consumer as ra

CodeReport = namedtuple("CodeReport",
                        "code n first last lost gaps restarts duplicates")


def analyse_counts(code, values):
    """One code's counter values (in stream order) -> a CodeReport.

    A step of +1 is healthy. A step > +1 means (step - 1) events never reached Redis. A
    step of 0 is a duplicate. A negative step is a publisher restart, which resets the
    counter to zero by design, so it is reported separately and never counted as loss."""
    lost, gaps, restarts, duplicates = 0, [], 0, 0
    prev = None
    for v in values:
        if prev is not None:
            step = v - prev
            if step < 0:
                restarts += 1
            elif step == 0:
                duplicates += 1
            elif step > 1:
                lost += step - 1
                gaps.append((prev, v))
        prev = v
    return CodeReport(code=code, n=len(values),
                      first=values[0] if values else None,
                      last=values[-1] if values else None,
                      lost=lost, gaps=gaps, restarts=restarts,
                      duplicates=duplicates)


def counter_keys(client, base):
    """Every {base}:<HEX>_C key present, as (code, key), sorted by code."""
    out = []
    for k in client.scan_iter(match="{%s}:*_C" % base, count=500):
        name = k.decode() if isinstance(k, bytes) else k
        hexpart = name.rsplit(":", 1)[-1][:-2]      # strip the trailing _C
        try:
            out.append((int(hexpart, 16), name))
        except ValueError:
            continue                                 # not a <HEX>_C key, ignore
    return sorted(out)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Verify no published event is missing.")
    ap.add_argument("--redis-host", default="127.0.0.1")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--base", default="TCLK")
    ap.add_argument("--top", type=int, default=10, help="how many codes to list")
    args = ap.parse_args(argv)

    import redis
    from redis_sink import resolve_auth
    user, pw = resolve_auth(None, None)
    c = redis.Redis(host=args.redis_host, port=args.redis_port,
                    username=user, password=pw, socket_connect_timeout=3.0)

    keys = counter_keys(c, args.base)
    if not keys:
        print("no {%s}:<HEX>_C keys found; is the publisher running?" % args.base)
        return 1

    reports = []
    for code, key in keys:
        values = [ra.decode_int64(f) for _, f in c.xrange(key)]
        reports.append(analyse_counts(code, values))

    checked = sum(r.n for r in reports)
    lost = sum(r.lost for r in reports)
    restarts = sum(r.restarts for r in reports)
    dups = sum(r.duplicates for r in reports)

    print("%d codes, %d counter entries checked in the retained window" % (len(reports), checked))
    print()
    print("  code   entries      first       last   lost")
    for r in sorted(reports, key=lambda r: -r.n)[:args.top]:
        print("  %02X   %9d %10s %10s %6d%s"
              % (r.code, r.n, r.first, r.last, r.lost, "  <-- GAPS" if r.lost else ""))
    if len(reports) > args.top:
        print("  ... %d more codes" % (len(reports) - args.top))
    print()

    if restarts:
        print("%d publisher restart(s) seen (counter reset to zero); not loss." % restarts)
    if dups:
        print("WARNING: %d duplicate counter value(s)." % dups)
    if lost:
        print("FAIL: %d event(s) never reached Redis." % lost)
        for r in reports:
            for a, b in r.gaps:
                print("   code %02X: counts %d -> %d, %d missing" % (r.code, a, b, b - a - 1))
        print("Check the publisher's queue_dropped / redis_dropped / reconnects stats.")
        return 1

    print("PASS: every counter sequence is contiguous. No event was lost between the")
    print("      drain thread and Redis. For the PL side (FIFO overflow, failed CRC),")
    print("      run: sudo python3 stats_report.py stats-tclk.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
