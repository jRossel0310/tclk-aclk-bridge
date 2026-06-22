"""Cocotb 2.0 runner for the unified ACLK/TCLK decoder rtl/aclk_lite/clk_rcv
(serdec4_9MHz + clk_byte_framer). Plain Verilog/SV -> Icarus. The line is driven by
the real-framing model in tb/clk_tx_model.py."""
import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "clk_rcv"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))    # shared tb/clk_tx_model.py + tb/tclk_tx_model.py

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_clk_rcv():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "aclk_bridge" / "serdec4_9MHz.v",
            RTL_DIR / "aclk_lite" / "clk_byte_framer.sv",
            RTL_DIR / "aclk_lite" / "clk_rcv.sv",
        ],
        hdl_toplevel="clk_rcv",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(hdl_toplevel="clk_rcv", test_module="test_clk_rcv", build_dir=BUILD, waves=True)


if __name__ == "__main__":
    test_clk_rcv()
