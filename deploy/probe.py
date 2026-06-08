#!/usr/bin/env python3
"""Is 0x8000_0000 actually our UART Lite, or just RAM/dead space returning zeros?

Writes a pattern to offset 0x00 (the UART Lite's RX FIFO, which is READ-ONLY on a
real UART Lite) and reads it back. If the pattern sticks, it's plain memory and the
address is NOT reaching the peripheral. If it doesn't stick, it's a peripheral.

    sudo python3 probe.py /dev/uio4
"""
import mmap, os, struct, sys

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/uio4"
OFF = 0 if "uio" in DEV else 0x8000_0000

fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OFF)
def rd(o): return struct.unpack("<I", m[o:o+4])[0]
def wr(o, v): m[o:o+4] = struct.pack("<I", v & 0xFFFFFFFF)

print("initial reads:  0x00=%08x  0x04=%08x  0x08=%08x  0x0C=%08x"
      % (rd(0x00), rd(0x04), rd(0x08), rd(0x0C)))

wr(0x00, 0xDEADBEEF); a = rd(0x00)
wr(0x00, 0x12345678); b = rd(0x00)
print("write-readback @0x00: wrote DEADBEEF->read %08x ; wrote 12345678->read %08x" % (a, b))

if a == 0xDEADBEEF and b == 0x12345678:
    print(">>> It's PLAIN MEMORY — 0x80000000 is NOT reaching the UART Lite (routing problem).")
else:
    print(">>> Writes don't stick — it IS the peripheral, but it's stuck/in reset.")
