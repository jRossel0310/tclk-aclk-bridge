"""End-to-end compliance test for the {TCLK} key space.

Two levels:
  * test_roundtrip_in_memory  - always runs. Publishes through the REAL producer path
    (RecordBuilder + RedisSink) into an in-memory Redis stub, then reads back with
    ra_consumer. Cross-checks that the producer's keys and payload widths are exactly
    what an independent consumer expects.
  * test_ra_roundtrip*        - needs a real `redis-server` binary (board / Linux CI).
    Same producer path against a private redis-server, proving real XADD accepts our
    explicit RA_Time ids and real HEXPIRE gives the watchdog field a TTL.

Run: python deploy/test_ra_roundtrip.py   or   pytest deploy -q"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from redis_publish import RecordBuilder
from redis_sink import RedisSink
import ra_consumer

BASE = "TCLK"
WD_FIELD = "kr260-tclk"
WD_VALUE = "kr260-readout/1.0"

# (event, ts=(sec<<32)|ns) -- distinct RA_Times, no collision
EVENTS = [
    (0x1D, (1_751_800_000 << 32) | 100),
    (0x07, (1_751_800_000 << 32) | 200),
    (0x07, (1_751_800_001 << 32) | 200),
]


def _ra(ts):
    return (ts >> 32) * 1_000_000_000 + (ts & 0xFFFFFFFF)


# ---------------------------------------------------------------- in-memory level

class MemPipe:
    def __init__(self, store):
        self.store = store
        self.pending = []

    def xadd(self, key, fields, id=None, maxlen=None, approximate=None):
        self.pending.append((key, id, dict(fields)))

    def execute(self):
        for key, eid, fields in self.pending:
            self.store.setdefault(key, []).append((eid, fields))
        self.pending = []


class MemRedis:
    """Just enough Redis to run the writer path and read it back."""
    def __init__(self):
        self.streams = {}
        self.hashes = {}
        self.expiries = {}

    def pipeline(self, transaction=False):
        return MemPipe(self.streams)

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hexpire(self, key, seconds, *fields):
        for f in fields:
            self.expiries[(key, f)] = seconds

    def xrange(self, key):
        return self.streams.get(key, [])


def _publish(sink_kwargs, events):
    sink = RedisSink(watchdog_key=ra_consumer.watchdog_key(BASE),
                     watchdog_field=WD_FIELD, watchdog_value=WD_VALUE,
                     watchdog_ttl=1, watchdog_period=0, **sink_kwargs)
    builder = RecordBuilder(BASE)
    sink.start()
    for ev, ts in events:
        sink.submit(builder.build(ev, ts))
    sink.stop(timeout=5.0)
    return sink.stats()


def test_roundtrip_in_memory():
    mem = MemRedis()
    stats = _publish({"connect": lambda: mem}, EVENTS)
    assert stats["published"] == len(EVENTS), stats
    assert stats["last_error"] is None, stats

    # the combined feed carries every event code, in order, as a uint16
    feed = mem.xrange(ra_consumer.stream_key(BASE))
    assert [ra_consumer.decode_event_id(f) for _, f in feed] == [e for e, _ in EVENTS]
    assert [ra_consumer.ra_time_from_id(i) for i, _ in feed] == [_ra(t) for _, t in EVENTS]

    # per-code event-time stream: value and stream ID are both the RA_Time
    ts_entries = mem.xrange(ra_consumer.ts_key(BASE, 0x07))
    assert [ra_consumer.decode_int64(f) for _, f in ts_entries] == [
        _ra(EVENTS[1][1]), _ra(EVENTS[2][1])]
    assert [ra_consumer.ra_time_from_id(i) for i, _ in ts_entries] == [
        _ra(EVENTS[1][1]), _ra(EVENTS[2][1])]

    # per-code counter stream counts occurrences of that code only
    counts = mem.xrange(ra_consumer.count_key(BASE, 0x07))
    assert [ra_consumer.decode_int64(f) for _, f in counts] == [1, 2]
    assert [ra_consumer.decode_int64(f)
            for _, f in mem.xrange(ra_consumer.count_key(BASE, 0x1D))] == [1]

    # exactly the contract's keys, nothing extra
    assert set(mem.streams) == {
        "{TCLK}:STREAM", "{TCLK}:1D", "{TCLK}:1D_C", "{TCLK}:07", "{TCLK}:07_C"}

    # watchdog: our field holds the build version and carries a short expiry
    wd = ra_consumer.watchdog_key(BASE)
    assert mem.hashes[wd] == {WD_FIELD: WD_VALUE}
    assert mem.expiries[(wd, WD_FIELD)] == 1


def test_roundtrip_in_memory_payload_widths():
    mem = MemRedis()
    _publish({"connect": lambda: mem}, [(0x1D, (5 << 32) | 7)])
    feed_fields = mem.xrange(ra_consumer.stream_key(BASE))[0][1]
    ts_fields = mem.xrange(ra_consumer.ts_key(BASE, 0x1D))[0][1]
    assert len(feed_fields["_"]) == ra_consumer.EVENT_LEN == 2
    assert len(ts_fields["_"]) == ra_consumer.TIME_LEN == 8
    assert list(feed_fields) == ["_"] and list(ts_fields) == ["_"]


def test_roundtrip_in_memory_ns_collision_guard():
    """Two events in the SAME nanosecond both publish; the shared feed bumps the second
    to +1 ns while the int64 payload keeps the true RA_Time."""
    mem = MemRedis()
    ts = (1_751_800_000 << 32) | 500
    _publish({"connect": lambda: mem}, [(0x07, ts), (0x07, ts)])
    feed = mem.xrange(ra_consumer.stream_key(BASE))
    ids = [ra_consumer.ra_time_from_id(i) for i, _ in feed]
    assert ids == [_ra(ts), _ra(ts) + 1], ids
    # the per-code time stream carries the true, unbumped value in its payload
    vals = [ra_consumer.decode_int64(f) for _, f in mem.xrange(ra_consumer.ts_key(BASE, 0x07))]
    assert vals == [_ra(ts), _ra(ts)], vals


# ---------------------------------------------------------------- real-server level

def _skip(reason):
    if "pytest" in sys.modules:
        import pytest
        pytest.skip(reason)
    print("SKIP:", reason)
    return True


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_redis(tmpdir):
    """Launch a private redis-server (persistence off) and wait for PING."""
    import redis
    port = _free_port()
    conf = os.path.join(tmpdir, "redis.conf")
    with open(conf, "w") as f:
        f.write("port %d\n" % port)
        f.write("bind 127.0.0.1\n")
        f.write("save \"\"\n")
        f.write("appendonly no\n")
        f.write("dir %s\n" % tmpdir)
    proc = subprocess.Popen(["redis-server", conf],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = redis.Redis(host="127.0.0.1", port=port, socket_connect_timeout=0.2)
    for _ in range(100):
        try:
            if client.ping():
                return proc, port
        except Exception:
            time.sleep(0.05)
    proc.terminate()
    raise RuntimeError("redis-server did not become ready")


def _need_real_redis():
    if shutil.which("redis-server") is None:
        return _skip("redis-server not on PATH")
    try:
        import redis                              # noqa: F401
    except ImportError:
        return _skip("redis-py not installed")
    return False


def test_ra_roundtrip():
    if _need_real_redis():
        return
    import redis
    tmp = tempfile.mkdtemp()
    proc = None
    try:
        proc, port = _start_redis(tmp)
        stats = _publish({"host": "127.0.0.1", "port": port}, EVENTS)
        assert stats["published"] == len(EVENTS), stats
        assert stats["last_error"] is None, stats     # HEXPIRE supported by this server

        client = redis.Redis(host="127.0.0.1", port=port)   # decode_responses=False
        feed = client.xrange(ra_consumer.stream_key(BASE))
        assert [ra_consumer.decode_event_id(f) for _, f in feed] == [e for e, _ in EVENTS]
        assert [ra_consumer.ra_time_from_id(i) for i, _ in feed] == [_ra(t) for _, t in EVENTS]

        ts07 = client.xrange(ra_consumer.ts_key(BASE, 0x07))
        assert [ra_consumer.decode_int64(f) for _, f in ts07] == [
            _ra(EVENTS[1][1]), _ra(EVENTS[2][1])]
        assert [ra_consumer.decode_int64(f)
                for _, f in client.xrange(ra_consumer.count_key(BASE, 0x07))] == [1, 2]

        wd = ra_consumer.watchdog_key(BASE)
        assert client.hget(wd, WD_FIELD) == WD_VALUE.encode()
        assert client.httl(wd, WD_FIELD)[0] > 0       # the field really has a TTL
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=5)
        shutil.rmtree(tmp, ignore_errors=True)


def test_ra_roundtrip_ns_collision_guard():
    """Real Redis rejects a non-increasing ID, so the guard is what keeps a same-ns
    burst publishable end to end."""
    if _need_real_redis():
        return
    import redis
    tmp = tempfile.mkdtemp()
    proc = None
    try:
        proc, port = _start_redis(tmp)
        ts = (1_751_800_000 << 32) | 500          # identical timestamp for both
        stats = _publish({"host": "127.0.0.1", "port": port}, [(0x07, ts), (0x07, ts)])
        assert stats["published"] == 2, stats

        client = redis.Redis(host="127.0.0.1", port=port)
        feed = client.xrange(ra_consumer.stream_key(BASE))
        assert len(feed) == 2, feed
        ra0 = ra_consumer.ra_time_from_id(feed[0][0])
        ra1 = ra_consumer.ra_time_from_id(feed[1][0])
        assert ra1 == ra0 + 1                      # guard bumped the collision by 1 ns
        vals = [ra_consumer.decode_int64(f)
                for _, f in client.xrange(ra_consumer.ts_key(BASE, 0x07))]
        assert vals == [_ra(ts), _ra(ts)]          # payload keeps the true time
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=5)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all ra_roundtrip tests passed")
