#!/usr/bin/env python3
"""Live WR PPS monitor: running edge count, outage alarms, and walk rate.

    sudo python3 wr_pps_live.py /dev/uio6            # one line per second, Ctrl-C to stop

Columns: UTC, PPS_COUNT, cumulative MISSING edges (wall seconds minus counted
edges since start), PPS_REJECT, HW-minus-system delta, and the walk rate in ppm
over the last 60 s and since start.

The rates come from the sub-second phase of the hardware clock measured against the
NTP-disciplined system clock, unwrapped to the nearest half-second step (`wrap_half`).
Whole-second events therefore leave the rate estimate alone, which matters because a
re-arm relabel or an outage's worth of lost seconds is exactly that. The blind spot is
a genuine sub-second phase step of more than half a second: it aliases, and you would
need to restart the monitor. Polling can never time an edge better than the poll
period, but the phase method is good to the chrony floor of ~10 us, so about 0.2 ppm
after a minute and 0.01 ppm after twenty.

Written 2026-08-04, the day the grandmaster was being worked on live. Pair it with a
wiggle test on the PPS coax to tell upstream dropouts (the count stalls, REJECT stays
at 0) apart from connector chatter (REJECT climbs)."""
import sys
import time

import readout_common as rc
from readout_common import say
from wr_time import (WR_STATUS, WR_PPS_COUNT, WR_PPS_REJECT,
                     decode_status, read_now)


def wrap_half(x):
    """Map a phase difference onto [-0.5, 0.5), undoing whole-second jumps."""
    return ((x + 0.5) % 1.0) - 0.5


class Walk:
    """Accumulates unwrapped phase and reports slope over a trailing window."""

    def __init__(self):
        self.hist = []          # (wall_t, accumulated_phase)
        self.acc = 0.0
        self.prev_frac = None

    def add(self, t, delta):
        frac = delta % 1.0
        if self.prev_frac is not None:
            self.acc += wrap_half(frac - self.prev_frac)
        self.prev_frac = frac
        self.hist.append((t, self.acc))

    def ppm(self, window_s=None):
        """Slope in ppm over the trailing window (None = since start)."""
        if len(self.hist) < 2:
            return None
        t1, p1 = self.hist[-1]
        t0, p0 = self.hist[0]
        if window_s is not None:
            for t, p in self.hist:
                if t >= t1 - window_s:
                    t0, p0 = t, p
                    break
        if t1 - t0 < 10.0:
            return None
        return (p1 - p0) / (t1 - t0) * 1e6


def main(argv):
    rc.line_buffer_stdout()
    dev = argv[1] if len(argv) > 1 else "/dev/uio6"
    io = rc.open_dev(dev)
    walk = Walk()
    t_start = None      # baselined on the first loop sample so MISS starts at 0
    count_start = None
    last_count = None
    say("#      UTC              COUNT  MISS  REJ   HW-sys (s)    ppm(60s)  ppm(run)")
    try:
        while True:
            t = time.time()
            s = decode_status(io.rd(WR_STATUS))
            count = io.rd(WR_PPS_COUNT)
            rej = io.rd(WR_PPS_REJECT)
            sec, ns = read_now(io)
            if count_start is None:
                t_start, count_start, last_count = t, count, count
            missing = int(round(t - t_start)) - (count - count_start)
            stamp = time.strftime("%H:%M:%S", time.gmtime(t))
            if not (sec or ns):
                say("# %s  %8d  %4d  %3d   UNSYNC (%s)"
                    % (stamp, count, missing, rej,
                       "PPS DEAD" if not s["pps_alive"] else "not armed"))
            else:
                delta = (sec + ns / 1e9) - t
                walk.add(t, delta)
                p60 = walk.ppm(60.0)
                prun = walk.ppm()
                say("# %s  %8d  %4d  %3d  %+12.6f  %8s  %8s%s"
                    % (stamp, count, missing, rej, delta,
                       "     -" if p60 is None else "%+8.3f" % p60,
                       "     -" if prun is None else "%+8.3f" % prun,
                       "" if count != last_count or s["pps_alive"] else "  !! PPS STALLED"))
            last_count = count
            time.sleep(max(0.0, 1.0 - (time.time() % 1.0)) or 1.0)
    except KeyboardInterrupt:
        say("# stopped: count %d, missing %d, reject %d"
            % (last_count, int(round(time.time() - t_start)) - (last_count - count_start),
               io.rd(WR_PPS_REJECT)))


if __name__ == "__main__":
    main(sys.argv)
