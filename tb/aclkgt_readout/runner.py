"""Cocotb 2.0 runner for the aclk_gt readout top testbench.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclkgt_readout():
    run_cocotb(
        "aclkgt_readout",
        sources=[
            "rtl/aclk_bridge/crc8_calc.v",
            "rtl/aclk_bridge/GEARBOX_16_TO_96.v",
            "rtl/aclk_bridge/ACLK_REV.v",
            "rtl/synchronizer.sv",
            "rtl/async_fifo.sv",
            "rtl/cdc_gray_count.sv",
            "rtl/aclk_readout/aclk_readout_core.sv",
            "rtl/aclk_readout/aclk_readout_axi.sv",
            "rtl/aclk_gt/aclk_gt_readout_top.sv",
            "tb/aclkgt_readout/tb_aclkgt_readout_top.sv",
        ],
        hdl_toplevel="tb_aclkgt_readout_top",
    )


if __name__ == "__main__":
    test_aclkgt_readout()
