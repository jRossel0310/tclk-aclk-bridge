#!/usr/bin/env python3
"""Stream decoded gigabit ACLK / GT events from the PL readout over UIO.

Drains the AXI-Lite readout at 0x8000_0000: polls STATUS, reads each buffered event
(16-bit event id + flags + 64-bit data + 64-bit hardware timestamp), pops it, prints a
line. Every ~1 s prints a stats line: EVENT/NULL/ERROR/FILTERED counts + the DEBUG
activity register (raw activity transitions, which climb even if the decoder
never frames).

    sudo python3 aclkgt_read.py /dev/uio4

Ctrl-C to stop. Diagnostic reading: line_edges climbing + EVT flat => signal present but
not decoding (check GTH alignment / line bit rate); line_edges flat => no signal / SFP.

Output is LINE-BUFFERED on purpose so a freeze can never hide already-printed output; a
startup probe + watchdog name the exact register if an AXI read wedges the bus.
"""
import mmap, os, struct, sys, threading, time
from tclk_filter import parse_drop_codes, filter_cfg_word

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

def say(msg):
    print(msg, flush=True)

_args = sys.argv[1:]
_drop_spec = ""
_pos = []
_i = 0
while _i < len(_args):
    if _args[_i] == "--drop" and _i + 1 < len(_args):
        _drop_spec = _args[_i + 1]; _i += 2
    else:
        _pos.append(_args[_i]); _i += 1
DEV = _pos[0] if _pos else "/dev/uio4"
DROP_CODES = parse_drop_codes(_drop_spec)
OFF = 0 if "uio" in DEV else 0x8000_0000

# Registers spaced 16 BYTES apart (the hand-written AXI4-Lite slave only returns correct
# data at 16-byte-aligned offsets on this board).
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xA0
)
HEARTBEAT, LOCK = 0xB0, 0xC0
FILTER_CFG, FILTERED_COUNT = 0xD0, 0xE0
TICK_NS = 1000.0 / 62.5  # GT RX usrclk2 = 62.5 MHz (1.25 Gbps / 20 for 16-bit 8b10b) => 16.0 ns/tick

NAME = {STATUS: "STATUS", EVENT: "EVENT", DATA_HI: "DATA_HI", DATA_LO: "DATA_LO",
        TS_HI: "TS_HI", TS_LO: "TS_LO", POP: "POP", EVENT_COUNT: "EVENT_COUNT",
        NULL_COUNT: "NULL_COUNT", ERROR_COUNT: "ERROR_COUNT", DEBUG: "DEBUG",
        HEARTBEAT: "HEARTBEAT", LOCK: "LOCK",
        FILTER_CFG: "FILTER_CFG", FILTERED_COUNT: "FILTERED_COUNT"}

_watch = {"label": None, "t": 0.0}

def _watchdog():
    warned = False
    while True:
        time.sleep(0.5)
        lbl = _watch["label"]
        if lbl is None:
            warned = False
            continue
        if (time.monotonic() - _watch["t"]) > 2.0 and not warned:
            say("# !! WATCHDOG: blocked >2s in %s" % lbl)
            say("# !! An AXI read is wedging the bus (reset held / overlay-bitstream not "
                "loaded / wrong address). This is the PL/BD side, NOT this Python script.")
            say("# !! Ctrl-C cannot break a wedged AXI load; reload the bitstream+overlay "
                "and re-check s_axi_aresetn / the dcm_locked tie-off.")
            warned = True

def _enter(label):
    _watch["t"] = time.monotonic()
    _watch["label"] = label

def _leave():
    _watch["label"] = None

def rd(o):
    _enter("read %s (0x%02X)" % (NAME.get(o, "?"), o))
    v = struct.unpack("<I", m[o:o + 4])[0]
    _leave()
    return v

def wr(o, v=0):
    _enter("write %s (0x%02X)" % (NAME.get(o, "?"), o))
    m[o:o + 4] = struct.pack("<I", v & 0xFFFFFFFF)
    _leave()

def read_event():
    ev = rd(EVENT)
    event = ev & 0xFFFF
    flags = (ev >> 16) & 0xFFFF
    data = (rd(DATA_HI) << 32) | rd(DATA_LO)
    ts = (rd(TS_HI) << 32) | rd(TS_LO)
    wr(POP)
    return event, flags, data, ts

def stats_line():
    # M0 bring-up DEBUG word (0xA0), round 3 - SNAPSHOT the decoded K-char byte:
    #   [7:0]   kbyte    = value of the first K char the GT RX decoded (0xBC == K28.5)
    #   [15:8]  kchar    = K-char decode count (8b, wraps; TX/decode alive?)
    #   [19]    klane    = byte lane of that K char (0=low, 1=high)
    #   [20]    kbvalid  = a K char was captured
    #   [21]    commaever= GT ever decoded a comma char (sticky)
    #   [22]    byteali  = GT RX byte-aligned
    #   [23]    rcv_algn = ACLK_RCV comma-aligned
    #   [31:24] gen      = generator frame count (8b, wraps; TX alive?)
    # kbyte==0xBC -> K28.5 on the wire -> comma-detect config; else wrong byte K-flagged.
    dbg = rd(DEBUG)
    kbyte = dbg & 0xFF
    kchar = (dbg >> 8) & 0xFF
    klane = (dbg >> 19) & 1
    kbvalid = (dbg >> 20) & 1
    commaever = (dbg >> 21) & 1
    byteali = (dbg >> 22) & 1
    rcv_algn = (dbg >> 23) & 1
    gen = (dbg >> 24) & 0xFF
    return ("[stats] EVT=%d ERR=%d | gen=%d kchar=%d kbyte=0x%02X klane=%d kbvalid=%d "
            "commaever=%d byteali=%d rcv_aligned=%d | dbg=0x%08X lock=%d") % (
        rd(EVENT_COUNT), rd(ERROR_COUNT),
        gen, kchar, kbyte, klane, kbvalid, commaever, byteali, rcv_algn,
        dbg, rd(LOCK) & 1)

def probe():
    """One-time startup read of each register, announced BEFORE each access, then a
    TRUST CHECK on the heartbeat (same CDC path as the counters): if it MOVES the
    readback works and EVENT_COUNT / line_edges can be trusted."""
    say("# --- startup probe (a freeze here names the wedged offset) ---")
    for o in (STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG):
        say("#   reading %-12s 0x%02X ..." % (NAME[o], o))
        v = rd(o)
        say("#     %-12s = 0x%08X" % (NAME[o], v))
    lock = rd(LOCK) & 1
    hb1 = rd(HEARTBEAT)
    time.sleep(0.05)
    hb2 = rd(HEARTBEAT)
    say("#   GT RX lock (0xC0) = %d   heartbeat (0xB0): %d -> %d (+%d)" % (lock, hb1, hb2, hb2 - hb1))
    if lock != 1:
        say("# --- RED FLAG: GT RX not locked => usrclk2 is dead; the readout has no clock. "
            "Fix GTH clocking / SFP before anything else. ---")
    elif hb2 != hb1 and hb1 != 0:
        say("# --- TRUST OK: heartbeat moving => AXI counter readback works, so "
            "EVENT_COUNT / line_edges are trustworthy. line_edges=0 just means no signal "
            "at the SFP yet -> safe to wire up a real gigabit ACLK source. ---")
    else:
        say("# --- WARNING: GT RX locked but heartbeat STUCK => counter readback broken. ---")
    say("# --- probe complete: AXI reads return, the bus is alive. ---")

say("# opening %s (offset 0x%x) ..." % (DEV, OFF))
fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OFF)
say("# mmap ok (0x1000 bytes). starting watchdog ...")
threading.Thread(target=_watchdog, daemon=True).start()
for _c in DROP_CODES:
    wr(FILTER_CFG, filter_cfg_word(_c))
if DROP_CODES:
    say("# drop-mask: suppressing " + ", ".join("0x%02X" % c for c in DROP_CODES))

say("# streaming gigabit ACLK / GT events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, OFF))
probe()
say(stats_line())
say("#        ts_ticks    dt_us   event     data               tclk  has_data")

last_ts = None
last_stats = time.monotonic()
try:
    while True:
        if rd(STATUS) & 0x1:                       # empty
            now = time.monotonic()
            if now - last_stats >= 1.0:
                say(stats_line())
                last_stats = now
            time.sleep(0.001)
            continue
        event, flags, data, ts = read_event()
        is_tclk = (flags >> 1) & 1
        has_data = flags & 1
        dt = "   --  " if last_ts is None else "%7.1f" % ((ts - last_ts) * TICK_NS / 1000.0)
        last_ts = ts
        data_str = "0x%016X" % data if has_data else "       --         "
        say("  %16d %s   0x%04X  %s    %d      %d" % (ts, dt, event, data_str, is_tclk, has_data))
except KeyboardInterrupt:
    say("\n# stopped.")
    say(stats_line())
