"""Unit tests for the pure event-filter helpers (no hardware needed).
Run: python deploy/test_tclk_filter.py"""
from tclk_filter import parse_drop_codes, filter_cfg_word


def test_parse():
    assert parse_drop_codes("") == []
    assert parse_drop_codes(None) == []
    assert parse_drop_codes("07") == [0x07]
    assert parse_drop_codes("07,0F,BA,8F") == [0x07, 0x0F, 0xBA, 0x8F]
    assert parse_drop_codes(" 07 , 0f ") == [0x07, 0x0F]   # whitespace + lowercase


def test_cfg_word():
    assert filter_cfg_word(0x07) == 0x107
    assert filter_cfg_word(0x07, drop=True) == 0x107
    assert filter_cfg_word(0x07, drop=False) == 0x007
    assert filter_cfg_word(0xBA) == 0x1BA


if __name__ == "__main__":
    test_parse()
    test_cfg_word()
    print("OK")
