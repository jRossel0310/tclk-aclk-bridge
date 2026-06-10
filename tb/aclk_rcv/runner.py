"""Cocotb 2.0 Python runner for the ACLK_RCV decoder testbench.

ACLK_RCV (rtl/aclk_bridge/ACLK_REV.v) is the receive/decode end of the ACLK-Lite
link: it turns the 16-bit + K-flag transceiver word stream into ACLK_EVENT[15:0]
+ ACLK_DATA[63:0] + an ACLK_VALID strobe. It is a multi-file design, the runner
compiles its two children (GEARBOX_16_TO_96, CRC8_CALC) along with it. All three
are plain Verilog with no vendor primitives, so they simulate under Icarus.

Pinned to Icarus. Verilator is unverified for this DUT: GEARBOX_16_TO_96 leaves
its k_a register intentionally unreset, so Icarus (X warm-up) and Verilator
(2-state 0 init) can differ in the pre-alignment cycles. Output correctness is
unaffected (the decoder only trusts CRC==0), but we don't claim Verilator works.

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
BUILD    = PROJ_DIR / "sim_build" / "aclk_rcv"

sys.path.insert(0, str(TB_DIR))

# Best-effort: honor OSS_CAD_SUITE if set; otherwise rely on the tools already
# being on PATH (the sim.sh / sim.ps1 wrappers put them there for you).
_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_rcv():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        # Note the aclk_bridge/ subdir, these sources don't live flat in rtl/.
        sources=[
            RTL_DIR / "aclk_bridge" / "crc8_calc.v",
            RTL_DIR / "aclk_bridge" / "GEARBOX_16_TO_96.v",
            RTL_DIR / "aclk_bridge" / "ACLK_REV.v",
        ],
        hdl_toplevel="ACLK_RCV",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="ACLK_RCV",
        test_module="test_aclk_rcv",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_aclk_rcv()
