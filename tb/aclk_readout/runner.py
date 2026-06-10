"""Cocotb 2.0 Python runner for the ACLK readout datapath testbench.

The DUT (tb_aclk_readout_top) wires the real decoder (ACLK_RCV and its children
GEARBOX_16_TO_96 + CRC8_CALC) to aclk_readout_core, which packs each good,
non-null event and pushes it through the dual-clock async_fifo (which in turn
uses synchronizer.sv). All sources are compiled here.

The receive side is driven by the shared TX model in tb/aclk_tx_model.py, so the
tb directory's parent is added to sys.path.

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
BUILD    = PROJ_DIR / "sim_build" / "aclk_readout"

sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))    # for the shared tb/aclk_tx_model.py

# Best-effort: honor OSS_CAD_SUITE if set; otherwise rely on the tools already
# being on PATH (the sim.sh / sim.ps1 wrappers put them there for you).
_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_readout():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[
            RTL_DIR / "aclk_bridge" / "crc8_calc.v",
            RTL_DIR / "aclk_bridge" / "GEARBOX_16_TO_96.v",
            RTL_DIR / "aclk_bridge" / "ACLK_REV.v",
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "async_fifo.sv",
            RTL_DIR / "aclk_readout" / "aclk_readout_core.sv",
            TB_DIR  / "tb_aclk_readout_top.sv",
        ],
        hdl_toplevel="tb_aclk_readout_top",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="tb_aclk_readout_top",
        test_module="test_aclk_readout",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_aclk_readout()
