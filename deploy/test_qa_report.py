"""Unit tests for qa_report pure helpers (no Redis, no board, no files).
Run: python deploy/test_qa_report.py   or   pytest deploy -q"""
import csv
import os
import tempfile

from qa_report import coverage, find_holes, Verdict, NS, scan_archive, GPS_EVENT


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "sec", "ns", "event"])
        for sec, ns, ev in rows:
            w.writerow(["%d-%d" % (sec * 1000 + ns // 1_000_000, ns % 1_000_000),
                        sec, ns, ev])


def test_scan_archive_matches_the_in_memory_helpers():
    # the streaming scan must agree exactly with coverage()/find_holes(), which are
    # the versions the other tests pin
    rows = [(1000, 0, 0x0C), (1001, 0, 0x0F), (1002, 0, 0x0C), (1010, 0, 0x0C)]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "events-tclk-20260804.csv")
        _write_csv(p, rows)
        sc = scan_archive([p], hole_ns=2 * NS)
    events = [(s * NS + n, e) for s, n, e in rows]
    cov = coverage(events)
    assert sc.n == cov.n == 4
    assert sc.span_s == cov.span_s
    assert sc.per_code == cov.per_code
    assert sc.holes == find_holes([t for t, _ in events], 2 * NS)


def test_scan_archive_collects_only_gps_markers_not_every_event():
    # holding every event is what made this unusable on a 1.7 M-row archive
    rows = [(1000 + i, 0, GPS_EVENT if i % 10 == 0 else 0x0C) for i in range(100)]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "events-tclk-20260804.csv")
        _write_csv(p, rows)
        sc = scan_archive([p], hole_ns=NS)
    assert sc.n == 100
    assert len(sc.markers) == 10                 # only the $8F times are retained


def test_scan_archive_skips_torn_and_short_rows():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "events-tclk-20260804.csv")
        with open(p, "w", newline="") as f:
            f.write("id,sec,ns,event\n")
            f.write("1000000-0,1000,0,12\n")
            f.write("short,row\n")                # torn last line of a live capture
            f.write("1001000-0,1001,0,15\n")
            f.write("bad,notanumber,0,12\n")
        sc = scan_archive([p], hole_ns=NS)
    assert sc.n == 2


def test_scan_archive_handles_no_files():
    sc = scan_archive([], hole_ns=NS)
    assert sc.n == 0 and sc.markers == [] and sc.holes == []

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
