<#
.SYNOPSIS
  Task wrapper for the SystemVerilog sim skeleton. The Windows-native
  stand-in for a Makefile. (git bash users: use ./sim.sh instead.)

.EXAMPLE
  .\sim.ps1 setup               # create .venv + install requirements (run once)
  .\sim.ps1 run                 # build + simulate the default module (Icarus)
  .\sim.ps1 run -Module counter -Sim verilator
  .\sim.ps1 wave                # open the latest waveform in GTKWave
  .\sim.ps1 test                # run, then open the waveform
  .\sim.ps1 new myfifo          # scaffold rtl/myfifo.sv + tb/myfifo/
  .\sim.ps1 clean               # delete build/sim artifacts
  .\sim.ps1 list                # list testbench modules

.NOTES
  Handles venv + tool PATH for you, so you do NOT need to activate the venv first.
  If running scripts is blocked, invoke as:
      powershell -ExecutionPolicy Bypass -File .\sim.ps1 run
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "run", "wave", "test", "new", "clean", "list", "help")]
    [string]$Task = "run",

    [Parameter(Position = 1)]
    [string]$Name = "",

    [string]$Module = "counter",

    [ValidateSet("icarus", "verilator")]
    [string]$Sim = "icarus"
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv\Scripts\python.exe"

# Resolve the OSS CAD Suite root: OSS_CAD_SUITE env var -> already on PATH.
# No machine-specific default, so this is portable across machines.
function Resolve-Oss {
    if ($env:OSS_CAD_SUITE -and (Test-Path (Join-Path $env:OSS_CAD_SUITE "bin"))) {
        return (Resolve-Path $env:OSS_CAD_SUITE).Path
    }
    $iv = Get-Command iverilog -ErrorAction SilentlyContinue
    if ($iv) { return (Resolve-Path (Join-Path (Split-Path $iv.Source -Parent) "..")).Path }
    return $null
}

$Oss = Resolve-Oss
if ($Oss) {
    # Both bin AND lib are needed: iverilog/vvp load DLLs from lib at runtime.
    $env:PATH = "$Oss\bin;$Oss\lib;$env:PATH"
}

$Fst = Join-Path $Root "sim_build\$Module\$Module.fst"

function Require-Tools {
    if (-not (Get-Command iverilog -ErrorAction SilentlyContinue)) {
        throw @"
HDL tools not found. Install the OSS CAD Suite and either add its bin\ to PATH,
or set OSS_CAD_SUITE to its root, e.g.:
    `$env:OSS_CAD_SUITE = "C:\Users\<you>\tools\oss-cad-suite"
"@
    }
}

function Invoke-Setup {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "python not found on PATH (install Python 3.12+)"
    }
    if (-not (Test-Path $Venv)) {
        Write-Host "==> creating venv at .venv" -ForegroundColor Cyan
        & python -m venv (Join-Path $Root ".venv")
    }
    Write-Host "==> installing requirements" -ForegroundColor Cyan
    & $Venv -m pip install --upgrade pip | Out-Null
    & $Venv -m pip install -r (Join-Path $Root "requirements.txt")
    Write-Host "==> done. Next: .\sim.ps1 run" -ForegroundColor Green
}

function Invoke-Run {
    $tb = Join-Path $Root "tb\$Module"
    if (-not (Test-Path (Join-Path $tb "runner.py"))) {
        throw "No runner found at tb\$Module\runner.py"
    }
    if (-not (Test-Path $Venv)) { throw "venv missing - run: .\sim.ps1 setup" }
    Require-Tools
    $env:SIM = $Sim
    Write-Host "==> simulate '$Module' with $Sim" -ForegroundColor Cyan
    Push-Location $tb
    try { & $Venv runner.py } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "simulation failed (exit $LASTEXITCODE)" }
}

# GTKWave needs the OSS CAD Suite's GTK runtime env (set by environment.bat).
# Without it, it bails out trying to load a missing svg pixbuf loader.
function Set-GtkEnv {
    if (-not $Oss) { return }
    $env:GTK_EXE_PREFIX     = $Oss
    $env:GTK_DATA_PREFIX    = $Oss
    $env:GDK_PIXBUF_MODULEDIR   = Join-Path $Oss "lib\gdk-pixbuf-2.0\2.10.0\loaders"
    $env:GDK_PIXBUF_MODULE_FILE = Join-Path $Oss "lib\gdk-pixbuf-2.0\2.10.0\loaders.cache"
    $q = Join-Path $Oss "bin\gdk-pixbuf-query-loaders.exe"
    if (Test-Path $q) { & $q --update-cache 2>$null }
}

function Invoke-Wave {
    if (-not (Test-Path $Fst)) { throw "No waveform at $Fst - run a sim first." }
    Require-Tools
    Set-GtkEnv
    Write-Host "==> opening $Fst" -ForegroundColor Cyan
    Start-Process gtkwave -ArgumentList "`"$Fst`""
}

function New-Module {
    param([string]$ModName)
    if (-not $ModName) { throw "usage: .\sim.ps1 new <module_name>" }
    if ($ModName -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        throw "invalid name '$ModName' (use a valid SV identifier)"
    }
    $rtl   = Join-Path $Root "rtl\$ModName.sv"
    $tbdir = Join-Path $Root "tb\$ModName"
    if (Test-Path $rtl)   { throw "rtl\$ModName.sv already exists" }
    if (Test-Path $tbdir) { throw "tb\$ModName already exists" }
    New-Item -ItemType Directory -Path $tbdir | Out-Null

    $rtlTpl = @'
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
'@

    $runnerTpl = @'
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
'@

    $testTpl = @'
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
'@

    # WriteAllText emits UTF-8 without a BOM (Set-Content would add one, which
    # can trip up iverilog / Python source reading).
    [System.IO.File]::WriteAllText($rtl, ($rtlTpl -replace '__MOD__', $ModName))
    [System.IO.File]::WriteAllText((Join-Path $tbdir "runner.py"), ($runnerTpl -replace '__MOD__', $ModName))
    [System.IO.File]::WriteAllText((Join-Path $tbdir "test_$ModName.py"), ($testTpl -replace '__MOD__', $ModName))

    Write-Host "scaffolded:" -ForegroundColor Green
    Write-Host "  rtl\$ModName.sv"
    Write-Host "  tb\$ModName\runner.py"
    Write-Host "  tb\$ModName\test_$ModName.py"
    Write-Host "run it with: .\sim.ps1 run -Module $ModName"
}

switch ($Task) {
    "setup" { Invoke-Setup }
    "run"   { Invoke-Run }
    "wave"  { Invoke-Wave }
    "test"  { Invoke-Run; Invoke-Wave }
    "new"   { New-Module -ModName $Name }
    "clean" {
        $sb = Join-Path $Root "sim_build"
        if (Test-Path $sb) { Remove-Item $sb -Recurse -Force; Write-Host "removed sim_build/" }
        else { Write-Host "nothing to clean" }
    }
    "list" {
        Get-ChildItem (Join-Path $Root "tb") -Directory |
            Where-Object { Test-Path (Join-Path $_.FullName "runner.py") } |
            ForEach-Object { " - $($_.Name)" }
    }
    "help" { Get-Help $PSCommandPath -Detailed }
}
