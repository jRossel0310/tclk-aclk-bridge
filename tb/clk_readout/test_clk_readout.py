"""Full chain: drive real-framing 1/2/12-byte frames at the serial line, then read the
buffered events over AXI4-Lite and check event/data/flags/timestamps/counts. Exercises
serdec -> clk_byte_framer -> timestamp + async FIFO -> AXI, across the rx (40 MHz) and
AXI clock domains."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from clk_tx_model import stream_frames, drive_samples, SAMPLES_PER_CELL
from axi_lite_bfm import axi_read, axi_write

WARMUP_CELLS = 40
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, ERROR_COUNT = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x90)
FLAG_HAS_DATA = 0x1
FLAG_IS_TCLK = 0x2


async def reset_dut(dut):
    dut.clkline.value = 1
    dut.pps.value = 0
    dut.mmcm_locked.value = 1
    dut.rstn.value = 0
    dut.s_axi_aresetn.value = 0
    dut.s_axi_awaddr.value = 0
    dut.s_axi_awvalid.value = 0
    dut.s_axi_wdata.value = 0
    dut.s_axi_wstrb.value = 0
    dut.s_axi_wvalid.value = 0
    dut.s_axi_bready.value = 0
    dut.s_axi_araddr.value = 0
    dut.s_axi_arvalid.value = 0
    dut.s_axi_rready.value = 0
    await ClockCycles(dut.clk_80m, 10)
    await ClockCycles(dut.s_axi_aclk, 5)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    dut.s_axi_aresetn.value = 1
    await ClockCycles(dut.clk_80m, 10)
    await ClockCycles(dut.s_axi_aclk, 4)


async def read_one(dut):
    ev_reg = await axi_read(dut, EVENT)
    event = ev_reg & 0xFFFF
    flags = (ev_reg >> 16) & 0xFFFF
    dhi = await axi_read(dut, DATA_HI)
    dlo = await axi_read(dut, DATA_LO)
    thi = await axi_read(dut, TS_HI)
    tlo = await axi_read(dut, TS_LO)
    await axi_write(dut, POP)
    return (event, flags, (dhi << 32) | dlo, (thi << 32) | tlo)


@cocotb.test()
async def test_chain(dut):
    cocotb.start_soon(Clock(dut.clk_80m, 12500, unit="ps").start())
    cocotb.start_soon(Clock(dut.clk_40m, 25000, unit="ps").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, 14000, unit="ps").start())
    await reset_dut(dut)

    frames = [
        [0x07],
        [0x9D, 0xD2],
        [0x12, 0x34, 0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE, 0x00, 0x01, 0x5A, 0xC3],
        [0x0F],
    ]
    expected = [
        (0x0007, None, 1),
        (0x9DD2, None, 0),
        (0x1234, 0xDEADBEEFCAFE0001, 0),
        (0x000F, None, 1),
    ]

    samples = stream_frames(frames, warmup_cells=WARMUP_CELLS)
    warm_n = WARMUP_CELLS * SAMPLES_PER_CELL
    await drive_samples(dut.clk_80m, dut.clkline, samples[:warm_n])
    # Baseline error count after warm-up: serdec emits one spurious PERR while first
    # locking to the carrier (documented in test_tclk_readout.py). The clean-stream
    # check uses a delta so the startup PERR is excluded.
    await ClockCycles(dut.clk_40m, 20)
    await ClockCycles(dut.s_axi_aclk, 6)
    base_err = await axi_read(dut, ERROR_COUNT)

    await drive_samples(dut.clk_80m, dut.clkline, samples[warm_n:])
    await ClockCycles(dut.clk_40m, 40)
    await ClockCycles(dut.s_axi_aclk, 8)

    collected = []
    while True:
        if (await axi_read(dut, STATUS)) & 0x1:
            break
        collected.append(await read_one(dut))

    assert (await axi_read(dut, STATUS)) & 0x2 == 0, "overflow set: events dropped"
    assert len(collected) == len(expected), f"read {len(collected)} != {len(expected)}: {collected}"

    last_ts = -1
    for i, ((ev, flags, data, ts), (xev, xdata, xtclk)) in enumerate(zip(collected, expected)):
        has_data = bool(flags & FLAG_HAS_DATA)
        is_tclk = bool(flags & FLAG_IS_TCLK)
        assert ev == xev, f"#{i} event 0x{ev:04X} != 0x{xev:04X}"
        assert is_tclk == bool(xtclk), f"#{i} is_tclk {is_tclk} != {bool(xtclk)}"
        assert has_data == (xdata is not None), f"#{i} has_data {has_data} wrong"
        if xdata is not None:
            assert data == xdata, f"#{i} data 0x{data:016X} != 0x{xdata:016X}"
        assert ts > last_ts, f"#{i} timestamp {ts} not increasing"
        last_ts = ts

    err_count = await axi_read(dut, ERROR_COUNT)
    assert (await axi_read(dut, EVENT_COUNT)) == len(expected)
    assert err_count - base_err == 0, f"ERROR_COUNT rose by {err_count - base_err} on clean frames"
    dut._log.info(f"full chain OK: {len(collected)} mixed frames decoded + read over AXI")
