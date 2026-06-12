"""Cocotb 2.0 Python runner for the ACLK-Lite Manchester decoder (ADM).

aclk_lite_decoder (rtl/aclk_lite/aclk_lite_decoder.sv) recovers a Manchester
ACLK-Lite stream into length-aware events. It reuses rtl/synchronizer.sv to bring
the async line into the oversampling clock domain, so both are compiled here.

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
BUILD    = PROJ_DIR / "sim_build" / "aclk_lite_decoder"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))    # for the shared tb/manchester_tx_model.py

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_lite_decoder():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "aclk_lite" / "aclk_lite_decoder.sv",
        ],
        hdl_toplevel="aclk_lite_decoder",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="aclk_lite_decoder",
        test_module="test_aclk_lite_decoder",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_aclk_lite_decoder()
