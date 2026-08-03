"""Unit tests for redis_smoketest pure helpers (no Redis, no board).
Run: python deploy/test_redis_smoketest.py   or   pytest deploy -q"""
import time

from redis_smoketest import (smoketest_keys, is_production_base, SMOKE_EVENTS,
                             watchdog_verdict, SMOKE_WATCHDOG_TTL, wait_until)


def test_wait_until_returns_true_as_soon_as_the_predicate_holds():
    calls = []
    assert wait_until(lambda: len(calls) >= 3 or calls.append(1), timeout=2.0,
                      interval=0.01) is True


def test_wait_until_returns_false_on_timeout_without_hanging():
    t0 = time.monotonic()
    assert wait_until(lambda: False, timeout=0.2, interval=0.01) is False
    assert time.monotonic() - t0 < 1.5          # bounded, does not spin forever


def test_wait_until_checks_before_sleeping():
    # an already-satisfied predicate must not cost a full interval
    t0 = time.monotonic()
    assert wait_until(lambda: True, timeout=5.0, interval=2.0) is True
    assert time.monotonic() - t0 < 0.5


def test_watchdog_ttl_is_long_enough_to_survive_verification():
    # the production publisher uses a 1 s TTL, but the smoke test reads the field back
    # only after several remote round trips; a 1 s TTL expires first and reads as failure
    assert SMOKE_WATCHDOG_TTL >= 30


def test_watchdog_verdict_accepts_a_live_ttl():
    ok, msg = watchdog_verdict([30], SMOKE_WATCHDOG_TTL)
    assert ok is True, msg
    ok, _ = watchdog_verdict([1], SMOKE_WATCHDOG_TTL)
    assert ok is True                       # counted down but still alive


def test_watchdog_verdict_rejects_a_field_with_no_ttl():
    # -1 is the dangerous case: HEXPIRE was a no-op, so liveness would never expire
    ok, msg = watchdog_verdict([-1], SMOKE_WATCHDOG_TTL)
    assert ok is False
    assert "no TTL" in msg and "HEXPIRE" in msg


def test_watchdog_verdict_rejects_a_missing_field():
    ok, msg = watchdog_verdict([-2], SMOKE_WATCHDOG_TTL)
    assert ok is False
    assert "did not exist" in msg or "missing" in msg


def test_watchdog_verdict_rejects_a_ttl_above_what_we_set():
    ok, msg = watchdog_verdict([9999], SMOKE_WATCHDOG_TTL)
    assert ok is False


def test_watchdog_verdict_handles_an_empty_reply():
    ok, msg = watchdog_verdict([], SMOKE_WATCHDOG_TTL)
    assert ok is False and msg


def test_keys_are_exactly_what_the_publisher_creates():
    # the cleanup deletes THIS list, never a wildcard scan: it may run against a shared
    # lab server where deleting someone else's key is unrecoverable
    keys = smoketest_keys("KR260-SMOKETEST", [(0x1D, 0), (0x07, 0), (0x07, 0)])
    assert keys == [
        "{KR260-SMOKETEST}:1D", "{KR260-SMOKETEST}:1D_C",
        "{KR260-SMOKETEST}:07", "{KR260-SMOKETEST}:07_C",
        "{KR260-SMOKETEST}:STREAM", "{KR260-SMOKETEST}:watchdog",
    ]


def test_keys_deduplicate_repeated_codes_and_keep_order():
    keys = smoketest_keys("B", [(0x01, 0), (0x01, 0), (0x02, 0)])
    assert keys.count("{B}:01") == 1
    assert keys.index("{B}:01") < keys.index("{B}:02") < keys.index("{B}:STREAM")


def test_keys_never_include_an_unbraced_or_wildcard_entry():
    for k in smoketest_keys("X", SMOKE_EVENTS):
        assert k.startswith("{X}:") and "*" not in k


def test_production_base_is_recognised_case_insensitively():
    assert is_production_base("TCLK") is True
    assert is_production_base("tclk") is True
    assert is_production_base("TCLK-MULTICAST") is True     # the admin's other producer
    assert is_production_base("KR260-SMOKETEST") is False
    assert is_production_base("TCLKTEST") is False


def test_smoke_events_cover_a_repeated_code():
    # the counter stream is only meaningful if one code occurs more than once
    codes = [e for e, _ in SMOKE_EVENTS]
    assert len(codes) > len(set(codes))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all redis_smoketest tests passed")
