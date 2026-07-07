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
    assert f["utc"].startswith("20") and f["utc"].endswith("Z")
    assert set(f.keys()) == {"sec", "ns", "utc", "event", "data",
                             "is_tclk", "has_data", "src"}
    assert all(isinstance(v, str) for v in f.values())


def test_event_fields_flag_variants():
    f = event_fields(0x18, 0x00, 0, (1 << 32), "aclk")   # is_tclk=0 has_data=0
    assert f["is_tclk"] == "0" and f["has_data"] == "0" and f["src"] == "aclk"


def test_should_publish_drops_unsync():
    assert should_publish(0) is False
    assert should_publish((1 << 32) | 5) is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all redis_publish tests passed")
