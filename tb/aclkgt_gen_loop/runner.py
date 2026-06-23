"""Cocotb 2.0 runner for the aclk_gt_frame_gen -> ACLK_RCV loopback sim.

Sources: crc8_calc.v, gearbox_96_to_16.v, GEARBOX_16_TO_96.v, ACLK_REV.v,
         aclk_gt_frame_gen.v, and the tb top.
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "aclkgt_gen_loop"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclkgt_gen_loop():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "aclk_bridge" / "crc8_calc.v",
            RTL_DIR / "aclk_bridge" / "gearbox_96_to_16.v",
            RTL_DIR / "aclk_bridge" / "GEARBOX_16_TO_96.v",
            RTL_DIR / "aclk_bridge" / "ACLK_REV.v",
            RTL_DIR / "aclk_gt" / "aclk_gt_frame_gen.v",
            TB_DIR / "tb_aclkgt_gen_loop_top.sv",
        ],
        hdl_toplevel="tb_aclkgt_gen_loop_top",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="tb_aclkgt_gen_loop_top",
        test_module="test_aclkgt_gen_loop",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_aclkgt_gen_loop()
