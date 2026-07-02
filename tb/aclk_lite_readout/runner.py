"""Cocotb 2.0 Python runner for the full Manchester PL chain
(aclk_lite_readout_top): Manchester ACLK-Lite decoder -> readout -> AXI4-Lite.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_lite_readout():
    run_cocotb(
        "aclk_lite_readout",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/async_fifo.sv",
            "rtl/cdc_gray_count.sv",
            "rtl/aclk_readout/aclk_readout_core.sv",
            "rtl/aclk_readout/aclk_readout_axi.sv",
            "rtl/aclk_lite/aclk_lite_decoder.sv",
            "rtl/aclk_lite/aclk_lite_readout_top.sv",
        ],
        hdl_toplevel="aclk_lite_readout_top",
    )


if __name__ == "__main__":
    test_aclk_lite_readout()
