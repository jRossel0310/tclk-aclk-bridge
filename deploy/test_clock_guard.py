"""Unit tests for the pre-arm system-clock guard (no hardware, no chrony).
Run: python deploy/test_clock_guard.py   or   pytest deploy -q"""
from clock_guard import (clock_is_trustworthy, parse_chrony_tracking,
                         timedatectl_synchronized, MAX_ARM_OFFSET_S)

# Real output from aclk-timestamper, 2026-08-03
CHRONY_GOOD = """Reference ID    : 83E17991 (chablis.fnal.gov)
Stratum         : 3
Ref time (UTC)  : Mon Aug 03 21:25:11 2026
System time     : 0.000004709 seconds fast of NTP time
Last offset     : +0.000004989 seconds
RMS offset      : 0.000021751 seconds
Frequency       : 2.192 ppm fast
Residual freq   : +0.001 ppm
Skew            : 0.026 ppm
Root delay      : 0.000411626 seconds
Root dispersion : 0.018570010 seconds
Update interval : 256.9 seconds
Leap status     : Normal
"""

CHRONY_UNSYNCED = CHRONY_GOOD.replace(
    "Reference ID    : 83E17991 (chablis.fnal.gov)",
    "Reference ID    : 00000000 ()").replace(
    "Leap status     : Normal", "Leap status     : Not synchronised")

CHRONY_FAR_OFF = CHRONY_GOOD.replace(
    "System time     : 0.000004709 seconds fast of NTP time",
    "System time     : 4.117300000 seconds slow of NTP time")

TD_SYNCED   = "System clock synchronized: yes\n              NTP service: active\n"
TD_UNSYNCED = "System clock synchronized: no\n              NTP service: active\n"


def test_parses_the_offset_and_reference():
    t = parse_chrony_tracking(CHRONY_GOOD)
    assert t["ref_id"] == "83E17991"
    assert t["stratum"] == 3
    assert abs(t["system_time_s"] - 4.709e-6) < 1e-12
    assert t["leap"] == "Normal"


def test_offset_sign_is_captured():
    # "slow of NTP time" means the clock is BEHIND, so the offset is negative
    t = parse_chrony_tracking(CHRONY_FAR_OFF)
    assert t["system_time_s"] < 0
    assert abs(t["system_time_s"]) > 4.0


def test_a_healthy_clock_is_trustworthy():
    ok, why = clock_is_trustworthy(TD_SYNCED, CHRONY_GOOD)
    assert ok is True, why


def test_unsynchronized_timedatectl_blocks_arming():
    ok, why = clock_is_trustworthy(TD_UNSYNCED, CHRONY_GOOD)
    assert ok is False
    assert "synchronized" in why.lower()


def test_no_chrony_reference_blocks_arming():
    # this board really does hit "Can't synchronise: no selectable sources"
    ok, why = clock_is_trustworthy(TD_SYNCED, CHRONY_UNSYNCED)
    assert ok is False
    assert "reference" in why.lower() or "synchronis" in why.lower()


def test_a_large_offset_blocks_arming():
    # the 2026-08-03 failure: arming against a clock that was seconds off wrote a
    # whole-second error into every timestamp, invisibly
    ok, why = clock_is_trustworthy(TD_SYNCED, CHRONY_FAR_OFF)
    assert ok is False
    assert "offset" in why.lower()


def test_the_threshold_is_tight_enough_to_catch_a_whole_second():
    assert MAX_ARM_OFFSET_S < 1.0


def test_missing_chrony_output_blocks_rather_than_assumes_good():
    # chrony absent must never be read as "clock is fine"
    ok, why = clock_is_trustworthy(TD_SYNCED, "")
    assert ok is False


def test_timedatectl_parser_is_whitespace_tolerant():
    assert timedatectl_synchronized("System clock synchronized: yes") is True
    assert timedatectl_synchronized("   System clock synchronized:   yes  ") is True
    assert timedatectl_synchronized("System clock synchronized: no") is False
    assert timedatectl_synchronized("") is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all clock_guard tests passed")
