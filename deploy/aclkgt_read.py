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

Output is LINE-BUFFERED on purpose; a startup probe + watchdog name the exact register
if an AXI read wedges the bus. Shared plumbing lives in readout_common.py.
"""
import sys
import time

import readout_common as rc
from readout_common import (
    STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG, LOCK,
    FILTERED_COUNT, GT_CTRL, parse_drop_codes, say,
)

rc.line_buffer_stdout()

_pos, _flags = rc.parse_args(
    sys.argv[1:],
    value_flags=("--drop", "--gtctrl", "--txdiff", "--txpost", "--txpre", "--tick-ns"),
    bool_flags=("--gtreset",))
DEV = _pos[0] if _pos else "/dev/uio4"
DROP_CODES = parse_drop_codes(_flags.get("--drop", ""))
_gtreset = bool(_flags.get("--gtreset"))   # pulse GT_CTRL[24]: full RX PLL+CDR relock (use after a
                                           # runtime loopback/source switch; [8] does NOT relock the CDR)
GTCTRL = int(_flags["--gtctrl"], 0) if "--gtctrl" in _flags else 0x00  # default: known normal state
# TX driver sweep fields live in GT_CTRL[23:9]: [13:9]=txdiffctrl [18:14]=txpostcursor
# [23:19]=txprecursor. The PL uses 0x18 when the txdiffctrl field is 0, so leaving --txdiff
# unset keeps the proven default swing. Sweep these live to find a TX eye the SFP link locks on.
GTCTRL |= (int(_flags.get("--txdiff", "0"), 0) & 0x1F) << 9
GTCTRL |= (int(_flags.get("--txpost", "0"), 0) & 0x1F) << 14
GTCTRL |= (int(_flags.get("--txpre", "0"), 0) & 0x1F) << 19
# Standalone: GT RX usrclk2 62.5 MHz = 16 ns. For the integrated pipeline (USE_EXT_TS=1)
# the timestamp is the shared pl_clk0 timebase, so pass --tick-ns 10.
TICK_NS = float(_flags["--tick-ns"]) if "--tick-ns" in _flags else 1000.0 / 62.5

io = rc.open_dev(DEV)
rc.apply_drop_filter(io, DROP_CODES)


def set_gt_ctrl(val, full=False):
    """Write GT_CTRL with an RX re-init pulse so a new rxpolarity/loopback actually takes
    effect: assert bit8 (gtwiz_reset_rx_datapath) WITH the config bits, then release. With
    full=True also pulse bit24 (gtwiz_reset_rx_pll_and_datapath) for a TRUE CDR/PLL relock --
    needed when SWITCHING loopback/source at runtime (datapath-only does NOT relock the CDR to
    a new source). On the shared-QPLL self-test a full reset also blips TX briefly, then recovers.
    val: [0]=rxpol [1]=txpol [4:2]=loopback [13:9]=txdiff [18:14]=txpost [23:19]=txpre."""
    # Assert exactly ONE reset input: asserting rx_datapath ([8]) and rx_pll_and_datapath ([24])
    # together can wedge the gtwizard reset FSM (rx_done never re-asserts -> lock stays 0).
    # full => pll+datapath ([24], a superset that also resets the datapath); else datapath ([8]).
    reinit = 0x1000000 if full else 0x100
    io.wr(GT_CTRL, (val | reinit) & 0xFFFFFFFF)   # config + RX re-init asserted
    time.sleep(0.05 if full else 0.02)
    io.wr(GT_CTRL, val & ~reinit & 0xFFFFFFFF)     # release: RX re-inits & re-aligns under new config
    time.sleep(0.20 if full else 0.10)
    rb = io.rd(GT_CTRL)
    _td = (val >> 9) & 0x1F
    want = val & ~reinit & 0xFFFFFF             # config bits only (the re-init pulses self-release to 0)
    if (rb & 0xFFFFFF) != want:
        say("# !! WARNING: GT_CTRL readback 0x%08X != written 0x%06X (masked) -- AXI write may "
            "have failed (wrong stride / wedged write channel); the GT config did NOT change." % (rb, want))
    say("# GT_CTRL <- 0x%06X (rxpol=%d txpol=%d loopback=%d | txdiff=%s txpost=%d txpre=%d | full_reset=%d), readback=0x%08X" % (
        val, val & 1, (val >> 1) & 1, (val >> 2) & 7,
        ("0x18(dflt)" if _td == 0 else "0x%02X" % _td), (val >> 14) & 0x1F, (val >> 19) & 0x1F, int(full), rb))


def stats_line():
    # GT-link health DEBUG word (0xA0):
    #   [7:0]   commadet  = GT RX comma-detect count (8b, wraps)
    #   [11:8]  recover   = RX link-recovery FSM firings (4b, wraps); 0/low = link holding lock
    #   [12]    mod_abs   = SFP module absent (1 = no module present)
    #   [13]    tx_fault  = SFP TX fault (1 = fault asserted)
    #   [27:14] disperr   = GT 8b10b disparity-error count (14b, wraps)
    #   [28]    notintbl  = an 8b10b not-in-table (invalid-code) symbol seen this lock session
    #   [29]    rx_los    = SFP RX loss-of-signal (1 = NO optical input reaching the receiver)
    #   [30]    byteali   = GT RX byte-aligned
    #   [31]    rcv_algn  = ACLK_RCV decoder locked
    # Healthy link: rx_los=0, byteali=1 and rcv_aligned=1 holding solid, EVT climbing every
    # second, recover NOT climbing. recover climbing => the link keeps losing lock and the FSM is
    # self-healing it (eye still marginal). Read counters by "climbing" vs "frozen" (they wrap).
    dbg = io.rd(DEBUG)
    commadet = dbg & 0xFF
    recover  = (dbg >> 8) & 0xF
    mod_abs  = (dbg >> 12) & 1
    tx_fault = (dbg >> 13) & 1
    disperr  = (dbg >> 14) & 0x3FFF
    notintbl = (dbg >> 28) & 1
    rx_los   = (dbg >> 29) & 1
    byteali  = (dbg >> 30) & 1
    rcv_algn = (dbg >> 31) & 1
    return ("[stats] EVT=%d NULL=%d ERR=%d FILT=%d | commadet=%d disperr=%d recover=%d rx_los=%d "
            "tx_fault=%d mod_abs=%d notintbl=%d byteali=%d rcv_aligned=%d | dbg=0x%08X lock=%d") % (
        io.rd(EVENT_COUNT), io.rd(NULL_COUNT), io.rd(ERROR_COUNT), io.rd(FILTERED_COUNT),
        commadet, disperr, recover, rx_los, tx_fault, mod_abs, notintbl, byteali, rcv_algn,
        dbg, io.rd(LOCK) & 1)


def format_event(ts, dt, event, data, is_tclk, has_data):
    data_str = "0x%016X" % data if has_data else "       --         "
    return "  %16d %s   0x%04X  %s    %d      %d" % (ts, dt, event, data_str, is_tclk, has_data)


set_gt_ctrl(GTCTRL, full=_gtreset)   # apply a known config (default 0x00 = normal) so a prior run can't bleed in

say("# streaming gigabit ACLK / GT events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, rc.dev_offset(DEV)))
rc.probe(
    io, (STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG),
    lock_desc="GT RX lock",
    red_flag=("GT RX not locked => usrclk2 is dead; the readout has no clock. "
              "Fix GTH clocking / SFP before anything else."),
    trust_ok=("heartbeat moving => AXI counter readback works, so "
              "EVENT_COUNT / line_edges are trustworthy. line_edges=0 just means no signal "
              "at the SFP yet -> safe to wire up a real gigabit ACLK source."),
    stuck_warn="GT RX locked but heartbeat STUCK => counter readback broken.",
)
say(stats_line())
rc.stream_events(io, TICK_NS, stats_line, format_event,
                 header="#        ts_ticks    dt_us   event     data               tclk  has_data")
