"""Unit tests for readout_common (no hardware: RegIO runs on a bytearray).
Run: python test_readout_common.py   or   pytest deploy -q"""
from readout_common import (
    RegIO, read_event, parse_args, dev_offset, apply_drop_filter,
    STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, FILTER_CFG, NAME, GT_CTRL,
    read_hw_counters,
    EVENT_COUNT, NULL_COUNT, ERROR_COUNT, FILTERED_COUNT, LOCK, HEARTBEAT,
)


def make_io():
    return RegIO(bytearray(0x1000))


def test_register_map_16_byte_stride():
    assert (STATUS, EVENT, DATA_HI, DATA_LO) == (0x00, 0x10, 0x20, 0x30)
    assert (TS_HI, TS_LO, POP) == (0x40, 0x50, 0x60)
    assert FILTER_CFG == 0xD0 and GT_CTRL == 0xF0
    assert NAME[EVENT] == "EVENT" and NAME[GT_CTRL] == "GT_CTRL"


def test_rd_wr_roundtrip_little_endian():
    io = make_io()
    io.wr(STATUS, 0xDEADBEEF)
    assert io.rd(STATUS) == 0xDEADBEEF
    assert io.m[0:4] == bytes([0xEF, 0xBE, 0xAD, 0xDE])
    io.wr(EVENT)                      # default value 0
    assert io.rd(EVENT) == 0


def test_read_event_unpacks_fields_and_pops():
    io = make_io()
    io.wr(EVENT, (0x0003 << 16) | 0xABCD)   # flags=3 (is_tclk|has_data), event=0xABCD
    io.wr(DATA_HI, 0xDEADBEEF)
    io.wr(DATA_LO, 0xCAFE0001)
    io.wr(TS_HI, 0x00000001)
    io.wr(TS_LO, 0x00000002)
    io.wr(POP, 0x55555555)                  # pre-load; read_event must overwrite with 0
    event, flags, data, ts = read_event(io)
    assert event == 0xABCD and flags == 0x0003
    assert data == 0xDEADBEEFCAFE0001
    assert ts == 0x0000000100000002
    assert io.rd(POP) == 0


def test_parse_args_matches_old_reader_behavior():
    pos, fl = parse_args(
        ["/dev/uio4", "--drop", "07,0F", "--gtreset", "--unknown"],
        value_flags=("--drop", "--tick-ns"), bool_flags=("--gtreset",))
    assert pos == ["/dev/uio4", "--unknown"]     # unknowns fall through to positionals
    assert fl == {"--drop": "07,0F", "--gtreset": True}
    pos, fl = parse_args([], value_flags=("--drop",))
    assert pos == [] and fl == {}


def test_dev_offset():
    assert dev_offset("/dev/uio4") == 0
    assert dev_offset("/dev/mem") == 0x8000_0000


def test_apply_drop_filter_writes_cfg_word():
    io = make_io()
    apply_drop_filter(io, [0x07])
    assert io.rd(FILTER_CFG) == 0x107            # bit8=drop | code


def test_wr_split_and_utc():
    from readout_common import wr_split, wr_utc
    ts = (1_751_800_000 << 32) | 123_456_789
    assert wr_split(ts) == (1_751_800_000, 123_456_789)
    assert wr_utc(0) == "UNSYNC"                    # strict-zero timestamp
    s = wr_utc((0 << 32) | 123)                     # epoch second 0
    assert s == "1970-01-01T00:00:00.000000123Z"
    s = wr_utc(ts)
    assert s.endswith(".123456789Z") and s.startswith("20")


def test_stream_events_wr_formats_sec_ns_and_unsync():
    import readout_common as rc
    from readout_common import STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP

    # Drive stream_events(wr=True) with a scripted fake io: STATUS is non-empty for
    # each event, then raises KeyboardInterrupt to end the loop; read_event reads
    # EVENT/DATA/TS then POP advances. Verifies (a) an UNSYNC (ts==0) event and the
    # event right after it print the "--" dt placeholder, (b) a real WR pair prints
    # dt = ns_delta / 1000 us.
    class FakeIO:
        def __init__(self, events):
            self.events = events   # list of (event, flags, data, ts)
            self.i = 0
            self.regs = {}

        def rd(self, o):
            if o == STATUS:
                if self.i >= len(self.events):
                    raise KeyboardInterrupt
                ev, fl, data, ts = self.events[self.i]
                self.regs = {
                    EVENT: (fl << 16) | (ev & 0xFFFF),
                    DATA_HI: (data >> 32) & 0xFFFFFFFF, DATA_LO: data & 0xFFFFFFFF,
                    TS_HI: (ts >> 32) & 0xFFFFFFFF, TS_LO: ts & 0xFFFFFFFF,
                }
                return 0   # empty bit clear
            return self.regs.get(o, 0)

        def wr(self, o, v=0):
            if o == POP:
                self.i += 1

    SEC = 1_751_800_000
    events = [
        (0x02, 0x02, 0, 0),                                   # UNSYNC
        (0x07, 0x02, 0, (SEC << 32) | 1500),                  # +1500 ns after a 0-ns base
        (0x09, 0x02, 0, (SEC << 32) | 3500),                  # +2000 ns later
    ]
    io = FakeIO(events)
    lines = []
    orig_say = rc.say
    rc.say = lambda m: lines.append(m)
    try:
        rc.stream_events(io, tick_ns=0.0,
                         stats_line=lambda: "[stats]",
                         format_event=lambda ts, dt, e, d, t, h: "TS=%d dt=%s ev=0x%02X" % (ts, dt.strip(), e & 0xFF),
                         header="#hdr", wr=True)
    finally:
        rc.say = orig_say
    body = [l for l in lines if l.startswith("TS=")]
    assert body[0].endswith("ev=0x02") and "TS=0 " in body[0], body[0]      # UNSYNC event
    assert "dt=--" in body[0]                                                # no dt from/at UNSYNC
    assert "dt=--" in body[1]                                                # event after UNSYNC also suppressed
    assert "dt=2.0" in body[2], body[2]                                      # 2000 ns -> 2.0 us


def test_drain_events_decodes_and_pops():
    import readout_common as rc
    from readout_common import STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP

    class FakeIO:
        def __init__(self, events):
            self.events = events   # list of (event, flags, data, ts)
            self.i = 0
            self.regs = {}
        def rd(self, o):
            if o == STATUS:
                if self.i >= len(self.events):
                    raise KeyboardInterrupt   # ends drain_events cleanly
                ev, fl, data, ts = self.events[self.i]
                self.regs = {
                    EVENT: (fl << 16) | (ev & 0xFFFF),
                    DATA_HI: (data >> 32) & 0xFFFFFFFF, DATA_LO: data & 0xFFFFFFFF,
                    TS_HI: (ts >> 32) & 0xFFFFFFFF, TS_LO: ts & 0xFFFFFFFF,
                }
                return 0   # not empty
            return self.regs.get(o, 0)
        def wr(self, o, v=0):
            if o == POP:
                self.i += 1

    SEC = 1_751_800_000
    events = [
        (0x07, 0x03, 0xABCD, (SEC << 32) | 1500),   # is_tclk=1, has_data=1
        (0x18, 0x02, 0,      0),                      # is_tclk=1, has_data=0, UNSYNC
    ]
    got = []
    io = FakeIO(events)
    rc.drain_events(io, lambda e: got.append(e), idle_cb=None, poll_s=0)
    assert len(got) == 2, got
    assert got[0] == {"event": 0x07, "flags": 0x03, "data": 0xABCD,
                      "ts": (SEC << 32) | 1500, "is_tclk": 1, "has_data": 1}, got[0]
    assert got[1]["event"] == 0x18 and got[1]["ts"] == 0
    assert got[1]["is_tclk"] == 1 and got[1]["has_data"] == 0


def test_read_hw_counters_decodes_all_regs():
    io = make_io()
    io.wr(EVENT_COUNT, 1000)
    io.wr(NULL_COUNT, 5)
    io.wr(ERROR_COUNT, 7)
    io.wr(FILTERED_COUNT, 3)
    io.wr(LOCK, 1)
    io.wr(HEARTBEAT, 0x00ABCDEF)
    io.wr(STATUS, 0b10)                 # overflow bit set, empty bit clear
    hw = read_hw_counters(io)
    assert hw == {"event_count": 1000, "null_count": 5, "error_count": 7,
                  "filtered_count": 3, "overflow": 1, "lock": 1,
                  "heartbeat": 0x00ABCDEF}
    io.wr(STATUS, 0b01)                 # empty set, overflow clear
    io.wr(LOCK, 0)
    hw = read_hw_counters(io)
    assert hw["overflow"] == 0 and hw["lock"] == 0


def test_drain_events_tick_fires_when_fifo_busy():
    import readout_common as rc
    from readout_common import STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP

    class BusyIO:
        # STATUS always reads not-empty (bit0=0) until the events run out, then raises
        # KeyboardInterrupt. So idle_cb (empty-only) must NEVER fire, but tick_cb must.
        def __init__(self, n):
            self.n = n
            self.i = 0
        def rd(self, o):
            if o == STATUS:
                if self.i >= self.n:
                    raise KeyboardInterrupt
                return 0                # not empty
            return 0
        def wr(self, o, v=0):
            if o == POP:
                self.i += 1

    ticks = [0]
    idles = [0]
    io = BusyIO(3)
    rc.drain_events(io, on_event=lambda e: None,
                    idle_cb=lambda: idles.__setitem__(0, idles[0] + 1),
                    poll_s=0,
                    tick_cb=lambda: ticks.__setitem__(0, ticks[0] + 1),
                    tick_s=0.0)          # tick_s=0 -> fires every iteration
    assert idles[0] == 0, "idle_cb must not fire on a never-empty FIFO"
    assert ticks[0] >= 3, ticks[0]       # fired on each busy iteration


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all readout_common tests passed")
