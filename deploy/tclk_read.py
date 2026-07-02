#!/usr/bin/env python3
"""Stream decoded TCLK events from the PL readout over UIO.

Drains the AXI-Lite readout at 0x8000_0000: polls STATUS, reads each buffered event
(code + flags + 64-bit hardware timestamp), pops it, prints a line. Every ~1 s prints
a stats line: EVENT/NULL/ERROR counts + the DEBUG activity register (raw TCLK
transitions, which climb even if the decoder never locks).

    sudo python3 tclk_read.py /dev/uio4

Ctrl-C to stop. Diagnostic reading: tclk_edges climbing + EVT flat => signal present
but not decoding; tclk_edges flat => no signal / pin / front-end.

Output is LINE-BUFFERED on purpose; a startup probe + watchdog name the exact register
if an AXI read wedges the bus. Shared plumbing lives in readout_common.py.
"""
import sys

import readout_common as rc
from readout_common import (
    STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG, HEARTBEAT, LOCK,
    FILTERED_COUNT, parse_drop_codes, say,
)

rc.line_buffer_stdout()

_pos, _flags = rc.parse_args(sys.argv[1:], value_flags=("--drop", "--tick-ns"))
DEV = _pos[0] if _pos else "/dev/uio4"
DROP_CODES = parse_drop_codes(_flags.get("--drop", ""))
# Standalone: clk_40m 40 MHz = 25 ns. For the integrated pipeline (USE_EXT_TS=1) the
# timestamp is the shared pl_clk0 timebase, so pass --tick-ns 10 to make dt_us correct.
TICK_NS = float(_flags["--tick-ns"]) if "--tick-ns" in _flags else 25.0

io = rc.open_dev(DEV)
rc.apply_drop_filter(io, DROP_CODES)


def stats_line():
    dbg = io.rd(DEBUG)
    return "[stats] EVT=%d NULL=%d ERR=%d FILT=%d | tclk_edges=%d level=%d sig_err=%d | hb=%d lock=%d" % (
        io.rd(EVENT_COUNT), io.rd(NULL_COUNT), io.rd(ERROR_COUNT), io.rd(FILTERED_COUNT),
        dbg & 0x3FFFFFFF, (dbg >> 30) & 1, (dbg >> 31) & 1,
        io.rd(HEARTBEAT), io.rd(LOCK) & 1)


def format_event(ts, dt, event, data, is_tclk, has_data):
    return "  %16d %s   0x%02X    %d      %d" % (ts, dt, event & 0xFF, is_tclk, has_data)


say("# streaming TCLK events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, rc.dev_offset(DEV)))
rc.probe(
    io, (STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG),
    lock_desc="MMCM lock",
    red_flag=("MMCM not locked => clk_40m/clk_80m are dead; the TCLK "
              "receiver has no clock. Fix clocking before anything else."),
    trust_ok=("heartbeat moving => AXI counter readback works, so "
              "EVENT_COUNT / tclk_edges are trustworthy. Path is healthy; tclk_edges=0 "
              "just means no signal at the pin yet -> safe to wire up real TCLK."),
    stuck_warn=("MMCM locked but heartbeat STUCK => the cdc_gray_count AXI "
                "readback is still broken. EVENT_COUNT / tclk_edges read 0 even when alive, "
                "so fix the readback BEFORE wiring TCLK (else bring-up is uninterpretable)."),
)
say(stats_line())
rc.stream_events(io, TICK_NS, stats_line, format_event,
                 header="#        ts_ticks    dt_us   event  tclk  has_data")
