"""Cocotb 2.0 runner for the aclk_lite_bridge -> aclk_lite_encoder -> clk_rcv chain.

Sources compiled:
  rtl/synchronizer.sv
  rtl/async_fifo.sv
  rtl/aclk_lite_bridge.v
  rtl/aclk_lite/aclk_lite_encoder.sv
  rtl/aclk_bridge/serdec4_9MHz.v
  rtl/aclk_lite/clk_byte_framer.sv
  rtl/aclk_lite/clk_rcv.sv
  tb/aclk_lite_bridge/tb_aclk_lite_bridge_top.sv
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "aclk_lite_bridge"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))   # tb/tclk_tx_model.py, tb/clk_tx_model.py

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_lite_bridge():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "async_fifo.sv",
            RTL_DIR / "aclk_lite_bridge.v",
            RTL_DIR / "aclk_lite" / "aclk_lite_encoder.sv",
            RTL_DIR / "aclk_bridge" / "serdec4_9MHz.v",
            RTL_DIR / "aclk_lite" / "clk_byte_framer.sv",
            RTL_DIR / "aclk_lite" / "clk_rcv.sv",
            TB_DIR  / "tb_aclk_lite_bridge_top.sv",
        ],
        hdl_toplevel="tb_aclk_lite_bridge_top",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="tb_aclk_lite_bridge_top",
        test_module="test_aclk_lite_bridge",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_aclk_lite_bridge()
