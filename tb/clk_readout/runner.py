"""Cocotb 2.0 runner for the full unified PL chain rtl/aclk_lite/clk_readout_top:
serdec + clk_byte_framer -> shared readout -> AXI4-Lite. Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_clk_readout():
    run_cocotb(
        "clk_readout",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/async_fifo.sv",
            "rtl/cdc_gray_count.sv",
            "rtl/aclk_readout/aclk_readout_core.sv",
            "rtl/aclk_readout/aclk_readout_axi.sv",
            "rtl/aclk_bridge/serdec4_9MHz.v",
            "rtl/aclk_lite/clk_byte_framer.sv",
            "rtl/aclk_lite/clk_rcv.sv",
            "rtl/aclk_lite/clk_readout_top.sv",
        ],
        hdl_toplevel="clk_readout_top",
    )


if __name__ == "__main__":
    test_clk_readout()
