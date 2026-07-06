"""Unit tests for wr_time helpers (no hardware).
Run: python deploy/test_wr_time.py   or   pytest deploy -q"""
from wr_time import next_pps_label, decode_status, ARM_FRAC_LO, ARM_FRAC_HI


def test_next_pps_label():
    assert next_pps_label(1_751_800_000.5) == 1_751_800_001
    assert next_pps_label(1_751_800_000.11) == 1_751_800_001


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
