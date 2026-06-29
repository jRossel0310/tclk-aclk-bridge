"""Cocotb 2.0 runner for rtl/global_timebase.v (shared timestamp).
Sources: rtl/synchronizer.sv, rtl/cdc_gray_count.sv, rtl/global_timebase.v.
"""
import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "global_timebase"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_global_timebase():
    runner = get_runner(SIM)
    runner.build(
        sources=[
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "cdc_gray_count.sv",
            RTL_DIR / "global_timebase.v",
        ],
        hdl_toplevel="global_timebase",
        build_dir=BUILD,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="global_timebase",
        test_module="test_global_timebase",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_global_timebase()
