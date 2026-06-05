"""Cocotb tests for rtl/uart_echo_top.sv — UART receive -> FIFO -> transmit echo.

CLOCK_FREQ/BAUD_RATE are overridden in runner.py so each symbol is only a few
clock cycles. SYMBOL below MUST equal CLOCK_FREQ // BAUD_RATE from runner.py.
The test drives a byte onto serial_in and checks the same byte comes back,
framed 8-N-1, on serial_out.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

CLK_PERIOD_NS = 10

# Keep in sync with runner.py: CLOCK_FREQ // BAUD_RATE.
SYMBOL = 10


def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())


async def reset_dut(dut):
    dut.serial_in.value = 1          # idle high
    dut.reset.value = 1
    await ClockCycles(dut.clk, 3)
    dut.reset.value = 0
    await RisingEdge(dut.clk)


async def serial_send(dut, byte):
    """Drive one 8-N-1 frame onto serial_in: start, d0..d7 (LSB first), stop."""
    dut.serial_in.value = 0                       # start bit
    await ClockCycles(dut.clk, SYMBOL)
    for b in range(8):
        dut.serial_in.value = (byte >> b) & 1     # data, LSB first
        await ClockCycles(dut.clk, SYMBOL)
    dut.serial_in.value = 1                        # stop bit / return to idle
    await ClockCycles(dut.clk, SYMBOL)


async def serial_recv(dut, timeout_symbols=400):
    """Wait for a start bit on serial_out, then sample one 8-N-1 frame."""
    # Wait for the line to leave idle (start bit pulls it low).
    for _ in range(timeout_symbols * SYMBOL):
        await RisingEdge(dut.clk)
        if int(dut.serial_out.value) == 0:
            break
    else:
        assert False, "timed out waiting for serial_out start bit"

    # Advance to the middle of the first data bit: finish the start symbol, then
    # half a symbol more.
    await ClockCycles(dut.clk, SYMBOL + SYMBOL // 2)
    byte = 0
    for b in range(8):
        byte |= (int(dut.serial_out.value) & 1) << b
        await ClockCycles(dut.clk, SYMBOL)
    assert int(dut.serial_out.value) == 1, "missing stop bit on serial_out"
    return byte


@cocotb.test()
async def test_echo_single_byte(dut):
    """A single byte sent in is echoed back unchanged."""
    start_clock(dut)
    await reset_dut(dut)

    recv = cocotb.start_soon(serial_recv(dut))
    await serial_send(dut, 0xA5)
    got = await recv

    assert got == 0xA5, f"echo mismatch: sent 0xA5, got 0x{got:02X}"
    dut._log.info("echo OK: 0xA5")


@cocotb.test()
async def test_echo_multiple_bytes(dut):
    """Several bytes are echoed back in order (exercises the FIFO buffering)."""
    start_clock(dut)
    await reset_dut(dut)

    data = [0x00, 0xFF, 0x3C, 0x81]
    received = []

    async def collector():
        for _ in range(len(data)):
            received.append(await serial_recv(dut))

    col = cocotb.start_soon(collector())
    for byte in data:
        await serial_send(dut, byte)
        await ClockCycles(dut.clk, SYMBOL)        # brief idle gap between frames
    await col

    assert received == data, (
        f"echo mismatch: sent {[hex(b) for b in data]}, "
        f"got {[hex(b) for b in received]}"
    )
    dut._log.info(f"echo OK: {[hex(b) for b in received]}")
