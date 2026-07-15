"""Unit tests for redis_sink.RedisSink (no hardware, no Redis server).
A stub Redis records the pipelined ops (xadd/hset/hincrby) and can be told to fail
to exercise reconnect. Run: python deploy/test_redis_sink.py   or   pytest deploy -q"""
import time

from redis_sink import RedisSink


class FakePipe:
    def __init__(self, ops_log, fail):
        self.ops_log = ops_log      # committed ops land here on execute()
        self.fail = fail
        self.pending = []

    def xadd(self, stream, fields, id=None, maxlen=None, approximate=None):
        self.pending.append(("xadd", stream, dict(fields), id, maxlen, approximate))

    def hset(self, key, mapping=None):
        self.pending.append(("hset", key, dict(mapping)))

    def hincrby(self, key, field, amount):
        self.pending.append(("hincrby", key, field, amount))

    def execute(self):
        if self.fail:
            raise RuntimeError("redis down")
        self.ops_log.extend(self.pending)
        self.pending = []


class FakeRedis:
    """Records committed pipeline ops in `ops`, and set() calls in `kv`. First
    `fail_times` pipelines raise on execute(), to exercise reconnect/drop."""
    def __init__(self, fail_times=0):
        self.ops = []
        self.kv = {}
        self.fail_times = fail_times
        self.set_calls = 0
        self.fail_set = False      # if True, set() always raises (watchdog fully down)

    def pipeline(self, transaction=False):
        fail = self.fail_times > 0
        if fail:
            self.fail_times -= 1
        return FakePipe(self.ops, fail)

    def set(self, key, value, ex=None):
        if self.fail_set:
            raise RuntimeError("redis down")
        self.set_calls += 1
        self.kv[key] = (value, ex)


def _wait(pred, timeout=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _record(ms, event="7"):
    return {
        "stream": "KR260:tclk",
        "id_ms": ms,
        "fields": {"sec": "1", "ns": "0", "event": event, "src": "tclk"},
        "index_key": "KR260:event:tclk:0x%02X" % int(event),
        "index_fields": {"sec": "1", "ns": "0", "data": "0"},
    }


def _xadds(fake):
    return [o for o in fake.ops if o[0] == "xadd"]


def test_record_pipelines_xadd_hset_hincrby():
    fake = FakeRedis()
    sink = RedisSink(maxlen=555, connect=lambda: fake)
    sink.start()
    sink.submit(_record(1000, event="29"))       # 29 == 0x1D
    assert _wait(lambda: len(fake.ops) >= 3), sink.stats()
    sink.stop()
    kinds = [o[0] for o in fake.ops]
    assert kinds == ["xadd", "hset", "hincrby"], fake.ops
    _, stream, fields, sid, maxlen, approx = fake.ops[0]
    assert stream == "KR260:tclk"
    assert fields == {"sec": "1", "ns": "0", "event": "29", "src": "tclk"}
    assert sid == "1000-*" and maxlen == 555 and approx is True
    assert fake.ops[1] == ("hset", "KR260:event:tclk:0x1D", {"sec": "1", "ns": "0", "data": "0"})
    assert fake.ops[2] == ("hincrby", "KR260:event:tclk:0x1D", "count", 1)
    assert sink.stats()["published"] == 1


def test_index_writes_aggregate_per_batch():
    # 5 events of one code + 1 of another, all drained in one batch: 6 XADDs but only
    # one HSET + one HINCRBY per code, with the count summed exactly.
    fake = FakeRedis()
    sink = RedisSink(connect=lambda: fake)
    for i in range(5):
        sink.submit(_record(1000 + i, event="7"))
    sink.submit(_record(2000, event="12"))               # 12 == 0x0C
    sink.start()
    sink.stop(timeout=3.0)
    assert len(_xadds(fake)) == 6
    hsets = [o for o in fake.ops if o[0] == "hset"]
    incs = {o[1]: o[3] for o in fake.ops if o[0] == "hincrby"}
    assert len(hsets) == 2, fake.ops
    assert incs == {"KR260:event:tclk:0x07": 5, "KR260:event:tclk:0x0C": 1}, incs
    assert sink.stats()["published"] == 6


def test_monotonic_id_guard():
    fake = FakeRedis()
    sink = RedisSink(connect=lambda: fake)
    sink.submit(_record(1000))
    sink.submit(_record(500))      # backward jump: must be clamped up to 1000
    sink.submit(_record(2000))     # forward: passes through
    sink.start()
    assert _wait(lambda: len(_xadds(fake)) >= 3), sink.stats()
    sink.stop()
    ids = [o[3] for o in _xadds(fake)]
    assert ids == ["1000-*", "1000-*", "2000-*"], ids


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
    assert len(_xadds(fake)) == 50
    assert sink.stats()["published"] == 50 and sink.stats()["queued"] == 0


def test_status_and_watchdog():
    fake = FakeRedis()
    sink = RedisSink(status_key="KR260:status", watchdog_key="KR260:watchdog",
                     watchdog_ttl=30, watchdog_period=0, connect=lambda: fake)
    sink.start()
    assert _wait(lambda: "KR260:status" in fake.kv and "KR260:watchdog" in fake.kv), fake.kv
    sink.stop()
    assert fake.kv["KR260:status"][0] == 1
    _, ex = fake.kv["KR260:watchdog"]
    assert ex == 30


def test_watchdog_only_no_status():
    fake = FakeRedis()
    sink = RedisSink(watchdog_key="KR260:watchdog", watchdog_ttl=30,
                     watchdog_period=0, connect=lambda: fake)
    sink.start()
    assert _wait(lambda: "KR260:watchdog" in fake.kv), fake.kv
    sink.stop()
    assert "KR260:status" not in fake.kv          # status_key is None -> never written
    _, ex = fake.kv["KR260:watchdog"]
    assert ex == 30


def test_watchdog_error_backs_off():
    class FailSet(FakeRedis):
        def set(self, key, value, ex=None):
            raise RuntimeError("redis down")
    fake = FailSet()
    sink = RedisSink(status_key="KR260:status", watchdog_key="KR260:watchdog",
                     watchdog_period=0, connect=lambda: fake)
    sink.start()
    time.sleep(0.3)
    sink.stop()
    # a persistent Redis error must back off (~0.5 s), not busy-spin: a spin would rack
    # up thousands of reconnects in 0.3 s; the backoff keeps it tiny.
    assert sink.stats()["reconnects"] <= 5, sink.stats()


def test_watchdog_throttle_period():
    fake = FakeRedis()
    sink = RedisSink(watchdog_key="KR260:watchdog", watchdog_period=1000,
                     connect=lambda: fake)
    sink.start()
    assert _wait(lambda: fake.set_calls >= 1), fake.set_calls
    time.sleep(0.05)
    sink.stop()
    # period is 1000 s, so after the first refresh no further watchdog writes happen
    assert fake.set_calls == 1, fake.set_calls


def test_stop_flushes_when_redis_fully_down():
    # set() raises (watchdog down) and every pipeline.execute() raises (writes down).
    # A stopping writer must skip liveness work and go straight to draining the queue
    # (discarding as redis_dropped), not busy-spin on the watchdog path until timeout.
    fake = FakeRedis(fail_times=10_000)
    fake.fail_set = True
    sink = RedisSink(status_key="KR260:status", watchdog_key="KR260:watchdog",
                     watchdog_period=0, connect=lambda: fake)
    for i in range(50):
        sink.submit(_record(1000 + i))
    sink.start()
    sink.stop(timeout=2.0)
    assert sink.stats()["queued"] == 0, sink.stats()
    assert sink.stats()["redis_dropped"] >= 50, sink.stats()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all redis_sink tests passed")
