"""Cocotb tests for rtl/fifo.sv — a single-clock first-word fall-through FIFO.

WIDTH/DEPTH below MUST match the `parameters` passed in runner.py.
FWFT semantics: when not empty, `dout` already shows the oldest entry; a
one-cycle `rd_en` pop advances to the next.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK_PERIOD_NS = 10

# Keep in sync with runner.py's `parameters`.
WIDTH = 8
DEPTH = 4


def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())


async def settle(dut):
    await Timer(1, unit="ns")


async def reset_dut(dut):
    dut.wr_en.value = 0
    dut.rd_en.value = 0
    dut.din.value = 0
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    await settle(dut)


async def push(dut, value):
    """Write one value (caller guarantees the FIFO is not full)."""
    dut.din.value = value
    dut.wr_en.value = 1
    await RisingEdge(dut.clk)
    dut.wr_en.value = 0
    await settle(dut)


async def pop(dut):
    """Read the oldest value (FWFT) and pop it. Returns the value read."""
    await settle(dut)
    value = int(dut.dout.value)
    dut.rd_en.value = 1
    await RisingEdge(dut.clk)
    dut.rd_en.value = 0
    await settle(dut)
    return value


@cocotb.test()
async def test_fifo_reset_empty(dut):
    """After reset the FIFO is empty and not full."""
    start_clock(dut)
    await reset_dut(dut)
    assert int(dut.empty.value) == 1, "FIFO should be empty after reset"
    assert int(dut.full.value) == 0, "FIFO should not be full after reset"
    dut._log.info("reset OK: empty=1 full=0")


@cocotb.test()
async def test_fifo_fifo_order(dut):
    """Values come out in the order they went in."""
    start_clock(dut)
    await reset_dut(dut)

    data = [0x11, 0x22, 0x33]
    for v in data:
        await push(dut, v)
    assert int(dut.empty.value) == 0, "FIFO should be non-empty after writes"

    out = [await pop(dut) for _ in data]
    assert out == data, f"FIFO order broken: wrote {data}, read {out}"
    assert int(dut.empty.value) == 1, "FIFO should be empty after reading everything"
    dut._log.info(f"order OK: {out}")


@cocotb.test()
async def test_fifo_full_and_overflow(dut):
    """Filling to DEPTH asserts full; writes while full are ignored."""
    start_clock(dut)
    await reset_dut(dut)

    for i in range(DEPTH):
        await push(dut, 0xA0 + i)
    assert int(dut.full.value) == 1, f"FIFO should be full after {DEPTH} writes"

    # Attempt to overflow: a write while full must be dropped.
    dut.din.value = 0xFF
    dut.wr_en.value = 1
    await RisingEdge(dut.clk)
    dut.wr_en.value = 0
    await settle(dut)
    assert int(dut.full.value) == 1, "FIFO should remain full"

    # The original contents must be intact and in order (0xFF never stored).
    out = [await pop(dut) for _ in range(DEPTH)]
    assert out == [0xA0 + i for i in range(DEPTH)], f"overflow corrupted data: {out}"
    assert int(dut.empty.value) == 1, "FIFO should be empty after draining"
    dut._log.info("overflow OK: write-while-full dropped, data intact")


@cocotb.test()
async def test_fifo_simultaneous_read_write(dut):
    """A concurrent read+write keeps occupancy steady and preserves order."""
    start_clock(dut)
    await reset_dut(dut)

    await push(dut, 0x01)
    await push(dut, 0x02)

    # Read and write on the same cycle: pop 0x01 while pushing 0x03.
    await settle(dut)
    assert int(dut.dout.value) == 0x01, "oldest should be 0x01 before the combined op"
    dut.din.value = 0x03
    dut.wr_en.value = 1
    dut.rd_en.value = 1
    await RisingEdge(dut.clk)
    dut.wr_en.value = 0
    dut.rd_en.value = 0
    await settle(dut)

    # 0x01 popped, 0x03 pushed -> queue is now [0x02, 0x03].
    assert int(dut.full.value) == 0 and int(dut.empty.value) == 0
    assert (await pop(dut)) == 0x02, "0x02 should now be the oldest"
    assert (await pop(dut)) == 0x03, "0x03 should follow"
    assert int(dut.empty.value) == 1
    dut._log.info("simultaneous R/W OK: occupancy and order preserved")
