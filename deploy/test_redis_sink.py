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
    """Records committed pipeline ops in `ops`. First `fail_times` pipelines raise on
    execute(), to exercise the sink's reconnect/drop path."""
    def __init__(self, fail_times=0):
        self.ops = []
        self.fail_times = fail_times

    def pipeline(self, transaction=False):
        fail = self.fail_times > 0
        if fail:
            self.fail_times -= 1
        return FakePipe(self.ops, fail)


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all redis_sink tests passed")
