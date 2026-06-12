#!/usr/bin/env python3
"""Stream decoded TCLK events from the PL readout over UIO.

Drains the AXI-Lite readout at 0x8000_0000: polls STATUS, reads each buffered event
(code + flags + 64-bit hardware timestamp), pops it, prints a line. Every ~1 s prints
a stats line: EVENT/NULL/ERROR counts + the DEBUG activity register (raw TCLK
transitions, which climb even if the decoder never locks).

    sudo python3 tclk_read.py /dev/uio4

Ctrl-C to stop. Diagnostic reading: tclk_edges climbing + EVT flat => signal present
but not decoding; tclk_edges flat => no signal / pin / front-end.
"""
import mmap, os, struct, sys, time

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/uio4"
OFF = 0 if "uio" in DEV else 0x8000_0000

STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = (
    0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28
)
TICK_NS = 25.0  # clk_40m = 40 MHz timestamp tick

fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OFF)

def rd(o):
    return struct.unpack("<I", m[o:o + 4])[0]

def wr(o, v=0):
    m[o:o + 4] = struct.pack("<I", v & 0xFFFFFFFF)

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
    return "[stats] EVT=%d NULL=%d ERR=%d | tclk_edges=%d level=%d sig_err=%d" % (
        rd(EVENT_COUNT), rd(NULL_COUNT), rd(ERROR_COUNT),
        dbg & 0x3FFFFFFF, (dbg >> 30) & 1, (dbg >> 31) & 1)

print("# streaming TCLK events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, OFF))
print(stats_line())
print("#        ts_ticks    dt_us   event  tclk  has_data")

last_ts = None
last_stats = time.monotonic()
try:
    while True:
        if rd(STATUS) & 0x1:                       # empty
            now = time.monotonic()
            if now - last_stats >= 1.0:
                print(stats_line())
                last_stats = now
            time.sleep(0.001)
            continue
        event, flags, data, ts = read_event()
        is_tclk = (flags >> 1) & 1
        has_data = flags & 1
        dt = "   --  " if last_ts is None else "%7.1f" % ((ts - last_ts) * TICK_NS / 1000.0)
        last_ts = ts
        print("  %16d %s   0x%02X    %d      %d" % (ts, dt, event & 0xFF, is_tclk, has_data))
except KeyboardInterrupt:
    print("\n# stopped.")
    print(stats_line())
