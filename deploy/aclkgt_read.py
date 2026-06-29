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
_gtctrl_spec = None
_txdiff = _txpost = _txpre = None     # TX driver sweep fields (None => use HW default)
_gtreset = False                      # --gtreset: also pulse GT_CTRL[24] (full RX PLL+CDR relock)
_pos = []
_i = 0
while _i < len(_args):
    if _args[_i] == "--drop" and _i + 1 < len(_args):
        _drop_spec = _args[_i + 1]; _i += 2
    elif _args[_i] == "--gtctrl" and _i + 1 < len(_args):
        _gtctrl_spec = _args[_i + 1]; _i += 2     # e.g. 0x01 (rxpol), 0x08 (loopback), 0x00 (normal)
    elif _args[_i] == "--txdiff" and _i + 1 < len(_args):
        _txdiff = int(_args[_i + 1], 0) & 0x1F; _i += 2   # TXDIFFCTRL 0..31 (0 => HW default 0x18)
    elif _args[_i] == "--txpost" and _i + 1 < len(_args):
        _txpost = int(_args[_i + 1], 0) & 0x1F; _i += 2   # TXPOSTCURSOR 0..31 (0 = no emphasis)
    elif _args[_i] == "--txpre" and _i + 1 < len(_args):
        _txpre = int(_args[_i + 1], 0) & 0x1F; _i += 2    # TXPRECURSOR 0..31 (0 = no emphasis)
    elif _args[_i] == "--gtreset":
        _gtreset = True; _i += 1   # pulse GT_CTRL[24]: full RX PLL+CDR relock (use after a runtime
                                   # loopback/source switch; datapath-only [8] does NOT relock the CDR)
    else:
        _pos.append(_args[_i]); _i += 1
DEV = _pos[0] if _pos else "/dev/uio4"
DROP_CODES = parse_drop_codes(_drop_spec)
GTCTRL = int(_gtctrl_spec, 0) if _gtctrl_spec is not None else 0x00  # default: known normal state
# TX driver sweep fields live in GT_CTRL[23:9]: [13:9]=txdiffctrl [18:14]=txpostcursor
# [23:19]=txprecursor. The PL uses 0x18 when the txdiffctrl field is 0, so leaving --txdiff
# unset keeps the proven default swing. Sweep these live to find a TX eye the SFP link locks on.
GTCTRL |= ((_txdiff or 0) & 0x1F) << 9
GTCTRL |= ((_txpost or 0) & 0x1F) << 14
GTCTRL |= ((_txpre  or 0) & 0x1F) << 19
OFF = 0 if "uio" in DEV else 0x8000_0000

# Registers spaced 16 BYTES apart (the hand-written AXI4-Lite slave only returns correct
# data at 16-byte-aligned offsets on this board).
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xA0
)
HEARTBEAT, LOCK = 0xB0, 0xC0
FILTER_CFG, FILTERED_COUNT = 0xD0, 0xE0
GT_CTRL = 0xF0   # RW: [0]=rxpolarity [1]=txpolarity [4:2]=loopback_in [8]=RX datapath re-init pulse
                 #     [13:9]=TXDIFFCTRL (0=>PL default 0x18) [18:14]=TXPOSTCURSOR [23:19]=TXPRECURSOR
                 #     [24]=full RX PLL+datapath reset pulse (true CDR relock; --gtreset)
TICK_NS = 1000.0 / 62.5  # GT RX usrclk2 = 62.5 MHz (1.25 Gbps / 20 for 16-bit 8b10b) => 16.0 ns/tick

NAME = {STATUS: "STATUS", EVENT: "EVENT", DATA_HI: "DATA_HI", DATA_LO: "DATA_LO",
        TS_HI: "TS_HI", TS_LO: "TS_LO", POP: "POP", EVENT_COUNT: "EVENT_COUNT",
        NULL_COUNT: "NULL_COUNT", ERROR_COUNT: "ERROR_COUNT", DEBUG: "DEBUG",
        HEARTBEAT: "HEARTBEAT", LOCK: "LOCK",
        FILTER_CFG: "FILTER_CFG", FILTERED_COUNT: "FILTERED_COUNT", GT_CTRL: "GT_CTRL"}

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
    wr(GT_CTRL, (val | reinit) & 0xFFFFFFFF)   # config + RX re-init asserted
    time.sleep(0.05 if full else 0.02)
    wr(GT_CTRL, val & ~reinit & 0xFFFFFFFF)     # release: RX re-inits & re-aligns under new config
    time.sleep(0.20 if full else 0.10)
    rb = rd(GT_CTRL)
    _td = (val >> 9) & 0x1F
    want = val & ~reinit & 0xFFFFFF             # config bits only (the re-init pulses self-release to 0)
    if (rb & 0xFFFFFF) != want:
        say("# !! WARNING: GT_CTRL readback 0x%08X != written 0x%06X (masked) -- AXI write may "
            "have failed (wrong stride / wedged write channel); the GT config did NOT change." % (rb, want))
    say("# GT_CTRL <- 0x%06X (rxpol=%d txpol=%d loopback=%d | txdiff=%s txpost=%d txpre=%d | full_reset=%d), readback=0x%08X" % (
        val, val & 1, (val >> 1) & 1, (val >> 2) & 7,
        ("0x18(dflt)" if _td == 0 else "0x%02X" % _td), (val >> 14) & 0x1F, (val >> 19) & 0x1F, int(full), rb))

def read_event():
    ev = rd(EVENT)
    event = ev & 0xFFFF
    flags = (ev >> 16) & 0xFFFF
    data = (rd(DATA_HI) << 32) | rd(DATA_LO)
    ts = (rd(TS_HI) << 32) | rd(TS_LO)
    wr(POP)
    return event, flags, data, ts

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
    dbg = rd(DEBUG)
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
        rd(EVENT_COUNT), rd(NULL_COUNT), rd(ERROR_COUNT), rd(FILTERED_COUNT),
        commadet, disperr, recover, rx_los, tx_fault, mod_abs, notintbl, byteali, rcv_algn,
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
set_gt_ctrl(GTCTRL, full=_gtreset)   # apply a known config (default 0x00 = normal) so a prior run can't bleed in

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
