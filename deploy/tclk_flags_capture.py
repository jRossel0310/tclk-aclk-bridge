#!/usr/bin/env python3
"""Board-side capture of TCLK events WITH the fine-timing FLAGS, to a CSV the PC
analysis tools can read.

The normal capture path (redis_publish -> stream_archive) writes id,sec,ns,event,
data and DROPS the FLAGS field, so it cannot validate the multiphase fine-TDC.
This drains the readout directly and records the fine bits alongside the coarse
White-Rabbit timestamp. Output columns:

    id,sec,ns,event,fine_phase,fine_valid

- id       : a running sequence number (col 0, so marker_timing.py /
             tclk_faithfulness.py -- which read sec=col1, ns=col2, event=col3 --
             work unchanged on this file).
- sec,ns   : White-Rabbit coarse timestamp of the event's reference edge
             (wr_split of the packed 64-bit TS, i.e. frozen_coarse in the
             fine-TDC build).
- event    : 8-bit TCLK event code.
- fine_phase: FLAGS[3:2], the 1.25 ns sub-bin (0..3).
- fine_valid: FLAGS[4], 1 = sub-bin trusted, 0 = lost to real-line ringing.

Usage (on the KR260, in the deploy folder, WR already armed+locked):
    sudo python3 -u tclk_flags_capture.py /dev/uio4 -o events-tclk-flags.csv
    sudo python3 -u tclk_flags_capture.py /dev/uio4 -o out.csv --seconds 300
Ctrl-C to stop. Reuses readout_common (same register map + single-store POP).
"""
import sys
import time

import readout_common as rc
from readout_common import STATUS, read_event, wr_split, open_dev, wr_utc


def main():
    pos, flags = rc.parse_args(sys.argv[1:], value_flags=("-o", "--seconds"),
                               bool_flags=())
    dev = pos[0] if pos else "/dev/uio4"
    out = flags.get("-o", "events-tclk-flags.csv")
    dur = float(flags["--seconds"]) if "--seconds" in flags else None

    io = open_dev(dev)
    t0 = time.time()
    n = 0
    last_stat = t0
    with open(out, "w", buffering=1) as f:
        f.write("id,sec,ns,event,fine_phase,fine_valid\n")
        try:
            while True:
                if io.rd(STATUS) & 1:          # FIFO empty
                    time.sleep(0.0002)
                    if dur is not None and (time.time() - t0) >= dur:
                        break
                    now = time.time()
                    if now - last_stat >= 1.0:
                        last_stat = now
                        sys.stderr.write("[flags-capture] %d events, %.0f s\n"
                                         % (n, now - t0))
                    continue
                event, ev_flags, _data, ts = read_event(io)
                sec, ns = wr_split(ts)
                fine_phase = (ev_flags >> 2) & 0x3
                fine_valid = (ev_flags >> 4) & 0x1
                f.write("%d,%d,%d,%d,%d,%d\n"
                        % (n, sec, ns, event & 0xFF, fine_phase, fine_valid))
                n += 1
                if dur is not None and (time.time() - t0) >= dur:
                    break
        except KeyboardInterrupt:
            pass
    sys.stderr.write("[flags-capture] wrote %d events to %s\n" % (n, out))


if __name__ == "__main__":
    main()
