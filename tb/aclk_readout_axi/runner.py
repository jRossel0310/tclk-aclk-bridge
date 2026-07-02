"""Cocotb 2.0 runner for the AXI-Lite readout testbench: the real decoder
(ACLK_RCV + GEARBOX_16_TO_96 + CRC8_CALC) feeding aclk_readout_axi. Driven by the
shared tb/aclk_tx_model.py. Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_readout_axi():
    run_cocotb(
        "aclk_readout_axi",
        sources=[
            "rtl/aclk_bridge/crc8_calc.v",
            "rtl/aclk_bridge/GEARBOX_16_TO_96.v",
            "rtl/aclk_bridge/ACLK_REV.v",
            "rtl/synchronizer.sv",
            "rtl/async_fifo.sv",
            "rtl/cdc_gray_count.sv",
            "rtl/aclk_readout/aclk_readout_core.sv",
            "rtl/aclk_readout/aclk_readout_axi.sv",
            "tb/aclk_readout_axi/tb_aclk_readout_axi_top.sv",
        ],
        hdl_toplevel="tb_aclk_readout_axi_top",
    )


if __name__ == "__main__":
    test_aclk_readout_axi()
