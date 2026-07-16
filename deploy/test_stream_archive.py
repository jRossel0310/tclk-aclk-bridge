"""Unit tests for stream_archive (no Redis server, no board).
Run: python test_stream_archive.py   or   pytest deploy -q"""
import csv
import json
import os
import tempfile

from stream_archive import (
    HEADER, row_from_entry, DailyCsv, drain_source, load_state, save_state,
)


class FakeStreamRedis:
    """Stub of the one redis-py call the archiver uses: xrange with an
    optional exclusive '(' min bound and a count limit."""
    def __init__(self, entries):
        self.entries = entries          # list of (id, fields), ascending

    @staticmethod
    def _key(eid):
        ms, seq = eid.split("-")
        return (int(ms), int(seq))

    def xrange(self, stream, min="-", max="+", count=None):
        excl = isinstance(min, str) and min.startswith("(")
        lo = None if min == "-" else self._key(min[1:] if excl else min)
        out = []
        for eid, f in self.entries:
            k = self._key(eid)
            if lo is not None and (k < lo or (excl and k == lo)):
                continue
            out.append((eid, dict(f)))
            if count is not None and len(out) >= count:
                break
        return out


def _entries(n, ms0=1000):
    return [("%d-0" % (ms0 + i),
             {"sec": "1", "ns": str(i), "event": "7", "data": "0"})
            for i in range(n)]


def test_row_from_entry_schema_and_defaults():
    r = row_from_entry("123-0", {"sec": "9", "ns": "8", "event": "29", "data": "5"})
    assert r == ["123-0", "9", "8", "29", "5"]
    r = row_from_entry("124-0", {})            # missing fields never crash
    assert r == ["124-0", "0", "0", "", "0"]


def test_drain_source_batches_and_resumes():
    fake = FakeStreamRedis(_entries(25))
    got = []
    last, n = drain_source(fake, "KR260:tclk", None, got.extend, batch=10)
    assert n == 25 and last == "1024-0"
    assert [g[0] for g in got] == ["%d-0" % (1000 + i) for i in range(25)]
    # resume: nothing new after last
    got2 = []
    last2, n2 = drain_source(fake, "KR260:tclk", last, got2.extend, batch=10)
    assert n2 == 0 and last2 == last and got2 == []
    # resume picks up only newer entries
    fake.entries += _entries(3, ms0=2000)
    got3 = []
    last3, n3 = drain_source(fake, "KR260:tclk", last, got3.extend, batch=10)
    assert n3 == 3 and last3 == "2002-0"


def test_daily_csv_rotates_by_utc_date():
    clock = [1_755_000_000.0]                  # mutable fake wall clock
    with tempfile.TemporaryDirectory() as d:
        w = DailyCsv(d, "tclk", now=lambda: clock[0])
        w.write_rows([["1-0", "1", "2", "7", "0"]])
        clock[0] += 86400.0                    # next UTC day -> new file
        w.write_rows([["2-0", "1", "3", "7", "0"]])
        w.close()
        files = sorted(os.listdir(d))
        assert len(files) == 2 and all(f.startswith("events-tclk-") for f in files)
        with open(os.path.join(d, files[0]), newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0] == HEADER and rows[1][0] == "1-0"


def test_state_roundtrip_and_missing():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "archive-state.json")
        assert load_state(p) == {}             # missing file -> empty
        save_state(p, {"tclk": "5-0"})
        assert load_state(p) == {"tclk": "5-0"}
        assert json.load(open(p)) == {"tclk": "5-0"}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all stream_archive tests passed")
