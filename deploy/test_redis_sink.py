"""Unit tests for redis_sink.RedisSink (no hardware, no Redis server).
A stub Redis records the pipelined XADDs and the watchdog hset/hexpire calls, and can
be told to fail to exercise reconnect.
Run: python deploy/test_redis_sink.py   or   pytest deploy -q"""
import struct
import time

from redis_sink import RedisSink

RA = 1_751_800_000_123_456_789


class FakePipe:
    def __init__(self, ops_log, fail):
        self.ops_log = ops_log      # committed ops land here on execute()
        self.fail = fail
        self.pending = []

    def xadd(self, stream, fields, id=None, maxlen=None, approximate=None):
        self.pending.append(("xadd", stream, dict(fields), id, maxlen, approximate))

    def execute(self):
        if self.fail:
            raise RuntimeError("redis down")
        self.ops_log.extend(self.pending)
        self.pending = []


class FakeRedis:
    """Records committed pipeline ops in `ops` and watchdog calls in `wd`. First
    `fail_times` pipelines raise on execute(), to exercise reconnect/drop."""
    def __init__(self, fail_times=0):
        self.ops = []
        self.wd = []               # ("hset", key, field, value) / ("hexpire", key, ttl, field)
        self.fail_times = fail_times
        self.fail_watchdog = False

    def pipeline(self, transaction=False):
        fail = self.fail_times > 0
        if fail:
            self.fail_times -= 1
        return FakePipe(self.ops, fail)

    def hset(self, key, field, value):
        if self.fail_watchdog:
            raise RuntimeError("redis down")
        self.wd.append(("hset", key, field, value))

    def hexpire(self, key, seconds, *fields):
        if self.fail_watchdog:
            raise RuntimeError("redis down")
        self.wd.append(("hexpire", key, seconds, fields[0]))


def _wait(pred, timeout=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _record(ra_time, event=0x1D, count=1):
    """The shape RecordBuilder emits: three writes sharing one RA_Time."""
    return {
        "ra_time": ra_time,
        "writes": [
            ("{TCLK}:%02X" % event,   {"_": struct.pack("<q", ra_time)}),
            ("{TCLK}:%02X_C" % event, {"_": struct.pack("<q", count)}),
            ("{TCLK}:STREAM",         {"_": struct.pack("<H", event)}),
        ],
    }


def _xadds(fake):
    return [o for o in fake.ops if o[0] == "xadd"]


def test_record_pipelines_three_xadds_in_order():
    fake = FakeRedis()
    sink = RedisSink(maxlen=555, connect=lambda: fake)
    sink.start()
    sink.submit(_record(1_000_000, event=0x1D))          # 1e6 ns -> id "1-0"
    assert _wait(lambda: len(fake.ops) >= 3), sink.stats()
    sink.stop()
    keys = [o[1] for o in _xadds(fake)]
    assert keys == ["{TCLK}:1D", "{TCLK}:1D_C", "{TCLK}:STREAM"], fake.ops
    for _, _, _fields, sid, maxlen, approx in _xadds(fake):
        assert sid == "1-0" and maxlen == 555 and approx is True
    assert sink.stats()["published"] == 1


def test_payloads_are_passed_through_untouched():
    fake = FakeRedis()
    sink = RedisSink(connect=lambda: fake)
    sink.start()
    sink.submit(_record(RA, event=0x0F, count=7))
    assert _wait(lambda: len(fake.ops) >= 3), sink.stats()
    sink.stop()
    ts_f, cnt_f, stream_f = [o[2] for o in _xadds(fake)]
    assert struct.unpack("<q", ts_f["_"])[0] == RA
    assert struct.unpack("<q", cnt_f["_"])[0] == 7
    assert struct.unpack("<H", stream_f["_"])[0] == 0x0F


def test_monotonic_guard_is_per_stream():
    # Two events at the SAME RA_Time under different codes: each per-code stream sees its
    # first entry (no bump), but the shared STREAM feed must bump the second to +1 ns.
    fake = FakeRedis()
    sink = RedisSink(connect=lambda: fake)
    sink.submit(_record(1_000_000, event=0x1D))
    sink.submit(_record(1_000_000, event=0x0F))
    sink.start()
    sink.stop(timeout=3.0)
    ids = {o[1]: o[3] for o in _xadds(fake)}
    assert ids["{TCLK}:1D"] == "1-0"
    assert ids["{TCLK}:0F"] == "1-0"          # own stream, no collision
    assert ids["{TCLK}:STREAM"] == "1-1"      # shared feed, bumped past the tie


def test_backward_ra_time_is_bumped_not_dropped():
    fake = FakeRedis()
    sink = RedisSink(connect=lambda: fake)
    sink.submit(_record(1_000_000, event=0x1D))
    sink.submit(_record(500_000, event=0x1D))     # backward: bumped to 1_000_001
    sink.submit(_record(2_000_000, event=0x1D))
    sink.start()
    sink.stop(timeout=3.0)
    ids = [o[3] for o in _xadds(fake) if o[1] == "{TCLK}:1D"]
    assert ids == ["1-0", "1-1", "2-0"], ids


def test_queue_full_drops_oldest():
    sink = RedisSink(queue_size=2, connect=lambda: FakeRedis())
    a, b, c = _record(1), _record(2), _record(3)
    sink.submit(a)
    sink.submit(b)
    sink.submit(c)                 # full -> drop oldest (a) -> [b, c]
    assert sink.stats()["queue_dropped"] == 1
    drained = [sink._q.get_nowait(), sink._q.get_nowait()]
    assert drained == [b, c]


def test_reconnect_after_error():
    fake = FakeRedis(fail_times=1)
    sink = RedisSink(connect=lambda: fake)
    sink.start()
    sink.submit(_record(1000))
    assert _wait(lambda: sink.stats()["reconnects"] >= 1), sink.stats()
    sink.submit(_record(1001))
    assert _wait(lambda: sink.stats()["published"] >= 1), sink.stats()
    sink.stop()
    assert sink.stats()["redis_dropped"] >= 1


def test_stop_flushes_queue():
    fake = FakeRedis()
    sink = RedisSink(connect=lambda: fake)
    for i in range(50):
        sink.submit(_record(1000 + i))
    sink.start()
    sink.stop(timeout=3.0)
    assert len(_xadds(fake)) == 150            # 3 writes per event
    assert sink.stats()["published"] == 50 and sink.stats()["queued"] == 0


def test_watchdog_sets_field_then_expires_it():
    fake = FakeRedis()
    sink = RedisSink(watchdog_key="{TCLK}:watchdog", watchdog_field="kr260-tclk",
                     watchdog_value="kr260-readout/1.0", watchdog_ttl=1,
                     watchdog_period=0, connect=lambda: fake)
    sink.start()
    assert _wait(lambda: len(fake.wd) >= 2), fake.wd
    sink.stop()
    assert fake.wd[0] == ("hset", "{TCLK}:watchdog", "kr260-tclk", "kr260-readout/1.0")
    assert fake.wd[1] == ("hexpire", "{TCLK}:watchdog", 1, "kr260-tclk")


def test_watchdog_throttled_to_its_period():
    fake = FakeRedis()
    sink = RedisSink(watchdog_key="{TCLK}:watchdog", watchdog_field="f",
                     watchdog_value="v", watchdog_period=1000, connect=lambda: fake)
    sink.start()
    assert _wait(lambda: len(fake.wd) >= 2), fake.wd
    time.sleep(0.05)
    sink.stop()
    assert len(fake.wd) == 2, fake.wd          # one refresh only, period is 1000 s


def test_no_watchdog_key_means_no_watchdog_writes():
    fake = FakeRedis()
    sink = RedisSink(connect=lambda: fake)
    sink.start()
    sink.submit(_record(1000))
    assert _wait(lambda: len(fake.ops) >= 3), sink.stats()
    sink.stop()
    assert fake.wd == []


def test_watchdog_error_backs_off():
    fake = FakeRedis()
    fake.fail_watchdog = True
    sink = RedisSink(watchdog_key="{TCLK}:watchdog", watchdog_field="f",
                     watchdog_value="v", watchdog_period=0, connect=lambda: fake)
    sink.start()
    time.sleep(0.3)
    sink.stop()
    # a persistent Redis error must back off (~0.5 s), not busy-spin: a spin would rack
    # up thousands of reconnects in 0.3 s; the backoff keeps it tiny.
    assert sink.stats()["reconnects"] <= 5, sink.stats()


def test_old_redis_py_without_hexpire_reports_a_clear_error():
    class NoHexpire:
        """redis-py < 5.1: has hset, has no hexpire (field TTLs are Redis 7.4 + py 5.1)."""
        def __init__(self):
            self.ops = []
            self.wd = []

        def pipeline(self, transaction=False):
            return FakePipe(self.ops, False)

        def hset(self, key, field, value):
            self.wd.append(("hset", key, field, value))

    fake = NoHexpire()
    sink = RedisSink(watchdog_key="{TCLK}:watchdog", watchdog_field="f",
                     watchdog_value="v", watchdog_period=0, connect=lambda: fake)
    sink.start()
    assert _wait(lambda: sink.stats()["last_error"]), sink.stats()
    sink.stop()
    assert "hexpire" in sink.stats()["last_error"].lower(), sink.stats()


def test_last_error_records_then_clears_on_recovery():
    fake = FakeRedis(fail_times=1)
    sink = RedisSink(connect=lambda: fake)
    sink.start()
    sink.submit(_record(1000))
    assert _wait(lambda: sink.stats()["last_error"]), sink.stats()
    sink.submit(_record(1001))
    assert _wait(lambda: sink.stats()["published"] >= 1), sink.stats()
    sink.stop()
    assert sink.stats()["last_error"] is None, sink.stats()


def test_stop_flushes_when_redis_fully_down():
    # the watchdog raises and every pipeline.execute() raises. A stopping writer must skip
    # liveness work and go straight to draining the queue (discarding as redis_dropped),
    # not busy-spin on the watchdog path until timeout.
    fake = FakeRedis(fail_times=10_000)
    fake.fail_watchdog = True
    sink = RedisSink(watchdog_key="{TCLK}:watchdog", watchdog_field="f",
                     watchdog_value="v", watchdog_period=0, connect=lambda: fake)
    for i in range(50):
        sink.submit(_record(1000 + i))
    sink.start()
    sink.stop(timeout=2.0)
    assert sink.stats()["queued"] == 0, sink.stats()
    assert sink.stats()["redis_dropped"] >= 50, sink.stats()


def test_resolve_auth_explicit_wins():
    from redis_sink import resolve_auth
    assert resolve_auth("u", "p", environ={}) == ("u", "p")
    # explicit args beat the environment
    assert resolve_auth("u", "p", environ={"REDIS_USERNAME": "x",
                                            "REDIS_PASSWORD": "y"}) == ("u", "p")


def test_resolve_auth_env_fallback():
    from redis_sink import resolve_auth
    assert resolve_auth(None, None,
                        environ={"REDIS_USERNAME": "x", "REDIS_PASSWORD": "y"}) == ("x", "y")
    # an empty explicit value falls back to env; an unset user resolves to None (not "")
    assert resolve_auth("", "", environ={"REDIS_PASSWORD": "y"}) == (None, "y")


def test_resolve_auth_all_unset():
    from redis_sink import resolve_auth
    assert resolve_auth(None, None, environ={}) == (None, None)


def test_sink_default_connect_gets_auth():
    # RedisSink with no injected connect must hand host/port AND the creds to the real
    # default factory. Swap _default_connect for a recorder so no redis-py is needed.
    import redis_sink
    seen = {}
    orig = redis_sink._default_connect
    redis_sink._default_connect = (lambda host, port, username=None, password=None:
                                   seen.update(host=host, port=port,
                                               username=username, password=password)
                                   or FakeRedis())
    try:
        sink = redis_sink.RedisSink(host="h", port=1234, username="u", password="p")
        sink._connect()                      # trigger the default factory
    finally:
        redis_sink._default_connect = orig
    assert seen == {"host": "h", "port": 1234, "username": "u", "password": "p"}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all redis_sink tests passed")
