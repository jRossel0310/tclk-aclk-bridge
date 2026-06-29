"""With USE_EXT_TS=1, the TS_HI/TS_LO register must reflect the driven ts_ext
value captured at the event's VALID cycle, not an internal counter."""
import sys
import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from axi_lite_bfm import axi_read  # noqa: E402

STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP = 0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60


@cocotb.test()
async def test_ext_ts_in_register(dut):
    cocotb.start_soon(Clock(dut.rx_clk, 16, unit="ns").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, 10, unit="ns").start())
    dut.rx_rstn.value = 0
    dut.s_axi_aresetn.value = 0
    dut.aclk_valid.value = 0
    dut.ts_ext.value = 0
    dut.aclk_event.value = 0
    dut.aclk_data.value = 0
    dut.flags.value = 0
    dut.s_axi_awaddr.value = 0
    dut.s_axi_awvalid.value = 0
    dut.s_axi_wdata.value = 0
    dut.s_axi_wstrb.value = 0
    dut.s_axi_wvalid.value = 0
    dut.s_axi_bready.value = 0
    dut.s_axi_araddr.value = 0
    dut.s_axi_arvalid.value = 0
    dut.s_axi_rready.value = 0
    await ClockCycles(dut.s_axi_aclk, 5)
    await Timer(1, unit="ns")
    dut.rx_rstn.value = 1
    dut.s_axi_aresetn.value = 1
    await ClockCycles(dut.rx_clk, 5)

    # drive a known ts_ext and a one-cycle event at that instant
    await RisingEdge(dut.rx_clk)
    dut.ts_ext.value = 0x00000000_DEADBEEF
    dut.aclk_event.value = 0x0042
    dut.aclk_data.value = 0
    dut.flags.value = 0x0002
    dut.aclk_valid.value = 1
    await RisingEdge(dut.rx_clk)
    dut.aclk_valid.value = 0
    await ClockCycles(dut.s_axi_aclk, 20)

    assert (await axi_read(dut, STATUS)) & 0x1 == 0, "FIFO unexpectedly empty"
    ts = (await axi_read(dut, TS_HI) << 32) | (await axi_read(dut, TS_LO))
    assert ts == 0xDEADBEEF, f"TS={ts:#x} did not capture ts_ext"
    dut._log.info("ext ts captured into TS register")

    _save_plot(0xDEADBEEF, ts)


def _save_plot(expected_ts, captured_ts):
    """Emit a minimal plot showing the expected vs captured TS value."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return

    fig, ax = plt.subplots(figsize=(6, 3))
    labels = ["ts_ext driven", "TS register captured"]
    values = [expected_ts, captured_ts]
    colors = ["tab:blue", "tab:green"]
    bars = ax.barh(labels, values, color=colors)
    ax.bar_label(bars, labels=[f"0x{v:08X}" for v in values], padding=4)
    ax.set_xlabel("Timestamp value (hex)")
    ax.set_title("External timestamp capture: USE_EXT_TS=1")
    ax.set_xlim(0, max(values) * 1.3)

    out_dir = Path(__file__).resolve().parents[2] / "sim_build" / "aclk_readout_ext_ts" / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "ext_ts_capture.png"
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
