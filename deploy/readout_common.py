"""Shared board-side helpers for the AXI-Lite readout register block.

One copy of everything the per-build readers (clk_read / tclk_read / aclk_read /
aclkgt_read) used to duplicate: the register map (16-byte stride), the mmap open,
the watchdog-guarded rd/wr accessors, event draining, the drop-filter application,
the startup probe, and the streaming loop. Pure logic stays off the mmap so it is
unit-testable on the PC (test_readout_common.py); only open_dev() touches
/dev/uio* and needs the board.
"""
import ctypes
import mmap
import os
import sys
import threading
import time

from tclk_filter import parse_drop_codes, filter_cfg_word  # noqa: F401 (re-exported)

# Registers are spaced 16 BYTES apart: the hand-written AXI4-Lite slave only returns
# correct data at 16-byte-aligned offsets on this board (non-aligned reads read 0).
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xA0
)
HEARTBEAT, LOCK = 0xB0, 0xC0   # free-running rx-domain counter (trust check); clock-alive bit
FILTER_CFG, FILTERED_COUNT = 0xD0, 0xE0   # drop-mask config (write); dropped-event count (read)
GT_CTRL = 0xF0                 # aclkgt builds only (rx/tx polarity, loopback, TX driver, resets)

NAME = {STATUS: "STATUS", EVENT: "EVENT", DATA_HI: "DATA_HI", DATA_LO: "DATA_LO",
        TS_HI: "TS_HI", TS_LO: "TS_LO", POP: "POP", EVENT_COUNT: "EVENT_COUNT",
        NULL_COUNT: "NULL_COUNT", ERROR_COUNT: "ERROR_COUNT", DEBUG: "DEBUG",
        HEARTBEAT: "HEARTBEAT", LOCK: "LOCK",
        FILTER_CFG: "FILTER_CFG", FILTERED_COUNT: "FILTERED_COUNT", GT_CTRL: "GT_CTRL"}


def say(msg):
    print(msg, flush=True)


def line_buffer_stdout():
    """Force line buffering so a freeze can never hide already-printed output.
    Guarded: not every stdout supports reconfigure."""
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass


def parse_args(argv, value_flags=(), bool_flags=()):
    """Tiny common CLI parser. value_flags consume the next token; bool_flags set
    True. Anything else (including unknown --flags) falls through to positionals,
    exactly like the hand-rolled loops the readers used before."""
    pos, flags = [], {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in value_flags and i + 1 < len(argv):
            flags[a] = argv[i + 1]
            i += 2
        elif a in bool_flags:
            flags[a] = True
            i += 1
        else:
            pos.append(a)
            i += 1
    return pos, flags


def dev_offset(dev):
    return 0 if "uio" in dev else 0x8000_0000


def wr_split(ts):
    """Split a packed White Rabbit timestamp into (sec, ns)."""
    return (ts >> 32) & 0xFFFFFFFF, ts & 0xFFFFFFFF


_utc_cache_sec = None       # last seconds value formatted (module-global memo)
_utc_cache_base = None      # its "%Y-%m-%dT%H:%M:%S" string


def wr_utc(ts):
    """Human-readable UTC for a packed WR timestamp. The strict-zero value (stamped while
    not WR-locked) renders as 'UNSYNC'.

    The strftime/gmtime of the SECONDS field is memoized for the current second: within one
    second only the nanoseconds change. This matters on the board -- glibc's strftime stats
    the timezone on every call (~ms each on the SD card), and the readout hot path formats
    ~100 events/s. Calling it once per second instead of twice per event took the PS drain
    from ~77 ev/s (the real overflow bottleneck) back above the ~100 ev/s decode rate. Only
    the drain thread calls this, so the module-global memo needs no lock."""
    if ts == 0:
        return "UNSYNC"
    global _utc_cache_sec, _utc_cache_base
    sec, ns = wr_split(ts)
    if sec != _utc_cache_sec:
        _utc_cache_sec = sec
        _utc_cache_base = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sec))
    return "%s.%09dZ" % (_utc_cache_base, ns)


class RegIO:
    """Watchdog-guarded 32-bit register access over a buffer: an mmap on the board,
    or any mutable buffer (e.g. bytearray) in unit tests.

    Watchdog rationale: an AXI read that never returns RVALID wedges the CPU's load
    instruction uninterruptibly (Ctrl-C will NOT break it). The daemon thread watches
    every access and, if one stalls >2 s, prints WHICH offset is stuck, pinning the
    freeze to a PL/BD fault (reset held, overlay/bitstream not loaded, dead bus),
    NOT a Python bug."""

    def __init__(self, buf, names=NAME):
        self.m = buf
        self.names = names
        self._watch = {"label": None, "t": 0.0}
        self._labels = {}          # (verb, offset) -> precomputed watchdog label string
        self._views = {}           # offset -> cached ctypes uint32 view into buf

    def _label(self, verb, o):
        """Watchdog label, precomputed per (verb, offset). Formatting it per access was
        ~8 us of the ~10.7 us per register access; a dict hit is ~0.1 us."""
        key = (verb, o)
        lbl = self._labels.get(key)
        if lbl is None:
            lbl = self._labels[key] = "%s %s (0x%02X)" % (verb, self.names.get(o, "?"), o)
        return lbl

    def _view(self, o):
        """Cached ctypes uint32 view of one register. ctypes scalar access is a single
        32-bit load/store (HW-verified for the store side); an mmap slice goes through
        glibc memcpy, which multi-loads/multi-stores (2 bus transactions for 4 bytes).
        Caching the view also keeps object creation off the hot path."""
        v = self._views.get(o)
        if v is None:
            v = self._views[o] = ctypes.c_uint32.from_buffer(self.m, o)
        return v

    def start_watchdog(self):
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _watchdog(self):
        warned = False
        while True:
            time.sleep(0.5)
            lbl = self._watch["label"]
            if lbl is None:
                warned = False
                continue
            if (time.monotonic() - self._watch["t"]) > 2.0 and not warned:
                say("# !! WATCHDOG: blocked >2s in %s" % lbl)
                say("# !! An AXI read is wedging the bus (reset held / overlay-bitstream not "
                    "loaded / wrong address). This is the PL/BD side, NOT this Python script.")
                say("# !! Ctrl-C cannot break a wedged AXI load; reload the bitstream+overlay "
                    "and re-check s_axi_aresetn / the dcm_locked tie-off.")
                warned = True

    def _enter(self, label):
        self._watch["t"] = time.monotonic()
        self._watch["label"] = label

    def _leave(self):
        self._watch["label"] = None

    def rd(self, o):
        self._enter(self._label("read", o))
        v = self._view(o).value
        self._leave()
        return v

    def wr(self, o, v=0):
        """Register write, exactly one AXI write transaction (ctypes scalar store).
        History: this used to be an mmap slice write, which goes through glibc memcpy
        and on aarch64 issues TWO overlapping 32-bit stores for a 4-byte copy = TWO AXI
        writes. Harmless for idempotent config registers (FILTER_CFG, GT_CTRL) but fatal
        for POP. Never reintroduce slice writes for device registers."""
        self._enter(self._label("write", o))
        self._view(o).value = v & 0xFFFFFFFF
        self._leave()

    def pulse(self, o):
        """Fire a write-sensitive register with EXACTLY ONE AXI write transaction.

        An mmap slice write memcpy-multi-stores: 2 stores for 4 bytes, 3 for 1 byte, each
        store a separate AXI write. On POP that popped the FIFO 2-3 times per call, and
        every extra pop while >=2 events were buffered silently DISCARDED an unread event.
        That ate the second member of every back-to-back TCLK chain (~24% of all events;
        the survivor gap was quantized at 2x the 1.2 us on-wire chain pitch). HW-verified
        2026-07-15: draining N buffered events took N/2 slice-write pops vs N ctypes pops
        (147 vs 298 for ~297 events). A ctypes scalar assignment stores the word exactly
        once. The written value is 0; POP fires on any write."""
        self._enter(self._label("pulse", o))
        self._view(o).value = 0
        self._leave()


def open_dev(dev, announce=True, watchdog=True):
    """mmap the readout register block (board-side only); returns a RegIO."""
    off = dev_offset(dev)
    if announce:
        say("# opening %s (offset 0x%x) ..." % (dev, off))
    fd = os.open(dev, os.O_RDWR | os.O_SYNC)
    m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE, offset=off)
    io = RegIO(m)
    if watchdog:
        if announce:
            say("# mmap ok (0x1000 bytes). starting watchdog ...")
        io.start_watchdog()
    return io


def read_event(io):
    """Read one buffered event (EVENT + optional DATA + TS) and pop it from the FIFO.
    DATA_HI/DATA_LO are read only when the head's has_data flag (FLAGS bit0) is set;
    payload-less events (every TCLK event) skip those two AXI reads."""
    ev = io.rd(EVENT)
    event = ev & 0xFFFF
    flags = (ev >> 16) & 0xFFFF
    if flags & 1:
        data = (io.rd(DATA_HI) << 32) | io.rd(DATA_LO)
    else:
        data = 0
    ts = (io.rd(TS_HI) << 32) | io.rd(TS_LO)
    io.pulse(POP)      # pulse, NOT a slice write: that double-fires POP (see pulse())
    return event, flags, data, ts


def read_hw_counters(io):
    """Snapshot the read-only diagnostic counters for one readout block. Reads only
    (no POP), so it is safe to call from the drain thread without disturbing the FIFO.
    overflow is STATUS bit1 (sticky: an enqueued event was lost to a full FIFO); lock
    is LOCK bit0 (MMCM locked)."""
    status = io.rd(STATUS)
    return {
        "event_count":    io.rd(EVENT_COUNT),
        "null_count":     io.rd(NULL_COUNT),
        "error_count":    io.rd(ERROR_COUNT),
        "filtered_count": io.rd(FILTERED_COUNT),
        "overflow":       (status >> 1) & 1,
        "lock":           io.rd(LOCK) & 1,
        "heartbeat":      io.rd(HEARTBEAT),
    }


def apply_drop_filter(io, drop_codes):
    for c in drop_codes:
        io.wr(FILTER_CFG, filter_cfg_word(c))
    if drop_codes:
        say("# drop-mask: suppressing " + ", ".join("0x%02X" % c for c in drop_codes))


def probe(io, counters, lock_desc, red_flag, trust_ok, stuck_warn):
    """One-time startup read of each register in `counters`, announced BEFORE each
    access (a freeze names the wedged offset), then the heartbeat TRUST CHECK:
    the counters cross clock domains through cdc_gray_count, and the free-running
    heartbeat (0xB0) takes the SAME path, so heartbeat moving => readback works.
    The three verdict strings differ per build and are passed in by the reader."""
    say("# --- startup probe (a freeze here names the wedged offset) ---")
    for o in counters:
        say("#   reading %-12s 0x%02X ..." % (NAME[o], o))
        say("#     %-12s = 0x%08X" % (NAME[o], io.rd(o)))
    lock = io.rd(LOCK) & 1
    hb1 = io.rd(HEARTBEAT)
    time.sleep(0.05)
    hb2 = io.rd(HEARTBEAT)
    say("#   %s (0xC0) = %d   heartbeat (0xB0): %d -> %d (+%d)"
        % (lock_desc, lock, hb1, hb2, hb2 - hb1))
    if lock != 1:
        say("# --- RED FLAG: " + red_flag + " ---")
    elif hb2 != hb1 and hb1 != 0:
        say("# --- TRUST OK: " + trust_ok + " ---")
    else:
        say("# --- WARNING: " + stuck_warn + " ---")
    say("# --- probe complete: AXI reads return, the bus is alive. ---")


def stream_events(io, tick_ns, stats_line, format_event, header, wr=False):
    """The shared drain loop: poll STATUS, print each event via format_event, emit a
    stats line every ~1 s while idle. Runs until Ctrl-C, then prints a final stats
    line. format_event(ts, dt, event, data, is_tclk, has_data) -> str.
    wr=True: ts is a WR {sec, ns} pair; dt_us comes from real nanoseconds and is
    suppressed around UNSYNC (zero) stamps. wr=False: legacy tick behavior."""
    say(header)
    last_ts = None
    last_stats = time.monotonic()
    try:
        while True:
            if io.rd(STATUS) & 0x1:                    # empty
                now = time.monotonic()
                if now - last_stats >= 1.0:
                    say(stats_line())
                    last_stats = now
                time.sleep(0.001)
                continue
            event, flags, data, ts = read_event(io)
            is_tclk = (flags >> 1) & 1
            has_data = flags & 1
            if wr:
                if last_ts in (None, 0) or ts == 0:
                    dt = "   --  "
                else:
                    s2, n2 = wr_split(ts)
                    s1, n1 = wr_split(last_ts)
                    dt = "%7.1f" % (((s2 - s1) * 1_000_000_000 + (n2 - n1)) / 1000.0)
            else:
                dt = "   --  " if last_ts is None else "%7.1f" % ((ts - last_ts) * tick_ns / 1000.0)
            last_ts = ts
            say(format_event(ts, dt, event, data, is_tclk, has_data))
    except KeyboardInterrupt:
        say("\n# stopped.")
        say(stats_line())


def drain_events(io, on_event, idle_cb=None, poll_s=0.0002, tick_cb=None, tick_s=60.0,
                 batch=4096):
    """Shared drain loop for the Redis publisher. Each pass drains EVERY event currently
    buffered, in a tight inner loop (bounded by `batch`), then sleeps `poll_s` only when the
    FIFO is genuinely empty. on_event(evt) gets a decoded dict; the event is popped.

    poll_s is 200 us (was 1 ms), and the inner loop drains the whole FIFO per pass (the old
    loop serviced ONE event per outer iteration and slept 1 ms the instant it caught up). On
    hardware the old structure lost ~25% of a sustained ~100 ev/s stream: after each catch-up
    the drain sat idle ~1 ms while the multi-threaded GIL handed off to the Redis sink, so its
    effective service rate fell just below the arrival rate, the readout FIFO stayed full, and
    it shed the overflow -- and NO FIFO depth fixed it, because the PS drain, not the buffer,
    was the bottleneck. Draining everything each pass and polling tightly keeps the drain ahead.

    While the FIFO is empty, idle_cb() is called at most once per second (for a stats line).
    tick_cb() is called at most once per tick_s seconds regardless of load (for the periodic
    snapshot); the `batch` bound returns to this housekeeping even under a never-empty overload
    so the snapshot still fires. Returns on KeyboardInterrupt. Source-agnostic: does NOT filter
    (the publisher drops UNSYNC). The console readers use stream_events instead; this is a
    separate, simpler loop kept deliberately."""
    last_idle = time.monotonic()
    last_tick = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if tick_cb is not None and now - last_tick >= tick_s:
                tick_cb()
                last_tick = now
            got = 0
            while got < batch and not (io.rd(STATUS) & 0x1):    # drain all buffered, tight
                event, flags, data, ts = read_event(io)
                on_event({
                    "event": event, "flags": flags, "data": data, "ts": ts,
                    "is_tclk": (flags >> 1) & 1, "has_data": flags & 1,
                })
                got += 1
            if got == 0:                                        # FIFO empty
                if idle_cb is not None and now - last_idle >= 1.0:
                    idle_cb()
                    last_idle = now
                time.sleep(poll_s)
    except KeyboardInterrupt:
        pass
