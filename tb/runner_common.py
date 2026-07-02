"""Shared cocotb 2.0 runner factory.

One place for what every tb/<name>/runner.py used to duplicate: the SIM env var,
the OSS_CAD_SUITE PATH setup, the sys.path/PYTHONPATH wiring, the build dir layout,
and the build+test call. A runner reduces to:

    from runner_common import run_cocotb
    run_cocotb("<name>", sources=["rtl/x.sv", ...], hdl_toplevel="x")

Why a Python runner (not a Makefile)?
  - No `make` dependency, which matters on Windows.
  - It is the direction cocotb is steering for 2.0; pure Python and portable.

Switch simulators from the shell:
    $env:SIM = "verilator"      # PowerShell
    export SIM=verilator        # bash
"""
import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

TB_ROOT  = Path(__file__).resolve().parent       # tb/
PROJ_DIR = TB_ROOT.parent                        # repo root


def run_cocotb(name, sources, hdl_toplevel, parameters=None, test_module=None):
    """Build + run one testbench.

    name          tb/<name>/ suite dir; the build lands in sim_build/<name>/
    sources       HDL paths RELATIVE TO THE REPO ROOT, e.g. "rtl/async_fifo.sv"
                  or "tb/<name>/tb_x_top.sv"
    hdl_toplevel  the top module compiled for the sim
    parameters    optional dict of HDL parameters
    test_module   cocotb test module (default: test_<name>)
    """
    sim = os.getenv("SIM", "icarus")
    tb_dir = TB_ROOT / name
    build = PROJ_DIR / "sim_build" / name

    # The runner propagates sys.path to the simulator process as PYTHONPATH: the
    # suite dir for test_<name>.py, tb/ for the shared models + cocotb_helpers.
    for p in (str(tb_dir), str(TB_ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)

    # Best-effort: honor OSS_CAD_SUITE if set; otherwise rely on the tools already
    # being on PATH (the sim.sh / sim.ps1 wrappers put them there for you).
    _oss = os.getenv("OSS_CAD_SUITE")
    if _oss and (Path(_oss) / "bin").is_dir():
        os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")

    runner = get_runner(sim)
    # Verilator traces to FST only when asked; these args are ignored by Icarus.
    build_args = ["--trace-fst", "--trace-structs"] if sim == "verilator" else []
    runner.build(
        sources=[PROJ_DIR / s for s in sources],
        hdl_toplevel=hdl_toplevel,
        build_dir=build,
        build_args=build_args,
        parameters=parameters or {},
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel=hdl_toplevel,
        test_module=test_module or f"test_{name}",
        build_dir=build,
        waves=True,
    )
