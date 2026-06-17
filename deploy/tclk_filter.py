"""Pure helpers for the TCLK readout event drop-filter (no hardware deps, so they
are unit-testable off the board). Used by tclk_read.py."""


def parse_drop_codes(spec):
    """Parse a comma-separated list of hex event codes into a list of ints.
    '' / None -> []. '07,0F,BA' -> [0x07, 0x0F, 0xBA]. Whitespace tolerant."""
    spec = (spec or "").strip()
    if not spec:
        return []
    return [int(tok, 16) & 0xFF for tok in spec.split(",") if tok.strip()]


def filter_cfg_word(code, drop=True):
    """FILTER_CFG write word: bit8 = drop?, bits[7:0] = event code."""
    return (0x100 if drop else 0x000) | (code & 0xFF)
