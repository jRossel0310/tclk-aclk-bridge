"""Full-chain sim for aclk_gt_readout_top: drive ACLK_RCV's 16-bit + K xcvr
interface with the gigabit-ACLK TX model, read decoded events back over AXI-Lite,
and check event/data/flags/timestamps/counts plus the null-drop and bad-CRC paths.
The GT transceiver is NOT in this DUT (it has no Icarus model); the tb feeds the
xcvr word stream the GT would otherwise recover."""

import sys
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # shared tb/*.py
from aclk_tx_model import stream_frames                                      # noqa: E402
from axi_lite_bfm import axi_read, axi_write, _b                            # noqa: E402
from plot_util import save_fifo_plot                                         # noqa: E402

RX_PERIOD_NS, AXI_PERIOD_NS = 16, 10
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP = 0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60
EVENT_COUNT, NULL_COUNT, ERROR_COUNT = 0x70, 0x80, 0x90
MASK64 = (1 << 64) - 1
NULL_EVENT = (0xFFFF, MASK64)
FLAG_HAS_DATA, FLAG_IS_TCLK = 0x1, 0x2


async def _reset(dut):
    dut.DATA_FROM_XCVR.value = 0
    dut.K_FROM_XCVR.value = 0
    dut.rx_rstn.value = 0
    dut.s_axi_aresetn.value = 0
    dut.pps.value = 0
    dut.mmcm_locked.value = 1
    for s in ("s_axi_arvalid", "s_axi_rready", "s_axi_awvalid", "s_axi_wvalid", "s_axi_bready"):
        getattr(dut, s).value = 0
    await ClockCycles(dut.CLK1, 5)
    await ClockCycles(dut.s_axi_aclk, 5)
    await Timer(1, unit="ns")
    dut.rx_rstn.value = 1
    dut.s_axi_aresetn.value = 1
    await RisingEdge(dut.CLK1)


async def _idle_carrier(dut, stop):
    while not stop.get("done"):
        await stream_frames(dut, [NULL_EVENT], repeat=1)


async def axi_read_event(dut):
    ev_reg = await axi_read(dut, EVENT)
    event, flags = ev_reg & 0xFFFF, (ev_reg >> 16) & 0xFFFF
    dhi, dlo = await axi_read(dut, DATA_HI), await axi_read(dut, DATA_LO)
    thi, tlo = await axi_read(dut, TS_HI), await axi_read(dut, TS_LO)
    await axi_write(dut, POP)
    return event, flags, (dhi << 32) | dlo, (thi << 32) | tlo


async def _rx_monitor(dut, acct):
    while not acct.get("rx_done"):
        await RisingEdge(dut.CLK1)
        await Timer(1, unit="ns")
        if _b(dut.dropped_null) == 1:
            acct["nulls"] += 1
        if _b(dut.aclk_valid) == 1:
            acct["w"] += 1


async def _sampler(dut, acct, series, interval_ns=80):
    t = 0
    while not acct.get("done"):
        await Timer(interval_ns, unit="ns")
        t += interval_ns
        series.append((t, acct["w"], acct["read"]))


@cocotb.test()
async def test_gt_readout_chain(dut):
    cocotb.start_soon(Clock(dut.CLK1, RX_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, AXI_PERIOD_NS, unit="ns").start())
    await _reset(dut)

    real = [
        (0x0001, 0x1111222233334444),
        (0x00A5, 0xAAAABBBBCCCCDDDD),
        (0x1000, 0x0123456789ABCDEF),
        (0x3C00, 0xFEDCBA9876543210),
    ]
    seq = [real[0], NULL_EVENT, real[1], real[2], NULL_EVENT, real[3]]
    nonnull = [f for f in seq if (f[0] & 0xFF) != 0xFF]

    acct = {"w": 0, "nulls": 0, "read": 0}
    series = []
    cocotb.start_soon(_rx_monitor(dut, acct))
    cocotb.start_soon(_sampler(dut, acct, series))

    await stream_frames(dut, seq, repeat=10)
    stop = {"done": False}
    carrier = cocotb.start_soon(_idle_carrier(dut, stop))
    await ClockCycles(dut.CLK1, 8)
    await ClockCycles(dut.s_axi_aclk, 6)

    assert int(dut.rx_aligned.value) == 1, "RX never aligned"
    assert ((await axi_read(dut, STATUS)) >> 1) & 1 == 0, "overflow: events dropped"

    collected = []
    while True:
        if (await axi_read(dut, STATUS)) & 0x1:
            break
        collected.append(await axi_read_event(dut))
        acct["read"] = len(collected)

    acct["done"] = True
    acct["rx_done"] = True
    stop["done"] = True

    assert collected, "no events read over AXI"
    ed = [(ev, da) for (ev, fl, da, ts) in collected]
    for ev, fl, da, ts in collected:
        assert (ev & 0xFF) != 0xFF, f"null leaked: 0x{ev:04X}"
        assert fl & FLAG_HAS_DATA, f"has_data not set for 0x{ev:04X}"
        assert not (fl & FLAG_IS_TCLK), f"is_tclk wrongly set for 0x{ev:04X}"
    start = nonnull.index(ed[0])
    for i, got in enumerate(ed):
        exp = nonnull[(start + i) % len(nonnull)]
        assert got == exp, f"order/data break #{i}: {got} != {exp}"
    tss = [ts for (ev, fl, da, ts) in collected]
    for i in range(1, len(tss)):
        assert tss[i] > tss[i - 1], f"timestamp not monotonic at #{i}"

    assert await axi_read(dut, EVENT_COUNT) == len(collected), "EVENT_COUNT mismatch"
    assert await axi_read(dut, NULL_COUNT) > 0, "NULL_COUNT did not register dropped nulls"
    assert await axi_read(dut, ERROR_COUNT) == 0, "ERROR_COUNT on a clean stream"

    # Write the FIFO-occupancy plot using the shared helper.
    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "aclkgt_readout" / "plots"
    plot_path = save_fifo_plot(
        series,
        n_events=len(collected),
        title="GT readout: events buffered then read over AXI",
        out_path=out_dir / "gt_readout.png",
    )
    if plot_path:
        dut._log.info(f"GT readout plot written to {plot_path}")

    dut._log.info(f"gt readout OK: {len(collected)} events in order, flags+ts correct")
