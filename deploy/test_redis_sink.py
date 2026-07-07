"""Unit tests for redis_sink.RedisSink (no hardware, no Redis server).
A stub Redis records XADDs and can be told to fail to exercise reconnect.
Run: python deploy/test_redis_sink.py   or   pytest deploy -q"""
import time

from redis_sink import RedisSink


class FakePipe:
    def __init__(self, added, fail):
        self.added = added
        self.fail = fail
        self.ops = []

    def xadd(self, stream, fields, maxlen=None, approximate=None):
        self.ops.append((stream, dict(fields), maxlen, approximate))

    def execute(self):
        if self.fail:
            raise RuntimeError("redis down")
        self.added.extend(self.ops)
        self.ops = []


class FakeRedis:
    """Records committed XADDs in `added`. First `fail_times` pipelines raise on
    execute(), to exercise the sink's reconnect/drop path."""
    def __init__(self, fail_times=0):
        self.added = []
        self.fail_times = fail_times
        self.pipelines = 0

    def pipeline(self, transaction=False):
        self.pipelines += 1
        fail = self.fail_times > 0
        if fail:
            self.fail_times -= 1
        return FakePipe(self.added, fail)


def _wait(pred, timeout=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _fields(n):
    return {"sec": str(n), "ns": "0", "event": "7", "src": "tclk"}


def test_field_mapping_and_maxlen():
    fake = FakeRedis()
    sink = RedisSink("events:tclk", maxlen=555, connect=lambda: fake)
    sink.start()
    sink.submit(_fields(1))
    assert _wait(lambda: len(fake.added) >= 1), sink.stats()
    sink.stop()
    stream, fields, maxlen, approx = fake.added[0]
    assert stream == "events:tclk"
    assert fields == _fields(1)
    assert maxlen == 555 and approx is True
    assert sink.stats()["published"] == 1


def test_queue_full_drops_oldest():
    # No writer started, so the queue never drains: submit 3 into a size-2 queue.
    sink = RedisSink("s", queue_size=2, connect=lambda: FakeRedis())
    sink.submit(_fields(1))   # A
    sink.submit(_fields(2))   # B
    sink.submit(_fields(3))   # C -> full, drop oldest (A), enqueue C -> [B, C]
    assert sink.stats()["queue_dropped"] == 1
    drained = [sink._q.get_nowait(), sink._q.get_nowait()]
    assert drained == [_fields(2), _fields(3)]


def test_reconnect_after_error():
    fake = FakeRedis(fail_times=1)          # first execute() raises
    sink = RedisSink("s", connect=lambda: fake)
    sink.start()
    sink.submit(_fields(1))                 # first batch fails -> redis_dropped, reconnect
    assert _wait(lambda: sink.stats()["reconnects"] >= 1), sink.stats()
    sink.submit(_fields(2))                 # after reconnect, this one lands
    assert _wait(lambda: sink.stats()["published"] >= 1), sink.stats()
    sink.stop()
    assert sink.stats()["redis_dropped"] >= 1


def test_stop_flushes_queue():
    fake = FakeRedis()
    sink = RedisSink("s", connect=lambda: fake)
    for i in range(50):
        sink.submit(_fields(i))
    sink.start()
    sink.stop(timeout=3.0)
    assert len(fake.added) == 50
    assert sink.stats()["published"] == 50 and sink.stats()["queued"] == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all redis_sink tests passed")
