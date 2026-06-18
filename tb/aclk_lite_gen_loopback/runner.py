"""Cocotb 2.0 runner for the ACLK-Lite generator -> decoder loopback. Compiles the
encoder, the hardcoded timeline, the existing decoder + its synchronizer, and the
tb top.
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "aclk_lite_gen_loopback"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_gen_loopback():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "aclk_lite" / "aclk_lite_decoder.sv",
            RTL_DIR / "aclk_lite" / "aclk_lite_encoder.sv",
            RTL_DIR / "aclk_lite" / "aclk_lite_gen_timeline.sv",
            TB_DIR / "tb_aclk_gen_loopback.sv",
        ],
        hdl_toplevel="tb_aclk_gen_loopback",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="tb_aclk_gen_loopback",
        test_module="test_aclk_gen_loopback",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_aclk_gen_loopback()
