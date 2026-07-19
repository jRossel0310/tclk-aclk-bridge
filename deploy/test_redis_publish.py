"""Unit tests for redis_publish pure helpers (no hardware, no Redis).
Run: python deploy/test_redis_publish.py   or   pytest deploy -q"""
from redis_publish import event_fields, should_publish


def test_event_fields_schema():
    SEC = 1_751_800_000
    f = event_fields(0x07, 0x03, 0xABCD, (SEC << 32) | 1500, "tclk")
    assert f["sec"] == str(SEC) and f["ns"] == "1500"
    assert f["event"] == "7" and f["data"] == str(0xABCD)
    assert f["is_tclk"] == "1" and f["has_data"] == "1"
    assert f["src"] == "tclk"
    # no per-entry utc on the stream (hot-path cost); consumers derive it from sec/ns
    assert set(f.keys()) == {"sec", "ns", "event", "data",
                             "is_tclk", "has_data", "src"}
    assert all(isinstance(v, str) for v in f.values())


def test_event_fields_flag_variants():
    f = event_fields(0x18, 0x00, 0, (1 << 32), "aclk")   # is_tclk=0 has_data=0
    assert f["is_tclk"] == "0" and f["has_data"] == "0" and f["src"] == "aclk"


def test_should_publish_drops_unsync():
    assert should_publish(0) is False
    assert should_publish((1 << 32) | 5) is True


def test_build_record():
    from redis_publish import build_record
    SEC = 1_751_800_000
    NS = 123_456_789
    r = build_record("KR260", "tclk", 0x1D, 0x02, 0, (SEC << 32) | NS)
    assert r["stream"] == "{KR260}:tclk"
    assert r["index_key"] == "{KR260}:event:tclk:0x1D"
    assert r["id_ms"] == SEC * 1000 + NS // 1_000_000       # ...*1000 + 123
    assert r["fields"]["event"] == str(0x1D) and r["fields"]["src"] == "tclk"
    assert "utc" not in r["fields"]                       # stream entries carry sec/ns only
    assert r["index_fields"]["sec"] == str(SEC) and r["index_fields"]["ns"] == str(NS)
    assert r["index_fields"]["data"] == "0"
    assert r["index_fields"]["utc"].startswith("20") and r["index_fields"]["utc"].endswith("Z")
    # a wide (16-bit) ACLK event still formats sensibly
    r2 = build_record("KR260", "aclk", 0xABCD, 0x01, 5, (SEC << 32) | NS)
    assert r2["stream"] == "{KR260}:aclk"
    assert r2["index_key"] == "{KR260}:event:aclk:0xABCD"
    assert r2["index_fields"]["data"] == "5"


def test_publisher_state_counts():
    from redis_publish import PublisherState
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
