"""Cocotb 2.0 Python runner for the full TCLK PL chain (tclk_readout_top): the
inherited biphase-mark receiver (TCLK_RCV = serdec4_9MHz + TCLK_DESERIALIZER2)
feeding the decoder-agnostic AXI4-Lite readout (aclk_readout_axi), end to end.

This is the PL block Phase B synthesizes for the KR260 TCLK input. The TCLK line
is driven by the shared biphase-mark model (tb/tclk_tx_model.py) and events are
read over AXI with the shared BFM (tb/axi_lite_bfm.py).

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
BUILD    = PROJ_DIR / "sim_build" / "tclk_readout"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))    # shared tb/tclk_tx_model.py + tb/axi_lite_bfm.py

_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_tclk_readout():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "async_fifo.sv",
            RTL_DIR / "cdc_gray_count.sv",
            RTL_DIR / "aclk_readout" / "aclk_readout_core.sv",
            RTL_DIR / "aclk_readout" / "aclk_readout_axi.sv",
            RTL_DIR / "aclk_bridge" / "serdec4_9MHz.v",
            RTL_DIR / "aclk_bridge" / "TCLK_DESERIALIZER2.v",
            RTL_DIR / "aclk_bridge" / "TCLK_RCV.v",
            RTL_DIR / "aclk_lite" / "tclk_readout_top.sv",
        ],
        hdl_toplevel="tclk_readout_top",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="tclk_readout_top",
        test_module="test_tclk_readout",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_tclk_readout()
