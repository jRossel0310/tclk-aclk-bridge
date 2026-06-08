#!/usr/bin/env python3
"""Test the custom uart_echo RTL over AXI, from Linux on the KR260 — no pins, no
external adapter.

The bitstream wires an AMD AXI UART Lite (AXI base 0xA000_0000) cross-coupled to
the custom uart_echo:
    UART Lite  tx  -> uart_echo serial_in
    uart_echo  serial_out -> UART Lite rx
So a byte we transmit out of the UART Lite travels THROUGH our RTL (receiver ->
FIFO -> transmitter) and returns in the UART Lite's RX FIFO. If what we read back
equals what we sent, the custom UART works on real hardware.

Run as root, AFTER loading the bitstream (see deploy/README.md):
    sudo python3 uart_echo_test.py                # via /dev/mem  (offset = AXI base)
    sudo python3 uart_echo_test.py /dev/uio0      # via UIO       (offset = 0)

Use the UIO form if /dev/mem is locked down (STRICT_DEVMEM -> "Bus error").
"""

import mmap
import os
import struct
import sys
import time

BASE = 0x8000_0000          # UART Lite AXI base address (from assign_bd_address)
PAGE = 0x1000

# Pick the device: a /dev/uioN maps the peripheral at offset 0; /dev/mem needs
# the absolute AXI base as the mmap offset.
DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/mem"
OFFSET = 0 if "uio" in DEV else BASE

# AXI UART Lite register map (AMD PG142)
RX_FIFO   = 0x00            # read: received byte
TX_FIFO   = 0x04            # write: byte to transmit
STAT_REG  = 0x08            # bit0 RX-valid, bit2 TX-empty, bit3 TX-full
CTRL_REG  = 0x0C            # bit0 TX-reset, bit1 RX-reset

STAT_RX_VALID = 1 << 0
STAT_TX_FULL  = 1 << 3


def main():
    print(f"mapping {DEV} (offset 0x{OFFSET:08X})")
    fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
    regs = mmap.mmap(fd, PAGE, mmap.MAP_SHARED,
                     mmap.PROT_READ | mmap.PROT_WRITE, offset=OFFSET)

    def rd(off):       return struct.unpack("<I", regs[off:off + 4])[0]
    def wr(off, val):  regs[off:off + 4] = struct.pack("<I", val & 0xFFFFFFFF)

    # Clear both FIFOs to start from a known state.
    wr(CTRL_REG, 0x03)
    time.sleep(0.01)
    wr(CTRL_REG, 0x00)

    def echo(byte, timeout=1.0):
        t0 = time.time()
        while rd(STAT_REG) & STAT_TX_FULL:
            if time.time() - t0 > timeout:
                raise RuntimeError("TX FIFO stayed full")
        wr(TX_FIFO, byte)
        t0 = time.time()
        while not (rd(STAT_REG) & STAT_RX_VALID):
            if time.time() - t0 > timeout:
                raise RuntimeError("no echo — RX never became valid "
                                   "(is the bitstream loaded?)")
        return rd(RX_FIFO) & 0xFF

    fails = 0
    for b in (0x41, 0x55, 0xA5, 0x00, 0xFF, 0x3C):
        got = echo(b)
        ok = (got == b)
        fails += 0 if ok else 1
        print(f"sent 0x{b:02X}  got 0x{got:02X}  {'OK' if ok else 'MISMATCH'}")

    print("PASS — uart_echo works on hardware" if fails == 0
          else f"FAIL — {fails} mismatch(es)")
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
