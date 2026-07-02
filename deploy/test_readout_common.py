"""Unit tests for readout_common (no hardware: RegIO runs on a bytearray).
Run: python test_readout_common.py   or   pytest deploy -q"""
from readout_common import (
    RegIO, read_event, parse_args, dev_offset, apply_drop_filter,
    STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, FILTER_CFG, NAME, GT_CTRL,
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all readout_common tests passed")
