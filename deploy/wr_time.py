#!/usr/bin/env python3
"""Arm and monitor the White Rabbit timebase over UIO (wr_timebase_axi @ 0x8002_0000).

    sudo python3 wr_time.py /dev/uio6 status     # lock state + HW-vs-system delta
    sudo python3 wr_time.py /dev/uio6 arm        # arm the next-PPS Unix label from NTP time
    sudo python3 wr_time.py /dev/uio6 disarm     # force unlock (CTRL[1])
    sudo python3 wr_time.py /dev/uio6 clear      # clear the lost_lock sticky (CTRL[0])
    sudo python3 wr_time.py /dev/uio6 guard      # unattended: auto re-arm on any unlock

Arm protocol: the PS system clock is NTP-disciplined; mid-second (to avoid racing
the PPS boundary) we write floor(now)+1, the label of the NEXT PPS, to SEC_ARM.
Hardware loads it at that PPS in every timebase copy and locks. Verify compares
SEC_NOW against the system clock afterwards. STRICT: any reference loss (or a GT
relock, which resets the ACLK replica) unlocks and needs a fresh `arm`.
"""
import sys
import time

import readout_common as rc
from readout_common import say

# wr_timebase_axi register map (16-byte stride)
(WR_STATUS, WR_SEC_ARM, WR_SEC_NOW, WR_NS_NOW, WR_PPS_COUNT, WR_CELLS_LAST,
 WR_CTRL, WR_PPS_REJECT) = (0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70)
WR_NAME = {WR_STATUS: "STATUS", WR_SEC_ARM: "SEC_ARM", WR_SEC_NOW: "SEC_NOW",
           WR_NS_NOW: "NS_NOW", WR_PPS_COUNT: "PPS_COUNT",
           WR_CELLS_LAST: "CELLS_LAST", WR_CTRL: "CTRL",
           WR_PPS_REJECT: "PPS_REJECT"}

CELLS_EXPECTED = 10_000_000     # 10 MHz cells per real second
ARM_FRAC_LO, ARM_FRAC_HI = 0.10, 0.80   # safe window within the second for arming


def next_pps_label(t):
    """Unix UTC label of the PPS that follows system time t."""
    return int(t) + 1


def decode_status(v):
    return {
        "locked_tclk": bool(v & 0x001),
        "locked_aclk": bool(v & 0x002),
        "locked_mon":  bool(v & 0x004),
        "pps_alive":   bool(v & 0x008),
        "clk10_alive": bool(v & 0x010),
        "arm_pending": bool(v & 0x020),
        "lost_lock":   bool(v & 0x100),
    }


def read_now(io):
    """Atomic {sec, ns} pair: the SEC_NOW read latches NS_NOW in hardware."""
    sec = io.rd(WR_SEC_NOW)
    ns = io.rd(WR_NS_NOW)
    return sec, ns


def cmd_status(io):
    s = decode_status(io.rd(WR_STATUS))
    say("# STATUS: " + "  ".join("%s=%d" % (k, int(v)) for k, v in s.items()))
    say("# PPS_COUNT=%d  CELLS_LAST=%d (expect %d; far off => flaky 10 MHz or PPS line)"
        % (io.rd(WR_PPS_COUNT), io.rd(WR_CELLS_LAST), CELLS_EXPECTED))
    # Each rejected edge is a glitch the PL refused to turn into a whole extra
    # second. Before the filter existed these were accepted silently: a 2026-08-03
    # capture ran 4 s fast for 5 h with every health flag green.
    rejected = io.rd(WR_PPS_REJECT)
    if rejected:
        say("# !! PPS_REJECT=%d spurious PPS edge(s) discarded. The WR PPS line is "
            "glitching; each one would have added a whole second. Scope the PPS pin."
            % rejected)
    else:
        say("# PPS_REJECT=0 (no spurious PPS edges seen)")
    sec, ns = read_now(io)
    if sec == 0 and ns == 0:
        say("# HW time: UNSYNC (strict zero: not armed / not locked)")
    else:
        sys_t = time.time()
        delta = (sec + ns / 1e9) - sys_t
        say("# HW time: %s  (HW - system clock = %+0.6f s)"
            % (rc.wr_utc((sec << 32) | ns), delta))
        if abs(delta) > 0.5:
            say("# !! WARNING: HW seconds disagree with the system clock; re-run 'arm'.")
    return s


def cmd_arm(io):
    # wait for a mid-second moment so the SEC_ARM write cannot race the PPS
    while True:
        t = time.time()
        frac = t % 1.0
        if ARM_FRAC_LO <= frac <= ARM_FRAC_HI:
            break
        time.sleep(0.02)
    label = next_pps_label(t)
    io.wr(WR_SEC_ARM, label)
    rb = io.rd(WR_SEC_ARM)
    if rb != label:
        say("# !! SEC_ARM readback 0x%08X != written 0x%08X; AXI write failed." % (rb, label))
        return
    say("# armed %d (UTC label of the next PPS); waiting for lock ..." % label)
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline:
        s = decode_status(io.rd(WR_STATUS))
        if s["locked_mon"] and s["locked_tclk"] and s["locked_aclk"]:
            break
        time.sleep(0.05)
    s = decode_status(io.rd(WR_STATUS))
    if not (s["locked_mon"] and s["locked_tclk"] and s["locked_aclk"]):
        say("# !! arm did not reach full lock within timeout; check STATUS below "
            "(pps_alive/clk10_alive and CELLS_LAST). Re-run 'arm' or check the WR wiring.")
    cmd_status(io)


def cmd_guard(io, interval=2.0, arm_retry=5.0, logpath="wr-guard.log"):
    """Unattended lock guard for multi-day runs. The timebase is deliberately STRICT:
    ~400 ns of missing 10 MHz edges, ~1 s of missing PPS, or a GT relock (which resets
    the ACLK replica) unlocks it PERMANENTLY until a fresh arm, and every event stamped
    while unlocked is UNSYNC-dropped by the publisher. This loop polls STATUS and
    re-arms automatically, turning a WR blip into a seconds-long UNSYNC gap instead of
    a dead remainder-of-run. Re-arm labels come from the system clock, so keep NTP
    (chrony) running. Lock transitions are logged (timestamped) to wr-guard.log; the
    lost_lock sticky is left latched as evidence. Ctrl-C to stop."""
    def log(msg):
        line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg)
        say(line)
        try:
            with open(logpath, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass

    log("# guard: start (poll %.0fs; log -> %s)" % (interval, logpath))
    was_full = None
    down_mono = None
    last_arm = 0.0
    rearms = 0
    try:
        while True:
            s = decode_status(io.rd(WR_STATUS))
            full = s["locked_mon"] and s["locked_tclk"] and s["locked_aclk"]
            if full != was_full:
                if full:
                    if down_mono is not None:
                        log("# guard: LOCKED again after %.0f s (re-arms so far: %d)"
                            % (time.monotonic() - down_mono, rearms))
                    else:
                        log("# guard: locked.")
                    down_mono = None
                else:
                    down_mono = time.monotonic()
                    log("# guard: UNLOCKED: " +
                        "  ".join("%s=%d" % (k, int(v)) for k, v in s.items()))
                was_full = full
            # Re-arm when unlocked, but never spam: an arm stays pending in hardware
            # until a PPS consumes it, and repeat attempts are throttled to arm_retry.
            if not full and not s["arm_pending"] and (time.monotonic() - last_arm) >= arm_retry:
                rearms += 1
                last_arm = time.monotonic()
                log("# guard: re-arming (attempt %d)" % rearms)
                cmd_arm(io)
            time.sleep(interval)
    except KeyboardInterrupt:
        log("# guard: stopped (re-arms: %d)" % rearms)


def main(argv):
    rc.line_buffer_stdout()
    pos, _flags = rc.parse_args(argv)
    dev = pos[0] if pos else "/dev/uio6"
    cmd = pos[1] if len(pos) > 1 else "status"
    io = rc.open_dev(dev)
    io.names = WR_NAME
    if cmd == "status":
        cmd_status(io)
    elif cmd == "arm":
        cmd_arm(io)
    elif cmd == "disarm":
        io.wr(WR_CTRL, 0x2)
        say("# disarmed (all copies unlock; timestamps read UNSYNC until re-armed).")
    elif cmd == "clear":
        io.wr(WR_CTRL, 0x1)
        say("# lost_lock sticky cleared.")
    elif cmd == "guard":
        cmd_guard(io)
    else:
        say("usage: wr_time.py /dev/uioN [status|arm|disarm|clear|guard]")


if __name__ == "__main__":
    main(sys.argv[1:])
