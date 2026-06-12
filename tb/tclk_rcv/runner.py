"""Cocotb 2.0 Python runner for the inherited TCLK receiver (rtl/aclk_bridge):
TCLK_RCV = serdec4_9MHz (biphase bit recovery) + TCLK_DESERIALIZER2 (byte
assembly + parity). All three are plain Verilog, so they simulate under Icarus.

The TCLK line is driven by the biphase-mark model in tb/tclk_tx_model.py, so the
tb parent is added to sys.path.

Switch simulators by changing SIM below, or from the shell:
    $env:SIM = "verilator"      # PowerShell
    export SIM=verilator        # bash
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

# ===== flip this ONE variable to change simulator ========================
SIM = os.getenv("SIM", "icarus")        # "icarus" (default); verilator unverified
# =========================================================================

TB_DIR   = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "tclk_rcv"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))    # for the shared tb/tclk_tx_model.py

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_tclk_rcv():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "aclk_bridge" / "serdec4_9MHz.v",
            RTL_DIR / "aclk_bridge" / "TCLK_DESERIALIZER2.v",
            RTL_DIR / "aclk_bridge" / "TCLK_RCV.v",
        ],
        hdl_toplevel="TCLK_RCV",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="TCLK_RCV",
        test_module="test_tclk_rcv",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_tclk_rcv()
