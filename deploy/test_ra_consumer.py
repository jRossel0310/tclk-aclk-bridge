"""Unit tests for ra_consumer (no Redis, no board).

Covers the {TCLK} key space published for the lab's redis-clock-server deployment:
sub-keys <HEX>, <HEX>_C, STREAM, watchdog, and the raw little-endian `_` payloads.
Run: python deploy/test_ra_consumer.py   or   pytest deploy -q"""
import struct

from ra_consumer import (
    ts_key, count_key, stream_key, watchdog_key,
    ra_time_from_id, decode_int64, decode_event_id,
    TIME_LEN, COUNT_LEN, EVENT_LEN,
)


def test_ts_key_is_braced_two_digit_uppercase_hex():
    assert ts_key("TCLK", 0x1D) == "{TCLK}:1D"
    assert ts_key("TCLK", 0x0F) == "{TCLK}:0F"
    assert ts_key("TCLK", 0xAB) == "{TCLK}:AB"
    assert ts_key("TCLK", 0) == "{TCLK}:00"


def test_count_key_appends_underscore_c():
    assert count_key("TCLK", 0x1D) == "{TCLK}:1D_C"
    assert count_key("TCLK", 0x07) == "{TCLK}:07_C"


def test_stream_and_watchdog_keys():
    assert stream_key("TCLK") == "{TCLK}:STREAM"
    assert watchdog_key("TCLK") == "{TCLK}:watchdog"


def test_payload_widths_match_the_contract():
    # widths are load-bearing: a consumer reading the wrong one gets garbage, not an error
    assert (TIME_LEN, COUNT_LEN, EVENT_LEN) == (8, 8, 2)


def test_ra_time_from_id_str_and_bytes():
    sec, ns = 1_751_800_000, 123_456_789
    ra = sec * 1_000_000_000 + ns
    ms, seq = divmod(ra, 1_000_000)
    assert ra_time_from_id("%d-%d" % (ms, seq)) == ra
    assert ra_time_from_id(("%d-%d" % (ms, seq)).encode()) == ra


def test_decode_int64_reads_little_endian_underscore():
    ra = 1_751_800_000_123_456_789
    assert decode_int64({b"_": struct.pack("<q", ra)}) == ra


def test_decode_int64_accepts_str_keyed_field_map():
    assert decode_int64({"_": struct.pack("<q", 42)}) == 42


def test_decode_event_id_reads_uint16():
    assert decode_event_id({b"_": struct.pack("<H", 0x1D)}) == 0x1D
    assert decode_event_id({b"_": struct.pack("<H", 0xFF)}) == 0xFF


def test_decode_missing_underscore_raises():
    for fn in (decode_int64, decode_event_id):
        try:
            fn({b"sec": b"5"})
            raised = False
        except ValueError:
            raised = True
        assert raised, fn


def test_decode_wrong_width_raises_rather_than_returning_garbage():
    # an 8-byte payload read as an event id must fail loudly
    try:
        decode_event_id({b"_": struct.pack("<q", 1)})
        raised = False
    except struct.error:
        raised = True
    assert raised


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all ra_consumer tests passed")
