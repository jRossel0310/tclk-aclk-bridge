"""Cocotb tests for rtl/uart_transmitter.sv — 8-N-1 UART TX, LSB first.

CLOCK_FREQ/BAUD_RATE are overridden in runner.py so each symbol is only a few
clock cycles. SYMBOL below MUST equal CLOCK_FREQ // BAUD_RATE from runner.py.
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
    dut.data_in.value = 0
    dut.data_in_valid.value = 0
    dut.reset.value = 1
    await ClockCycles(dut.clk, 3)
    dut.reset.value = 0
    await RisingEdge(dut.clk)
    await settle(dut)


async def send_and_check(dut, byte):
    """Hand `byte` to the TX, sample the serial line, and verify the frame."""
    await settle(dut)
    assert int(dut.data_in_ready.value) == 1, "TX should be ready before sending"

    # Handshake: byte is accepted on the edge where valid & ready are both high.
    dut.data_in.value = byte
    dut.data_in_valid.value = 1
    await RisingEdge(dut.clk)        # accepted; start bit now on the line
    dut.data_in_valid.value = 0

    # Sample each of the 10 symbols at its midpoint: start, d0..d7, stop.
    await ClockCycles(dut.clk, SYMBOL // 2)
    bits = []
    for i in range(10):
        await settle(dut)
        bits.append(int(dut.serial_out.value))
        if i < 9:
            await ClockCycles(dut.clk, SYMBOL)

    assert bits[0] == 0, f"start bit should be 0, got {bits[0]}"
    assert bits[9] == 1, f"stop bit should be 1, got {bits[9]}"
    decoded = sum(bits[1 + b] << b for b in range(8))   # LSB first
    assert decoded == byte, f"sent 0x{byte:02X}, line carried 0x{decoded:02X}"

    # TX should return to idle/ready within a symbol.
    await ClockCycles(dut.clk, SYMBOL)
    await settle(dut)
    assert int(dut.data_in_ready.value) == 1, "TX should be ready again after the frame"
    assert int(dut.serial_out.value) == 1, "line should idle high after the frame"
    dut._log.info(f"byte OK: 0x{byte:02X} transmitted and decoded correctly")


@cocotb.test()
async def test_uart_tx_idle_after_reset(dut):
    """After reset the line idles high and the TX is ready."""
    start_clock(dut)
    await reset_dut(dut)
    assert int(dut.serial_out.value) == 1, "serial_out should idle high"
    assert int(dut.data_in_ready.value) == 1, "TX should be ready after reset"
    dut._log.info("reset OK: idle high, ready")


@cocotb.test()
async def test_uart_tx_transmits_bytes(dut):
    """Several bytes transmit correctly, back to back."""
    start_clock(dut)
    await reset_dut(dut)
    for byte in (0x00, 0xFF, 0xA5, 0x3C, 0x81):
        await send_and_check(dut, byte)
