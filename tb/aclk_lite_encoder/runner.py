"""Cocotb 2.0 runner for rtl/aclk_lite/aclk_lite_encoder.sv (biphase-mark encoder).
The encoder is a leaf module (no dependencies). The test reuses the shared
tb/clk_tx_model.py / tb/tclk_tx_model.py golden reference.
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "aclk_lite_encoder"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))    # for tb/clk_tx_model.py, tb/tclk_tx_model.py

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_lite_encoder():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[RTL_DIR / "aclk_lite" / "aclk_lite_encoder.sv"],
        hdl_toplevel="aclk_lite_encoder",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="aclk_lite_encoder",
        test_module="test_aclk_lite_encoder",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_aclk_lite_encoder()
