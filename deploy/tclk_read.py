#!/usr/bin/env python3
"""Stream decoded TCLK events from the PL readout over UIO.

Drains the AXI-Lite readout at 0x8000_0000: polls STATUS, reads each buffered event
(code + flags + 64-bit hardware timestamp), pops it, prints a line. Every ~1 s prints
a stats line: EVENT/NULL/ERROR counts + the DEBUG activity register (raw TCLK
transitions, which climb even if the decoder never locks).

    sudo python3 tclk_read.py /dev/uio4

Ctrl-C to stop. Diagnostic reading: tclk_edges climbing + EVT flat => signal present
but not decoding; tclk_edges flat => no signal / pin / front-end.

Output is LINE-BUFFERED on purpose. A streaming diagnostic that block-buffers (which
Python does whenever stdout is a pipe/file instead of a terminal) swallows everything
printed so far the instant it blocks -- including the header -- so the tool looks dead
before it ever started. Every line here flushes immediately, and a one-time startup
probe + a watchdog name the exact register if an AXI read wedges the bus. If a read
ever hangs uninterruptibly, the LAST line on screen tells you which offset did it.
"""
import mmap, os, struct, sys, threading, time
from tclk_filter import parse_drop_codes, filter_cfg_word

# Force line buffering so a freeze can never hide already-printed output (the #1 reason
# this looked like "the header never even ran"). Guarded: not every stdout supports it.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

def say(msg):
    print(msg, flush=True)

_args = sys.argv[1:]
_drop_spec = ""
_tick_ns = None                       # --tick-ns override (default: this reader's local tick)
_pos = []
_i = 0
while _i < len(_args):
    if _args[_i] == "--drop" and _i + 1 < len(_args):
        _drop_spec = _args[_i + 1]; _i += 2
    elif _args[_i] == "--tick-ns" and _i + 1 < len(_args):
        _tick_ns = float(_args[_i + 1]); _i += 2   # e.g. 10 for the pl_clk0 shared timebase (USE_EXT_TS)
    else:
        _pos.append(_args[_i]); _i += 1
DEV = _pos[0] if _pos else "/dev/uio4"
DROP_CODES = parse_drop_codes(_drop_spec)
OFF = 0 if "uio" in DEV else 0x8000_0000

# Registers are spaced 16 BYTES apart: the hand-written AXI4-Lite slave only returns
# correct data at 16-byte-aligned offsets on this board (non-aligned reads read 0).
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xA0
)
HEARTBEAT, LOCK = 0xB0, 0xC0   # free-running clk_40m counter (trust check); MMCM-locked bit
FILTER_CFG, FILTERED_COUNT = 0xD0, 0xE0   # drop-mask config (write); dropped-event count (read)
TICK_NS = _tick_ns if _tick_ns is not None else 25.0  # standalone: clk_40m 40 MHz = 25 ns.
# For the integrated pipeline (USE_EXT_TS=1) the timestamp is the shared pl_clk0 timebase,
# so pass --tick-ns 10 to make dt_us correct.

NAME = {STATUS: "STATUS", EVENT: "EVENT", DATA_HI: "DATA_HI", DATA_LO: "DATA_LO",
        TS_HI: "TS_HI", TS_LO: "TS_LO", POP: "POP", EVENT_COUNT: "EVENT_COUNT",
        NULL_COUNT: "NULL_COUNT", ERROR_COUNT: "ERROR_COUNT", DEBUG: "DEBUG",
        HEARTBEAT: "HEARTBEAT", LOCK: "LOCK",
        FILTER_CFG: "FILTER_CFG", FILTERED_COUNT: "FILTERED_COUNT"}

# --- Watchdog -------------------------------------------------------------------
# An AXI read that never returns RVALID wedges the CPU's load instruction
# uninterruptibly: Ctrl-C will NOT break it. A daemon thread watches every register
# access and, if one stalls, prints which offset is stuck -- pinning the freeze to an
# exact register = a PL/BD fault (reset held, overlay/bitstream not loaded, dead bus),
# NOT a Python bug. It cannot unstick the access (nothing can, short of a reload), but
# it turns a silent hang into a labeled verdict.
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

# --- Register access (every access is watched + labeled) ------------------------
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
    dbg = rd(DEBUG)
    return "[stats] EVT=%d NULL=%d ERR=%d FILT=%d | tclk_edges=%d level=%d sig_err=%d | hb=%d lock=%d" % (
        rd(EVENT_COUNT), rd(NULL_COUNT), rd(ERROR_COUNT), rd(FILTERED_COUNT),
        dbg & 0x3FFFFFFF, (dbg >> 30) & 1, (dbg >> 31) & 1,
        rd(HEARTBEAT), rd(LOCK) & 1)

def probe():
    """One-time startup read of each register, announced BEFORE each access. If the
    bus wedges, the last 'reading ...' line names the exact offset that hung. Then a
    TRUST CHECK: EVENT_COUNT and tclk_edges cross clock domains through cdc_gray_count,
    which has a documented AXI-readback bug (reads 0 even when the count is alive). The
    free-running heartbeat (0xB0) takes the SAME path, so if it MOVES the readback works
    and those diagnostics can be trusted; if it is stuck the readback is broken and
    'EVENT_COUNT=0 / tclk_edges=0' tells us nothing."""
    say("# --- startup probe (a freeze here names the wedged offset) ---")
    for o in (STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG):
        say("#   reading %-12s 0x%02X ..." % (NAME[o], o))
        v = rd(o)
        say("#     %-12s = 0x%08X" % (NAME[o], v))
    lock = rd(LOCK) & 1
    hb1 = rd(HEARTBEAT)
    time.sleep(0.05)
    hb2 = rd(HEARTBEAT)
    say("#   MMCM lock (0xC0) = %d   heartbeat (0xB0): %d -> %d (+%d)" % (lock, hb1, hb2, hb2 - hb1))
    if lock != 1:
        say("# --- RED FLAG: MMCM not locked => clk_40m/clk_80m are dead; the TCLK "
            "receiver has no clock. Fix clocking before anything else. ---")
    elif hb2 != hb1 and hb1 != 0:
        say("# --- TRUST OK: heartbeat moving => AXI counter readback works, so "
            "EVENT_COUNT / tclk_edges are trustworthy. Path is healthy; tclk_edges=0 "
            "just means no signal at the pin yet -> safe to wire up real TCLK. ---")
    else:
        say("# --- WARNING: MMCM locked but heartbeat STUCK => the cdc_gray_count AXI "
            "readback is still broken. EVENT_COUNT / tclk_edges read 0 even when alive, "
            "so fix the readback BEFORE wiring TCLK (else bring-up is uninterpretable). ---")
    say("# --- probe complete: AXI reads return, the bus is alive. ---")

# --- Open + map -----------------------------------------------------------------
say("# opening %s (offset 0x%x) ..." % (DEV, OFF))
fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OFF)
say("# mmap ok (0x1000 bytes). starting watchdog ...")
threading.Thread(target=_watchdog, daemon=True).start()
for _c in DROP_CODES:
    wr(FILTER_CFG, filter_cfg_word(_c))
if DROP_CODES:
    say("# drop-mask: suppressing " + ", ".join("0x%02X" % c for c in DROP_CODES))

say("# streaming TCLK events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, OFF))
probe()
say(stats_line())
say("#        ts_ticks    dt_us   event  tclk  has_data")

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
        say("  %16d %s   0x%02X    %d      %d" % (ts, dt, event & 0xFF, is_tclk, has_data))
except KeyboardInterrupt:
    say("\n# stopped.")
    say(stats_line())
