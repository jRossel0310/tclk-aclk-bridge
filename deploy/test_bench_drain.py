from bench_drain import measure_rate


class FakeReader:
    def __init__(self, per_call, overflow_at=None):
        self.per_call = per_call
        self.calls = 0
        self.overflow_at = overflow_at
    def drain_once(self):
        self.calls += 1
        return self.per_call
    def overflow(self):
        return self.overflow_at is not None and self.calls >= self.overflow_at


def test_rate_and_overflow():
    # fake clock: 0.0, then 0.5s per drain_once, stop once elapsed >= 1.0s
    ticks = iter([0.0, 0.5, 1.0, 1.0])
    r = FakeReader(per_call=10, overflow_at=2)
    out = measure_rate(r, seconds=1.0, now=lambda: next(ticks))
    assert out["reads"] == 20            # two drain_once calls * 10
    assert out["elapsed_s"] == 1.0
    assert out["reads_per_s"] == 20.0
    assert out["overflow"] is True
