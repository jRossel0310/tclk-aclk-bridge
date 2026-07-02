"""Cocotb 2.0 Python runner for the ACLK readout datapath testbench.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_readout():
    run_cocotb(
        "aclk_readout",
        sources=[
            "rtl/aclk_bridge/crc8_calc.v",
            "rtl/aclk_bridge/GEARBOX_16_TO_96.v",
            "rtl/aclk_bridge/ACLK_REV.v",
            "rtl/synchronizer.sv",
            "rtl/async_fifo.sv",
            "rtl/aclk_readout/aclk_readout_core.sv",
            "tb/aclk_readout/tb_aclk_readout_top.sv",
        ],
        hdl_toplevel="tb_aclk_readout_top",
    )


if __name__ == "__main__":
    test_aclk_readout()
