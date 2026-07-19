"""End-to-end RedisAdapter Protocol v1.0 compliance self-test.

Stands up a PRIVATE redis-server on an ephemeral port, publishes synthetic events
through the REAL producer path (build_record + RedisSink), then reads them back with
the RA-compliant ra_consumer using ONLY the key schema + stream ID + `_` field. This
proves a generic RedisAdapter consumer recovers our primary data. The same producer
targets any Redis by host/port, so pointing at a lab Redis is just different args.

Skips cleanly if `redis-server` is not on PATH (e.g. Windows dev box) or redis-py is
missing. Run: python deploy/test_ra_roundtrip.py   or   pytest deploy -q"""
import os
import shutil
import socket
import subprocess
import tempfile
import time

from redis_publish import build_record
from redis_sink import RedisSink
import ra_consumer

NS = "KR260"


def _skip(reason):
    try:
        import pytest
        pytest.skip(reason, allow_module_level=False)
    except ImportError:
        print("SKIP:", reason)
        raise SystemExit(0)


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


def _publish(port, events):
    sink = RedisSink(host="127.0.0.1", port=port,
                     status_key="{%s}:status" % NS,
                     watchdog_key="{%s}:watchdog" % NS, watchdog_period=0)
    sink.start()
    for ev, fl, da, ts in events:
        sink.submit(build_record(NS, "tclk", ev, fl, da, ts))
    sink.stop(timeout=5.0)
    return sink.stats()


def test_ra_roundtrip():
    if shutil.which("redis-server") is None:
        _skip("redis-server not on PATH")
    try:
        import redis
    except ImportError:
        _skip("redis-py not installed")

    tmp = tempfile.mkdtemp()
    proc = None
    try:
        proc, port = _start_redis(tmp)
        events = [
            # (event, flags, data, ts=(sec<<32)|ns)  -- distinct RA_Times, no collision
            (0x1D, 0x02, 0,      (1_751_800_000 << 32) | 100),
            (0x07, 0x03, 0xABCD, (1_751_800_000 << 32) | 200),
            (0x07, 0x03, 0x1234, (1_751_800_001 << 32) | 200),
        ]
        stats = _publish(port, events)
        assert stats["published"] == len(events), stats

        client = redis.Redis(host="127.0.0.1", port=port)   # decode_responses=False
        entries = client.xrange(ra_consumer.stream_key(NS, "tclk"))
        assert len(entries) == len(events), entries
        decoded = [ra_consumer.decode_entry(eid, fmap) for eid, fmap in entries]

        # `_` recovers event/data/flags/sec/ns exactly, in publish order
        for (ev, fl, da, ts), d in zip(events, decoded):
            sec, ns = ts >> 32, ts & 0xFFFFFFFF
            assert d["event"] == ev, (d, ev)
            assert d["data"] == da, (d, da)
            assert d["is_tclk"] == (fl >> 1) & 1
            assert d["has_data"] == fl & 1
            assert d["sec"] == sec and d["ns"] == ns
            # RA_Time from the stream ID equals sec*1e9+ns (no collisions here)
            assert d["ra_time"] == sec * 1_000_000_000 + ns

        # braced keys, per-code index hash, and liveness all present
        assert client.exists("{%s}:event:tclk:0x1D" % NS) == 1
        assert client.exists("{%s}:event:tclk:0x07" % NS) == 1
        assert client.hget("{%s}:event:tclk:0x07" % NS, "count") == b"2"
        assert client.get("{%s}:status" % NS) == b"1"
        assert client.exists("{%s}:watchdog" % NS) == 1
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=5)
        shutil.rmtree(tmp, ignore_errors=True)


def test_ra_roundtrip_ns_collision_guard():
    """Two events in the SAME nanosecond must both publish, with strictly increasing
    stream IDs (the second bumped +1 ns), and the `_` payload keeps the true sec/ns."""
    if shutil.which("redis-server") is None:
        _skip("redis-server not on PATH")
    try:
        import redis
    except ImportError:
        _skip("redis-py not installed")

    tmp = tempfile.mkdtemp()
    proc = None
    try:
        proc, port = _start_redis(tmp)
        ts = (1_751_800_000 << 32) | 500          # identical timestamp for both
        events = [(0x07, 0x03, 1, ts), (0x07, 0x03, 2, ts)]
        stats = _publish(port, events)
        assert stats["published"] == 2, stats

        client = redis.Redis(host="127.0.0.1", port=port)
        entries = client.xrange(ra_consumer.stream_key(NS, "tclk"))
        assert len(entries) == 2, entries
        ra0 = ra_consumer.ra_time_from_id(entries[0][0])
        ra1 = ra_consumer.ra_time_from_id(entries[1][0])
        assert ra1 == ra0 + 1                      # guard bumped the collision by 1 ns
        # both `_` payloads still carry the true (identical) sec/ns and their own data
        d0 = ra_consumer.decode_entry(*entries[0])
        d1 = ra_consumer.decode_entry(*entries[1])
        assert d0["ns"] == 500 and d1["ns"] == 500
        assert d0["data"] == 1 and d1["data"] == 2
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
