"""Cocotb 2.0 Python runner for the external-timestamp (USE_EXT_TS=1) test.

Compiles aclk_readout_axi with USE_EXT_TS=1 and drives events directly from
cocotb (no decoder above). Proves that TS_HI/TS_LO reflect ts_ext at capture
time, not the internal counter.
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "aclk_readout_ext_ts"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_readout_ext_ts():
    runner = get_runner(SIM)
    runner.build(
        sources=[
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "async_fifo.sv",
            RTL_DIR / "cdc_gray_count.sv",
            RTL_DIR / "aclk_readout" / "aclk_readout_core.sv",
            RTL_DIR / "aclk_readout" / "aclk_readout_axi.sv",
            TB_DIR  / "tb_ext_ts_top.sv",
        ],
        hdl_toplevel="tb_ext_ts_top",
        build_dir=BUILD,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="tb_ext_ts_top",
        test_module="test_ext_ts",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_aclk_readout_ext_ts()
