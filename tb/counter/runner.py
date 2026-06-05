"""Cocotb 2.0 Python runner for the counter smoke test.

Why the Python runner (not a Makefile)?
  - No `make` dependency — important on Windows, where make isn't standard.
  - It's the direction cocotb is steering for 2.0; pure-Python and portable.
  - Simulator choice is a normal variable, so switching is genuinely one line.

Switch simulators by changing SIM below, or from the shell:
    $env:SIM = "verilator"      # PowerShell
    set SIM=verilator           # cmd
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

# ===== flip this ONE variable to change simulator ========================
SIM = os.getenv("SIM", "icarus")        # "icarus" (default) or "verilator"
# =========================================================================

# Paths are resolved relative to this file, so the runner works from any cwd.
TB_DIR   = Path(__file__).resolve().parent          # tb/counter
PROJ_DIR = TB_DIR.parents[1]                         # repo root (fpga-sim)
RTL_DIR  = PROJ_DIR / "rtl"
BUILD    = PROJ_DIR / "sim_build" / "counter"

# Make this test module importable by the spawned simulator process. The
# runner propagates the current sys.path as PYTHONPATH to the simulator.
sys.path.insert(0, str(TB_DIR))

# Best-effort: honor OSS_CAD_SUITE if set; otherwise rely on the tools already
# being on PATH (the sim.sh / sim.ps1 wrappers put them there for you).
_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_counter():
    runner = get_runner(SIM)

    # Verilator traces to FST only when asked; these args are ignored by Icarus.
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []

    runner.build(
        sources=[RTL_DIR / "counter.sv"],
        hdl_toplevel="counter",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,          # emit a waveform (FST for Icarus via cocotb)
        always=True,         # always rebuild — fine for a tiny smoke test
    )

    runner.test(
        hdl_toplevel="counter",
        test_module="test_counter",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test_counter()
