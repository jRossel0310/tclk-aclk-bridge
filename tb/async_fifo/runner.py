"""Cocotb 2.0 Python runner for the async_fifo testbench.

async_fifo (rtl/async_fifo.sv) is the dual-clock FIFO that carries decoded ACLK
events from the recovered RX clock domain (where ACLK_RCV runs) into the PS
facing AXI clock domain. It instantiates the project CDC primitive
rtl/synchronizer.sv for its Gray-coded pointer crossings, so both sources are
compiled here. Both are SystemVerilog but use only constructs Icarus supports.

Switch simulators by changing SIM below, or from the shell:
    $env:SIM = "verilator"      # PowerShell
    export SIM=verilator        # bash
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

# ===== flip this ONE variable to change simulator ========================
SIM = os.getenv("SIM", "icarus")        # "icarus" (default) or "verilator"
# =========================================================================

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "async_fifo"

sys.path.insert(0, str(TB_DIR))

# Best-effort: honor OSS_CAD_SUITE if set; otherwise rely on the tools already
# being on PATH (the sim.sh / sim.ps1 wrappers put them there for you).
_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_async_fifo():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "async_fifo.sv",
        ],
        hdl_toplevel="async_fifo",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="async_fifo",
        test_module="test_async_fifo",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_async_fifo()
