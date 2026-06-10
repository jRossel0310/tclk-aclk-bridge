#!/usr/bin/env python3
"""Is the PL running? Read the free-running counter (pl_heartbeat) through the AXI
GPIO at 0x8000_0000, several times. If the value changes between reads, the PL
fabric is configured AND clocked, i.e. your design is genuinely running.

    sudo python3 pltest.py /dev/uio4
"""
import mmap, os, struct, sys, time

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/uio4"
OFF = 0 if "uio" in DEV else 0x8000_0000

fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ, offset=OFF)

def rd(o):
    return struct.unpack("<I", m[o:o+4])[0]

vals = []
for _ in range(8):
    vals.append(rd(0x00))      # AXI GPIO data register = live counter value
    time.sleep(0.02)

for i, v in enumerate(vals):
    print("read %d:  0x%08x" % (i, v))

if len(set(vals)) > 1:
    print(">>> PL IS RUNNING — the counter is incrementing.")
elif vals[0] == 0x00000000:
    print(">>> stuck at 0 — counter not advancing or GPIO not reading it.")
else:
    print(">>> stuck at 0x%08x — reads work but the counter isn't advancing." % vals[0])
