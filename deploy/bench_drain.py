#!/usr/bin/env python3
"""Measure the sustained drain ceiling of a readout UIO: how many events per
second the PS can pop, and whether the sticky overflow bit sets under load.

On the board:  sudo python3 bench_drain.py /dev/uio5 --seconds 10   # aclk
The FIFO must be actively fed (run the generator / live line) during the run.
"""
import argparse
import time


def measure_rate(reader, seconds, now=time.monotonic):
    """Drain in a tight loop for `seconds`, return throughput + overflow.

    reader.drain_once() pops all currently-buffered events and returns the count;
    reader.overflow() reports the sticky hardware overflow bit.
    """
    t0 = now()
    reads = 0
    overflow = False
    while True:
        reads += reader.drain_once()
        overflow = overflow or reader.overflow()
        elapsed = now() - t0
        if elapsed >= seconds:
            break
    return {
        "reads": reads,
        "elapsed_s": elapsed,
        "reads_per_s": reads / elapsed if elapsed else 0.0,
        "overflow": overflow,
    }


def _main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dev")
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args(argv)
    from readout_common import RegIO           # board-only import
    io = RegIO(args.dev)

    class _R:
        def drain_once(self):
            n = 0
            while not (io.rd(0x00) & 0x1):      # STATUS bit0 = empty
                io.rd(0x10); io.rd(0x40); io.rd(0x50)
                io.pulse(0x60)                  # single-store POP (see readout_common)
                n += 1
            return n
        def overflow(self):
            return bool(io.rd(0x00) & 0x2)      # STATUS bit1 = sticky overflow

    out = measure_rate(_R(), args.seconds)
    print(f"drain: {out['reads']} events in {out['elapsed_s']:.2f}s = "
          f"{out['reads_per_s']:.0f}/s  overflow={out['overflow']}")


if __name__ == "__main__":
    _main()
