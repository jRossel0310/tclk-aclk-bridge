"""Unit tests for verify_no_loss pure helpers (no Redis, no board).
Run: python deploy/test_verify_no_loss.py   or   pytest deploy -q"""
from verify_no_loss import analyse_counts, CodeReport


def test_contiguous_counts_report_no_loss():
    r = analyse_counts(0x0C, [1, 2, 3, 4, 5])
    assert isinstance(r, CodeReport)
    assert r.lost == 0 and r.gaps == [] and r.restarts == 0
    assert r.first == 1 and r.last == 5 and r.n == 5


def test_a_single_missing_count_is_one_lost_event():
    r = analyse_counts(0x0C, [1, 2, 4, 5])
    assert r.lost == 1
    assert r.gaps == [(2, 4)]


def test_a_wide_gap_counts_every_missing_event():
    r = analyse_counts(0x0C, [10, 25])
    assert r.lost == 14 and r.gaps == [(10, 25)]


def test_several_gaps_are_all_reported():
    r = analyse_counts(0x0C, [1, 3, 4, 9])
    assert r.gaps == [(1, 3), (4, 9)]
    assert r.lost == 1 + 4


def test_a_window_that_starts_mid_sequence_is_not_loss():
    # MAXLEN trims old entries, so the retained window rarely starts at 1
    r = analyse_counts(0x0C, [8123, 8124, 8125])
    assert r.lost == 0 and r.first == 8123


def test_a_counter_going_backwards_is_a_restart_not_loss():
    # counters reset to zero when the publisher restarts; that is the documented signal
    r = analyse_counts(0x0C, [7, 8, 1, 2, 3])
    assert r.restarts == 1
    assert r.lost == 0 and r.gaps == []


def test_a_gap_after_a_restart_is_still_counted():
    r = analyse_counts(0x0C, [7, 8, 1, 2, 5])
    assert r.restarts == 1 and r.lost == 2 and r.gaps == [(2, 5)]


def test_a_repeated_count_is_a_duplicate_not_a_gap():
    r = analyse_counts(0x0C, [1, 2, 2, 3])
    assert r.lost == 0 and r.duplicates == 1


def test_empty_and_single_entry_streams_are_handled():
    assert analyse_counts(0x0C, []).n == 0
    r = analyse_counts(0x0C, [42])
    assert r.n == 1 and r.lost == 0 and r.first == 42 and r.last == 42


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all verify_no_loss tests passed")
