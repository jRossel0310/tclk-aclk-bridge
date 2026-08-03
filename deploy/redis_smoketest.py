#!/usr/bin/env python3
"""Prove a Redis target accepts our {base} key space before pointing the board at it.

Runs the REAL producer path (RecordBuilder + RedisSink) against a remote Redis with
synthetic events, reads it back with the independent ra_consumer decoders, checks the
watchdog field really got a TTL, then deletes exactly the keys it created. No hardware
and no /dev/uio, so it is safe to run before the bitstream is even loaded.

    python3 redis_smoketest.py --redis-host 10.200.8.97 --base KR260-SMOKETEST

It refuses to touch a production base key (TCLK, TCLK-MULTICAST) unless you pass
--allow-production, because this deletes the keys it writes and that server is shared.

Exit 0 = the target is ready for `redis_publish.py --base TCLK`. Exit 1 = it is not,
with the reason. The failure this exists to catch is HEXPIRE: hash-field TTLs need
redis-server >= 7.4 and redis-py >= 5.1, and without them the watchdog would either
error or (worse, if faked) report a dead publisher as alive forever."""
import sys
import time

import ra_consumer
from redis_publish import RecordBuilder
from redis_sink import RedisSink, resolve_auth

# (event code, ts=(sec<<32)|ns). 0x07 twice so the counter stream proves it increments.
SMOKE_EVENTS = [
    (0x1D, (1_751_800_000 << 32) | 100),
    (0x07, (1_751_800_000 << 32) | 200),
    (0x07, (1_751_800_001 << 32) | 200),
]

PRODUCTION_BASES = ("TCLK", "TCLK-MULTICAST")
WD_FIELD = "kr260-smoketest"
WD_VALUE = "kr260-readout/smoketest"

# The production publisher uses a 1 s watchdog TTL. The smoke test cannot: it reads the
# field back only after stopping the sink and several remote round trips, by which time a
# 1 s field has correctly expired and reads as a failure. A long TTL still proves exactly
# what matters here, that the SERVER accepts HEXPIRE and the field really carries a TTL.
SMOKE_WATCHDOG_TTL = 60


def wait_until(pred, timeout=15.0, interval=0.05):
    """Poll pred() until it is truthy or `timeout` seconds pass. Returns whether it held.

    Checks before sleeping, so an already-satisfied predicate returns immediately."""
    deadline = time.monotonic() + timeout
    while True:
        if pred():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def watchdog_verdict(httl_reply, ttl_set):
    """Judge an HTTL reply for the watchdog field -> (ok, message).

    Redis HTTL returns, per field: the remaining TTL in seconds, -1 if the field exists
    but has no expiry, or -2 if the field (or key) does not exist. The -1 case is the
    dangerous one: HEXPIRE was accepted but did nothing, so a dead publisher would read
    as alive forever."""
    if not httl_reply:
        return False, "HTTL returned an empty reply; this server may not support it"
    ttl = httl_reply[0]
    if ttl == -1:
        return False, ("watchdog field exists but has no TTL: HEXPIRE was a no-op, so a "
                       "dead publisher would report as alive forever")
    if ttl == -2:
        return False, ("watchdog field did not exist when read back: the HSET failed, or "
                       "the field expired before the check")
    if ttl <= 0 or ttl > ttl_set:
        return False, "watchdog TTL is %r, expected 1..%d" % (ttl, ttl_set)
    return True, "watchdog field carries a %ds TTL" % ttl


def is_production_base(base):
    """True for a base key the lab deployment serves to EPICS. The smoke test writes
    then DELETES its keys, which must never happen to a live producer's key space."""
    return base.upper() in PRODUCTION_BASES


def smoketest_keys(base, events):
    """Exactly the keys `events` will create, in creation order, deduplicated.

    Cleanup deletes this explicit list. It never scans or globs: this can run against a
    shared lab Redis where deleting another producer's key is unrecoverable."""
    keys = []
    for event, _ts in events:
        for k in (ra_consumer.ts_key(base, event), ra_consumer.count_key(base, event)):
            if k not in keys:
                keys.append(k)
    keys.append(ra_consumer.stream_key(base))
    keys.append(ra_consumer.watchdog_key(base))
    return keys


def _fail(msg):
    print("FAIL: %s" % msg)
    return 1


def run(host, port, base, user=None, pw=None):
    try:
        import redis
    except ImportError:
        return _fail("redis-py is not installed (pip install -r requirements-board.txt)")

    client = redis.Redis(host=host, port=port, username=user, password=pw,
                         socket_connect_timeout=3.0, socket_timeout=3.0)
    try:
        client.ping()
    except Exception as e:
        return _fail("cannot reach redis at %s:%d (%s)" % (host, port, e))
    version = client.info("server").get("redis_version", "?")
    print("connected to %s:%d, redis_version=%s" % (host, port, version))
    if not hasattr(client, "hexpire"):
        return _fail("this redis-py has no HEXPIRE (needs >= 5.1); pip install -U redis")

    print("publishing %d synthetic events to {%s} ..." % (len(SMOKE_EVENTS), base))
    sink = RedisSink(host=host, port=port, username=user, password=pw,
                     watchdog_key=ra_consumer.watchdog_key(base),
                     watchdog_field=WD_FIELD, watchdog_value=WD_VALUE,
                     watchdog_ttl=SMOKE_WATCHDOG_TTL, watchdog_period=0)
    builder = RecordBuilder(base)
    sink.start()
    for ev, ts in SMOKE_EVENTS:
        sink.submit(builder.build(ev, ts))

    # WAIT for the writer thread rather than stopping straight away. Its loop refreshes the
    # watchdog only while not stopping, and does so BEFORE draining, so calling stop() here
    # would race it: connecting to a remote Redis takes milliseconds while these submits
    # take microseconds, the watchdog pass gets skipped, and the field is never written
    # even though all the events publish. Reaching `published == N` proves that iteration
    # ran, so the watchdog has been written by the time we read it below.
    published = wait_until(
        lambda: sink.stats()["published"] >= len(SMOKE_EVENTS) or sink.stats()["last_error"])
    stats = sink.stats()
    if not published or stats["published"] != len(SMOKE_EVENTS) or stats["last_error"]:
        sink.stop(timeout=10.0)
        client.delete(*smoketest_keys(base, SMOKE_EVENTS))
        return _fail("publish failed (%s). If last_error mentions HEXPIRE the SERVER "
                     "is older than 7.4." % stats)

    # Read the watchdog while the sink is STILL RUNNING. After stop() the field is meant to
    # expire, so checking it on a stopped publisher tests the wrong thing.
    ok, wd_msg = watchdog_verdict(
        client.httl(ra_consumer.watchdog_key(base), WD_FIELD), SMOKE_WATCHDOG_TTL)
    sink.stop(timeout=10.0)

    try:
        if not ok:
            return _fail(wd_msg)

        feed = client.xrange(ra_consumer.stream_key(base))
        codes = [ra_consumer.decode_event_id(f) for _, f in feed]
        if codes != [e for e, _ in SMOKE_EVENTS]:
            return _fail("STREAM read back %r, expected %r"
                         % (codes, [e for e, _ in SMOKE_EVENTS]))

        for ev, ts in SMOKE_EVENTS[:1]:
            ra = (ts >> 32) * 1_000_000_000 + (ts & 0xFFFFFFFF)
            got = [ra_consumer.decode_int64(f)
                   for _, f in client.xrange(ra_consumer.ts_key(base, ev))]
            if got != [ra]:
                return _fail("{%s}:%02X read back %r, expected [%d]" % (base, ev, got, ra))

        counts = [ra_consumer.decode_int64(f)
                  for _, f in client.xrange(ra_consumer.count_key(base, 0x07))]
        if counts != [1, 2]:
            return _fail("counter stream read back %r, expected [1, 2]" % (counts,))

        print("stream, per-code time and counters verified; %s" % wd_msg)
        print("(the publisher uses a 1 s watchdog TTL in production; this test uses %ds "
              "so the check does not race its own expiry)" % SMOKE_WATCHDOG_TTL)
        print("PASS: %s:%d is ready for redis_publish.py" % (host, port))
        return 0
    finally:
        keys = smoketest_keys(base, SMOKE_EVENTS)
        client.delete(*keys)
        print("cleaned up %d test keys" % len(keys))


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Smoke-test a Redis target for the {TCLK} key space.")
    ap.add_argument("--redis-host", default="127.0.0.1")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--base", default="KR260-SMOKETEST")
    ap.add_argument("--redis-username", default=None)
    ap.add_argument("--redis-password", default=None,
                    help="prefer the REDIS_PASSWORD env var (a CLI value shows in ps)")
    ap.add_argument("--allow-production", action="store_true",
                    help="permit a production base key (this DELETES the keys it writes)")
    args = ap.parse_args(argv)
    if is_production_base(args.base) and not args.allow_production:
        print("refusing to smoke-test the production base key %r: this test deletes the "
              "keys it writes. Use --base KR260-SMOKETEST, or --allow-production if you "
              "are certain nothing is publishing there." % args.base, file=sys.stderr)
        return 2
    user, pw = resolve_auth(args.redis_username, args.redis_password)
    return run(args.redis_host, args.redis_port, args.base, user, pw)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
