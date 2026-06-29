"""Cocotb 2.0 runner for the full pure-RTL TCLK->ACLK pipeline chain sim.

Sources: every RTL module the chain instantiates (no GT, no BRAM IP).
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "aclk_pipeline_chain"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_pipeline_chain():
    runner = get_runner(SIM)
    runner.build(
        sources=[
            # Shared primitives
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "async_fifo.sv",
            RTL_DIR / "cdc_gray_count.sv",

            # Shared timebase
            RTL_DIR / "global_timebase.v",

            # TCLK_RCV (biphase-mark receiver)
            RTL_DIR / "aclk_bridge" / "serdec4_9MHz.v",
            RTL_DIR / "aclk_bridge" / "TCLK_DESERIALIZER2.v",
            RTL_DIR / "aclk_bridge" / "TCLK_RCV.v",

            # Readout AXI blocks
            RTL_DIR / "aclk_readout" / "aclk_readout_core.sv",
            RTL_DIR / "aclk_readout" / "aclk_readout_axi.sv",

            # TCLK readout top (readout #1)
            RTL_DIR / "aclk_lite" / "tclk_readout_top.sv",

            # ACLK TX encoder path
            RTL_DIR / "aclk_bridge" / "crc8_calc.v",
            RTL_DIR / "aclk_bridge" / "gearbox_96_to_16.v",
            RTL_DIR / "aclk_gt" / "aclk_tclk_encoder.v",

            # ACLK RX decoder path (ACLK_RCV = ACLK_REV)
            RTL_DIR / "aclk_bridge" / "GEARBOX_16_TO_96.v",
            RTL_DIR / "aclk_bridge" / "ACLK_REV.v",

            # ACLK GT readout top (readout #2)
            RTL_DIR / "aclk_gt" / "aclk_gt_readout_top.sv",

            # Testbench top
            TB_DIR / "tb_aclk_pipeline_chain_top.sv",
        ],
        hdl_toplevel="tb_aclk_pipeline_chain_top",
        build_dir=BUILD,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="tb_aclk_pipeline_chain_top",
        test_module="test_aclk_pipeline_chain",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_aclk_pipeline_chain()
