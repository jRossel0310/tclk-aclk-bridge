"""Cocotb 2.0 Python runner for the uart_transmitter testbench.

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
BUILD    = PROJ_DIR / "sim_build" / "uart_transmitter"

sys.path.insert(0, str(TB_DIR))

# Best-effort: honor OSS_CAD_SUITE if set; otherwise rely on the tools already
# being on PATH (the sim.sh / sim.ps1 wrappers put them there for you).
_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_uart_transmitter():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[RTL_DIR / "uart_transmitter.sv"],
        hdl_toplevel="uart_transmitter",
        build_dir=BUILD,
        build_args=build_args,
        # 10 clk cycles per symbol so the test is fast. The SYMBOL constant in
        # test_uart_transmitter.py must equal CLOCK_FREQ // BAUD_RATE.
        parameters={"CLOCK_FREQ": 100, "BAUD_RATE": 10},
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="uart_transmitter",
        test_module="test_uart_transmitter",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_uart_transmitter()
