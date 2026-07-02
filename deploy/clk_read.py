#!/usr/bin/env python3
"""Stream decoded events from the unified ACLK/TCLK readout over UIO.

Drains the AXI-Lite readout at 0x8000_0000: polls STATUS, reads each buffered event
(16-bit id + flags + 64-bit data + 64-bit hardware timestamp), pops it, prints a line.
is_tclk=1 marks a legacy 8-bit TCLK event; has_data=1 marks a full ACLK packet with a
64-bit payload. Every ~1 s prints a stats line incl. the DEBUG activity register (raw
serial-line transitions + level + serdec sig_err).

    sudo python3 clk_read.py /dev/uio4

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

_pos, _flags = rc.parse_args(sys.argv[1:], value_flags=("--drop",))
DEV = _pos[0] if _pos else "/dev/uio4"
DROP_CODES = parse_drop_codes(_flags.get("--drop", ""))
TICK_NS = 25.0  # clk_40m = 40 MHz timestamp tick

io = rc.open_dev(DEV)
rc.apply_drop_filter(io, DROP_CODES)


def stats_line():
    dbg = io.rd(DEBUG)
    return "[stats] EVT=%d NULL=%d ERR=%d FILT=%d | line_edges=%d level=%d sig_err=%d | hb=%d lock=%d" % (
        io.rd(EVENT_COUNT), io.rd(NULL_COUNT), io.rd(ERROR_COUNT), io.rd(FILTERED_COUNT),
        dbg & 0x3FFFFFFF, (dbg >> 30) & 1, (dbg >> 31) & 1,
        io.rd(HEARTBEAT), io.rd(LOCK) & 1)


def format_event(ts, dt, event, data, is_tclk, has_data):
    data_str = "0x%016X" % data if has_data else "       --         "
    return "  %16d %s   0x%04X  %s    %d      %d" % (ts, dt, event, data_str, is_tclk, has_data)


say("# streaming ACLK/TCLK events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, rc.dev_offset(DEV)))
rc.probe(
    io, (STATUS, EVENT_COUNT, ERROR_COUNT, DEBUG),
    lock_desc="MMCM lock",
    red_flag="MMCM not locked => clk_40m/clk_80m dead; the decoder has no clock.",
    trust_ok=("heartbeat moving => AXI counter readback works. line_edges=0 just "
              "means no signal at the pin yet."),
    stuck_warn="MMCM locked but heartbeat STUCK => counter readback broken.",
)
say(stats_line())
rc.stream_events(io, TICK_NS, stats_line, format_event,
                 header="#        ts_ticks    dt_us   event     data               tclk  has_data")
