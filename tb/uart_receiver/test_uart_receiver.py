"""Cocotb tests for rtl/uart_receiver.sv — 8-N-1 UART RX, LSB first.

CLOCK_FREQ/BAUD_RATE are overridden in runner.py so each symbol is only a few
clock cycles. SYMBOL below MUST equal CLOCK_FREQ // BAUD_RATE from runner.py.
The test drives `serial_in` bit-by-bit, holding each symbol for SYMBOL cycles,
which keeps the driver's symbol windows aligned with the receiver's.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK_PERIOD_NS = 10

# Keep in sync with runner.py: CLOCK_FREQ // BAUD_RATE.
SYMBOL = 10


def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())


async def settle(dut):
    await Timer(1, unit="ns")


async def reset_dut(dut):
    dut.serial_in.value = 1          # idle high
    dut.data_out_ready.value = 0
    dut.reset.value = 1
    await ClockCycles(dut.clk, 3)
    dut.reset.value = 0
    await RisingEdge(dut.clk)
    await settle(dut)


async def serial_send(dut, byte):
    """Drive one 8-N-1 frame onto serial_in: start, d0..d7 (LSB first), stop."""
    dut.serial_in.value = 0                       # start bit
    await ClockCycles(dut.clk, SYMBOL)
    for b in range(8):
        dut.serial_in.value = (byte >> b) & 1     # data, LSB first
        await ClockCycles(dut.clk, SYMBOL)
    dut.serial_in.value = 1                        # stop bit / return to idle
    await ClockCycles(dut.clk, SYMBOL)


async def recv_and_check(dut, expected):
    """Wait for a received byte, verify it, and accept it via the handshake."""
    for _ in range(SYMBOL * 12):
        await RisingEdge(dut.clk)
        await settle(dut)
        if int(dut.data_out_valid.value) == 1:
            break
    else:
        assert False, "timed out waiting for data_out_valid"

    got = int(dut.data_out.value)
    assert got == expected, f"expected 0x{expected:02X}, received 0x{got:02X}"

    # Accept the byte; valid must drop.
    dut.data_out_ready.value = 1
    await RisingEdge(dut.clk)
    dut.data_out_ready.value = 0
    await settle(dut)
    assert int(dut.data_out_valid.value) == 0, "valid should clear after data_out_ready"
    dut._log.info(f"byte OK: received 0x{expected:02X}")


@cocotb.test()
async def test_uart_rx_idle_after_reset(dut):
    """With an idle line, no byte is reported."""
    start_clock(dut)
    await reset_dut(dut)
    await ClockCycles(dut.clk, SYMBOL)
    await settle(dut)
    assert int(dut.data_out_valid.value) == 0, "no data should be valid on an idle line"
    dut._log.info("reset OK: idle line reports no data")


@cocotb.test()
async def test_uart_rx_receives_bytes(dut):
    """Several bytes are received and decoded correctly, one after another."""
    start_clock(dut)
    await reset_dut(dut)
    for byte in (0x00, 0xFF, 0xA5, 0x3C, 0x81):
        await serial_send(dut, byte)
        await recv_and_check(dut, byte)
        await ClockCycles(dut.clk, SYMBOL)   # idle gap before the next frame
