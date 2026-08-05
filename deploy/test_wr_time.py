"""Unit tests for wr_time helpers (no hardware).
Run: python deploy/test_wr_time.py   or   pytest deploy -q"""
from wr_time import (next_pps_label, decode_status, label_error,
                     ARM_FRAC_LO, ARM_FRAC_HI)


def test_next_pps_label():
    assert next_pps_label(1_751_800_000.5) == 1_751_800_001
    assert next_pps_label(1_751_800_000.11) == 1_751_800_001


def test_label_error_unsync_is_none():
    # ts == 0 is the strict UNSYNC stamp: no label to judge.
    assert label_error(0, 0, 1_785_700_000.0) is None


def test_label_error_reports_stale_arm():
    # The 2026-08-04 incident: an arm pended through a 16 s PPS outage, so the relock
    # came up 16 s slow while every health flag stayed green. label_error is the only
    # thing the guard has that can see that.
    now = 1_785_700_016.0
    delta = label_error(1_785_700_000, 0, now)
    assert delta is not None and abs(delta + 16.0) < 1e-6


def test_label_error_healthy_is_small():
    delta = label_error(1_785_700_000, 952_200_000, 1_785_700_001.0)
    assert delta is not None and abs(delta) < 0.5


def test_wrap_half_rides_through_relabels():
    from wr_pps_live import wrap_half, Walk
    # A +16 s relabel landing between two samples must leave the phase difference
    # alone; only the sub-second part carries the rate.
    assert abs(wrap_half((16.000620 % 1.0) - (0.000558 % 1.0)) - 62e-6) < 1e-9
    # Ordinary sub-ms walk passes straight through, whichever sign it has.
    assert abs(wrap_half(0.000614 - 0.000558) - 56e-6) < 1e-12
    assert abs(wrap_half(-0.000200) + 200e-6) < 1e-12
    # Slope estimate: +0.22 ppm walk sampled across a -31 s outage relabel.
    w = Walk()
    for i, (t, d) in enumerate([(0.0, 0.000558), (300.0, 0.000624),
                                (600.0, -30.999310)]):
        w.add(t, d)
    ppm = w.ppm()
    assert ppm is not None and abs(ppm - 0.22) < 0.01


def test_arm_window_constants_leave_margin():
    assert 0.05 <= ARM_FRAC_LO < ARM_FRAC_HI <= 0.95


def test_decode_status():
    d = decode_status(0x0000_0107)
    assert d["locked_tclk"] and d["locked_aclk"] and d["locked_mon"]
    assert not d["pps_alive"] and not d["clk10_alive"] and not d["arm_pending"]
    assert d["lost_lock"]
    d = decode_status(0x0000_0038)
    assert d["pps_alive"] and d["clk10_alive"] and d["arm_pending"]
    assert not d["locked_tclk"] and not d["lost_lock"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all wr_time tests passed")
