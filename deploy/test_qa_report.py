"""Unit tests for qa_report pure helpers (no Redis, no board, no files).
Run: python deploy/test_qa_report.py   or   pytest deploy -q"""
from qa_report import coverage, find_holes, Verdict, NS

SEC0 = 1_785_774_000


def _ev(sec_off, ns, code):
    return ((SEC0 + sec_off) * NS + ns, code)


# ---- coverage ----

def test_coverage_span_count_and_rate():
    events = [_ev(0, 0, 0x0C), _ev(1, 0, 0x0F), _ev(2, 0, 0x0C), _ev(4, 0, 0x0C)]
    c = coverage(events)
    assert c.n == 4
    assert c.span_s == 4.0
    assert c.rate == 1.0                      # 4 events over a 4 s span
    assert c.per_code[0x0C] == 3 and c.per_code[0x0F] == 1
    assert c.distinct == 2


def test_coverage_of_an_empty_archive():
    c = coverage([])
    assert c.n == 0 and c.span_s == 0.0 and c.rate == 0.0 and c.distinct == 0


def test_coverage_of_a_single_event_does_not_divide_by_zero():
    c = coverage([_ev(0, 0, 0x0C)])
    assert c.n == 1 and c.span_s == 0.0 and c.rate == 0.0


def test_coverage_is_order_independent():
    a = coverage([_ev(0, 0, 1), _ev(5, 0, 2)])
    b = coverage([_ev(5, 0, 2), _ev(0, 0, 1)])
    assert (a.n, a.span_s, a.distinct) == (b.n, b.span_s, b.distinct)


# ---- timeline holes ----

def test_no_holes_in_a_dense_timeline():
    times = [SEC0 * NS + i * NS // 100 for i in range(500)]     # 100 Hz
    assert find_holes(times, min_gap_ns=NS) == []


def test_a_hole_is_reported_with_its_size():
    times = [SEC0 * NS, SEC0 * NS + 5 * NS]
    holes = find_holes(times, min_gap_ns=NS)
    assert len(holes) == 1
    start, end, gap = holes[0]
    assert start == SEC0 * NS and end == SEC0 * NS + 5 * NS and gap == 5 * NS


def test_a_gap_exactly_at_the_threshold_is_not_a_hole():
    times = [SEC0 * NS, SEC0 * NS + NS]
    assert find_holes(times, min_gap_ns=NS) == []


def test_holes_are_found_in_unsorted_input():
    times = [SEC0 * NS + 9 * NS, SEC0 * NS]
    assert len(find_holes(times, min_gap_ns=NS)) == 1


def test_empty_and_single_timelines_have_no_holes():
    assert find_holes([], min_gap_ns=NS) == []
    assert find_holes([SEC0 * NS], min_gap_ns=NS) == []


# ---- verdict accumulation ----

def test_a_clean_verdict_passes_with_exit_zero():
    v = Verdict()
    v.ok("loss", "contiguous")
    assert v.passed is True and v.exit_code == 0


def test_a_failure_makes_the_verdict_fail():
    v = Verdict()
    v.ok("loss", "contiguous")
    v.fail("loss", "3 events missing")
    assert v.passed is False and v.exit_code == 1
    assert "3 events missing" in v.summary()


def test_an_advisory_does_not_fail_the_run():
    # the -3.48 ppm clock offset is a known condition, not a reason to fail a capture
    v = Verdict()
    v.advise("clock", "-3.48 ppm vs GPS")
    assert v.passed is True and v.exit_code == 0
    assert "advisory" in v.summary().lower()


def test_a_skipped_section_neither_passes_nor_fails():
    v = Verdict()
    v.skip("archive", "no CSVs found (running off-board?)")
    assert v.passed is True and v.exit_code == 0
    assert "skip" in v.summary().lower()


def test_summary_reports_every_failure_not_just_the_first():
    v = Verdict()
    v.fail("loss", "gap in 0C")
    v.fail("ledger", "decoded != published")
    s = v.summary()
    assert "gap in 0C" in s and "decoded != published" in s


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all qa_report tests passed")
