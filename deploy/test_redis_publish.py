"""Unit tests for redis_publish pure helpers (no hardware, no Redis).
Run: python deploy/test_redis_publish.py   or   pytest deploy -q"""
import struct

from redis_publish import (
    RecordBuilder, should_publish, PublisherState,
    DEFAULT_BASE, DEFAULT_MAXLEN, DEFAULT_WATCHDOG_TTL, DEFAULT_WATCHDOG_PERIOD,
)

SEC = 1_751_800_000
NS = 123_456_789
TS = (SEC << 32) | NS
RA = SEC * 1_000_000_000 + NS


def test_defaults_match_the_lab_deployment():
    # base key reserved for this decoder; retention and watchdog cadence per their doc
    assert DEFAULT_BASE == "TCLK"
    assert DEFAULT_MAXLEN == 10_000
    assert DEFAULT_WATCHDOG_TTL == 1
    assert DEFAULT_WATCHDOG_PERIOD == 0.9


def test_record_writes_three_streams_in_contract_order():
    r = RecordBuilder("TCLK").build(0x1D, TS)
    assert [k for k, _ in r["writes"]] == ["{TCLK}:1D", "{TCLK}:1D_C", "{TCLK}:STREAM"]


def test_record_ra_time_is_ns_since_epoch():
    r = RecordBuilder("TCLK").build(0x1D, TS)
    assert r["ra_time"] == RA


def test_time_payload_is_int64_ra_time():
    r = RecordBuilder("TCLK").build(0x1D, TS)
    _, fields = r["writes"][0]
    assert list(fields) == ["_"]
    assert struct.unpack("<q", fields["_"])[0] == RA


def test_stream_payload_is_uint16_event_id():
    r = RecordBuilder("TCLK").build(0x1D, TS)
    _, fields = r["writes"][2]
    assert len(fields["_"]) == 2
    assert struct.unpack("<H", fields["_"])[0] == 0x1D


def test_count_starts_at_one_and_increments_per_occurrence():
    rb = RecordBuilder("TCLK")
    counts = [struct.unpack("<q", rb.build(0x1D, TS)["writes"][1][1]["_"])[0]
              for _ in range(3)]
    assert counts == [1, 2, 3]


def test_counts_are_tracked_per_event_code():
    rb = RecordBuilder("TCLK")
    rb.build(0x1D, TS)
    rb.build(0x1D, TS)
    r = rb.build(0x0F, TS)
    assert struct.unpack("<q", r["writes"][1][1]["_"])[0] == 1     # 0x0F's own first


def test_counts_reset_with_a_new_producer():
    # "counters reset to zero when their producer restarts" is the intended signal
    rb = RecordBuilder("TCLK")
    rb.build(0x1D, TS)
    fresh = RecordBuilder("TCLK")
    assert struct.unpack("<q", fresh.build(0x1D, TS)["writes"][1][1]["_"])[0] == 1


def test_event_codes_format_as_two_uppercase_hex_digits():
    rb = RecordBuilder("TCLK")
    assert rb.build(0x00, TS)["writes"][0][0] == "{TCLK}:00"
    assert rb.build(0xAB, TS)["writes"][0][0] == "{TCLK}:AB"
    assert rb.build(0xFF, TS)["writes"][1][0] == "{TCLK}:FF_C"


def test_should_publish_drops_unsync():
    assert should_publish(0) is False
    assert should_publish((1 << 32) | 5) is True


def test_publisher_state_counts():
    st = PublisherState()
    assert st.note(0) is False and st.unsync == 1 and st.drained == 0
    assert st.note((1 << 32) | 5) is True and st.drained == 1 and st.unsync == 1
    st.note(0)
    assert st.unsync == 2 and st.drained == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all redis_publish tests passed")
