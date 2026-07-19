"""Unit tests for ra_consumer (no Redis, no board).
Run: python deploy/test_ra_consumer.py   or   pytest deploy -q"""
import struct

from ra_consumer import (
    stream_key, ra_time_from_id, decode_payload, decode_entry, PAYLOAD_LEN,
)


def test_stream_key_is_braced():
    assert stream_key("KR260", "tclk") == "{KR260}:tclk"


def test_payload_len_is_15():
    assert PAYLOAD_LEN == 15


def test_ra_time_from_id_str_and_bytes():
    sec, ns = 1_751_800_000, 123_456_789
    ra = sec * 1_000_000_000 + ns
    ms, seq = divmod(ra, 1_000_000)
    assert ra_time_from_id("%d-%d" % (ms, seq)) == ra
    assert ra_time_from_id(("%d-%d" % (ms, seq)).encode()) == ra


def test_decode_payload():
    buf = struct.pack("<IIIHB", 1_751_800_000, 1500, 0xABCD, 0x07, 0x03)
    d = decode_payload(buf)
    assert d == {"sec": 1_751_800_000, "ns": 1500, "data": 0xABCD,
                 "event": 0x07, "is_tclk": 1, "has_data": 1}


def test_decode_entry_reads_only_underscore():
    buf = struct.pack("<IIIHB", 5, 6, 9, 0x1D, 0x02)
    e = decode_entry(b"1000-500", {b"_": buf, b"sec": b"5"})
    assert e["event"] == 0x1D and e["is_tclk"] == 1 and e["has_data"] == 0
    assert e["ra_time"] == 1000 * 1_000_000 + 500


def test_decode_entry_missing_underscore_raises():
    try:
        decode_entry("1-0", {"sec": "5"})
        raised = False
    except ValueError:
        raised = True
    assert raised


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all ra_consumer tests passed")
