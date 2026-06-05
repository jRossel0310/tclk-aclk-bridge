#!/usr/bin/env bash
#
# Task wrapper for the SystemVerilog sim skeleton — git bash / MSYS equivalent
# of sim.ps1. Handles venv + tool PATH for you, so you do NOT activate first.
#
# Usage:
#   ./sim.sh setup                    # create .venv + install requirements (run once)
#   ./sim.sh run                      # build + simulate the default module (Icarus)
#   ./sim.sh run -m counter -s verilator
#   ./sim.sh wave                     # open the latest waveform in GTKWave
#   ./sim.sh test                     # run, then open the waveform
#   ./sim.sh new <name>               # scaffold rtl/<name>.sv + tb/<name>/
#   ./sim.sh clean                    # delete sim_build/
#   ./sim.sh list                     # list testbench modules
#   ./sim.sh help
#
set -euo pipefail

# --- locate ourselves (works from any cwd) -------------------------------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- parse args ----------------------------------------------------------
TASK="${1:-run}"; shift || true
MODULE="counter"
SIM="icarus"
POS=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--module) MODULE="$2"; shift 2 ;;
    -s|--sim)    SIM="$2";    shift 2 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *)  POS="$1"; shift ;;
  esac
done

FST="$ROOT/sim_build/$MODULE/$MODULE.fst"

die() { echo "error: $*" >&2; exit 1; }

# Path to the venv's Python (Windows venv → Scripts/, POSIX venv → bin/).
venv_py() {
  if [[ -f "$ROOT/.venv/Scripts/python.exe" ]]; then echo "$ROOT/.venv/Scripts/python.exe"
  else echo "$ROOT/.venv/bin/python"; fi
}

# Resolve the OSS CAD Suite and put its bin+lib on PATH for this process.
# Resolution order: OSS_CAD_SUITE env var -> already on PATH. No machine-specific
# default, so this is portable across machines. Sets OSS_ROOT ("" if not found).
OSS_ROOT=""
resolve_oss() {
  if [[ -n "${OSS_CAD_SUITE:-}" && -d "$OSS_CAD_SUITE/bin" ]]; then
    OSS_ROOT="$(cd "$OSS_CAD_SUITE" && pwd)"
  elif command -v iverilog >/dev/null 2>&1; then
    # Derive the suite root from the tool location (bin/iverilog -> ..).
    OSS_ROOT="$(cd "$(dirname "$(command -v iverilog)")/.." && pwd)"
  fi
  if [[ -n "$OSS_ROOT" ]]; then
    # Both bin AND lib are needed: iverilog/vvp load DLLs from lib at runtime.
    export PATH="$OSS_ROOT/bin:$OSS_ROOT/lib:$PATH"
  fi
}

require_tools() {
  resolve_oss
  command -v iverilog >/dev/null 2>&1 || die \
"HDL tools not found. Install the OSS CAD Suite and either add its bin/ to PATH,
   or set OSS_CAD_SUITE to its root, e.g.:
       export OSS_CAD_SUITE=/c/Users/<you>/tools/oss-cad-suite"
}

do_setup() {
  command -v python >/dev/null 2>&1 || die "python not found on PATH (install Python 3.12+)"
  if [[ ! -f "$(venv_py)" ]]; then
    echo "==> creating venv at .venv"
    python -m venv "$ROOT/.venv"
  fi
  local py; py="$(venv_py)"
  echo "==> installing requirements"
  "$py" -m pip install --upgrade pip >/dev/null
  "$py" -m pip install -r "$ROOT/requirements.txt"
  echo "==> done. Next: ./sim.sh run"
}

run_sim() {
  local tb="$ROOT/tb/$MODULE"
  [[ -f "$tb/runner.py" ]] || die "No runner found at tb/$MODULE/runner.py"
  local py; py="$(venv_py)"
  [[ -f "$py" ]] || die "venv missing - run: ./sim.sh setup"
  require_tools
  export SIM="$SIM"
  echo "==> simulate '$MODULE' with $SIM"
  ( cd "$tb" && "$py" runner.py )
}

# GTKWave is a native GTK app and needs the OSS CAD Suite's GTK runtime env
# (pixbuf loaders + prefixes) that environment.bat normally sets. Without it,
# gtkwave bails out: "Unable to load image-loading module ...libpixbufloader-svg.dll".
# Paths must be Windows-form (cygpath -w) since gtkwave.exe is a native binary.
setup_gtk_env() {
  [[ -n "$OSS_ROOT" ]] || return 0
  local oss_w; oss_w="$(cygpath -w "$OSS_ROOT")"
  export GTK_EXE_PREFIX="$oss_w"
  export GTK_DATA_PREFIX="$oss_w"
  export GDK_PIXBUF_MODULEDIR="$(cygpath -w "$OSS_ROOT/lib/gdk-pixbuf-2.0/2.10.0/loaders")"
  export GDK_PIXBUF_MODULE_FILE="$(cygpath -w "$OSS_ROOT/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache")"
  # Rebuild the loader cache so it only lists DLLs actually present (the shipped
  # cache references an svg loader that isn't on disk). Idempotent + quick.
  if command -v gdk-pixbuf-query-loaders.exe >/dev/null 2>&1; then
    gdk-pixbuf-query-loaders.exe --update-cache 2>/dev/null || true
  fi
}

open_wave() {
  [[ -f "$FST" ]] || die "No waveform at $FST - run a sim first."
  require_tools
  setup_gtk_env
  echo "==> opening $FST"
  # Background it so the terminal isn't blocked while GTKWave is open.
  gtkwave "$FST" &
}

new_module() {
  local name="$1"
  [[ -n "$name" ]] || die "usage: ./sim.sh new <module_name>"
  [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "invalid name '$name' (use a valid SV identifier)"
  local rtl="$ROOT/rtl/$name.sv"
  local tbdir="$ROOT/tb/$name"
  [[ -e "$rtl" ]]   && die "rtl/$name.sv already exists"
  [[ -e "$tbdir" ]] && die "tb/$name already exists"
  mkdir -p "$tbdir"

  # --- rtl stub (resets q to 0; the generated test asserts that) ---------
  cat > "$rtl" <<'EOF'
// rtl/__MOD__.sv
//
// TODO: describe what __MOD__ does. This is a scaffold — the generated
// testbench only checks that q resets to 0, so it passes out of the box.

`timescale 1ns / 1ps

module __MOD__ #(
    parameter int WIDTH = 8
) (
    input  logic             clk,
    input  logic             rst,    // synchronous, active-high
    output logic [WIDTH-1:0] q
);

    always_ff @(posedge clk) begin
        if (rst)
            q <= '0;
        // TODO: add your logic here
    end

endmodule
EOF

  # --- runner.py ---------------------------------------------------------
  cat > "$tbdir/runner.py" <<'EOF'
"""Cocotb 2.0 Python runner for the __MOD__ testbench.

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
BUILD    = PROJ_DIR / "sim_build" / "__MOD__"

sys.path.insert(0, str(TB_DIR))

# Best-effort: honor OSS_CAD_SUITE if set; otherwise rely on the tools already
# being on PATH (the sim.sh / sim.ps1 wrappers put them there for you).
_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test___MOD__():
    runner = get_runner(SIM)
    build_args = ["--trace-fst", "--trace-structs"] if SIM == "verilator" else []
    runner.build(
        sources=[RTL_DIR / "__MOD__.sv"],
        hdl_toplevel="__MOD__",
        build_dir=BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=True,
        always=True,
    )
    runner.test(
        hdl_toplevel="__MOD__",
        test_module="test___MOD__",
        build_dir=BUILD,
        waves=True,
    )


if __name__ == "__main__":
    test___MOD__()
EOF

  # --- test_<name>.py ----------------------------------------------------
  cat > "$tbdir/test___MOD__.py" <<'EOF'
"""Cocotb smoke test for rtl/__MOD__.sv. Replace with real tests."""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

CLK_PERIOD_NS = 10


def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())


async def reset_dut(dut, cycles: int = 2):
    dut.rst.value = 1
    await ClockCycles(dut.clk, cycles)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


@cocotb.test()
async def test___MOD___smoke(dut):
    """After reset, q is 0. Build out real checks from here."""
    start_clock(dut)
    await reset_dut(dut)
    await Timer(1, unit="ns")            # step past the <= update region
    assert int(dut.q.value) == 0, f"after reset expected q=0, got {int(dut.q.value)}"
    dut._log.info("reset OK: q == 0")
EOF

  # Substitute the module name into all three files.
  sed -i "s/__MOD__/$name/g" "$rtl" "$tbdir/runner.py" "$tbdir/test___MOD__.py"
  mv "$tbdir/test___MOD__.py" "$tbdir/test_$name.py"

  echo "scaffolded:"
  echo "  rtl/$name.sv"
  echo "  tb/$name/runner.py"
  echo "  tb/$name/test_$name.py"
  echo "run it with: ./sim.sh run -m $name"
}

case "$TASK" in
  setup) do_setup ;;
  run)   run_sim ;;
  wave)  open_wave ;;
  test)  run_sim; open_wave ;;
  new)   new_module "$POS" ;;
  clean)
    if [[ -d "$ROOT/sim_build" ]]; then
      rm -rf "$ROOT/sim_build"; echo "removed sim_build/"
    else
      echo "nothing to clean"
    fi
    ;;
  list)
    for d in "$ROOT"/tb/*/; do
      [[ -f "$d/runner.py" ]] && echo " - $(basename "$d")"
    done
    ;;
  help|-h|--help)
    sed -n '3,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    ;;
  *) die "unknown task '$TASK' (try: setup run wave test new clean list help)" ;;
esac
