#!/usr/bin/env python3
"""Diagnostic for the uart_echo AXI loopback: send one byte through the UART Lite
and watch its status register to see exactly where the echo chain breaks.

    sudo python3 diag.py /dev/uio4
"""
import mmap, os, struct, sys, time

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/uio4"
OFFSET = 0 if "uio" in DEV else 0x8000_0000

RX, TX, STAT, CTRL = 0x00, 0x04, 0x08, 0x0C

fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OFFSET)
def rd(o): return struct.unpack("<I", m[o:o+4])[0]
def wr(o, v): m[o:o+4] = struct.pack("<I", v & 0xFFFFFFFF)

def flags(s):
    f = []
    if s & 0x01: f.append("RXvalid")
    if s & 0x02: f.append("RXfull")
    if s & 0x04: f.append("TXempty")
    if s & 0x08: f.append("TXfull")
    if s & 0x20: f.append("OVERRUN")
    if s & 0x40: f.append("FRAME_ERR")
    if s & 0x80: f.append("PARITY_ERR")
    return " ".join(f) if f else "(none)"

wr(CTRL, 0x03); time.sleep(0.01); wr(CTRL, 0x00)        # reset both FIFOs
print("idle  status=0x%02x  %s" % (rd(STAT), flags(rd(STAT))))

print("writing 0x41 ('A') to TX FIFO...")
wr(TX, 0x41)

for i in range(80):
    s = rd(STAT)
    print("t=%2d  status=0x%02x  %s" % (i, s, flags(s)))
    if s & 0x01:
        b = rd(RX)
        print(">>> RX byte = 0x%02x  %s" % (b, "ECHO OK" if b == 0x41 else "MISMATCH"))
        break
    time.sleep(0.005)
else:
    print(">>> no RX byte arrived")
