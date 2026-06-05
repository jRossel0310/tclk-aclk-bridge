<#
.SYNOPSIS
  Hardware-build task wrapper for the Kria KR260 flow (Vivado). The hardware
  counterpart to sim.ps1. (git bash users: use ./hw.sh instead.)

.EXAMPLE
  .\hw.ps1 build                # RTL -> bitstream via vivado/build.tcl (batch)
  .\hw.ps1 gui                  # open the generated project in the Vivado GUI
  .\hw.ps1 clean                # delete vivado/build/

.NOTES
  Finds Vivado in one of three ways (first that works wins):
    1. -Vivado <path-to-vivado.bat/exe>
    2. $env:VIVADO       (full path to the vivado launcher)
    3. vivado already on PATH
  Loading the bitstream onto the board is intentionally NOT handled here — do
  that yourself (JTAG Hardware Manager, fpgautil, or xmutil loadapp).
  If running scripts is blocked, invoke as:
      powershell -ExecutionPolicy Bypass -File .\hw.ps1 build
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("build", "gui", "clean", "help")]
    [string]$Task = "build",

    [string]$Vivado = ""
)

$ErrorActionPreference = "Stop"
$Root     = $PSScriptRoot
$BuildTcl = Join-Path $Root "vivado\build.tcl"
$BuildDir = Join-Path $Root "vivado\build"

function Resolve-Vivado {
    if ($Vivado) {
        if (Test-Path $Vivado) { return $Vivado }
        throw "Vivado not found at -Vivado '$Vivado'"
    }
    if ($env:VIVADO -and (Test-Path $env:VIVADO)) { return $env:VIVADO }
    $cmd = Get-Command vivado -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw @"
Vivado not found. Install AMD Vivado (ML Standard Edition is free and supports
the KR260 / xck26), then make it resolvable in ONE of these ways:
    .\hw.ps1 build -Vivado "C:\Xilinx\Vivado\2024.2\bin\vivado.bat"
    `$env:VIVADO = "C:\Xilinx\Vivado\2024.2\bin\vivado.bat"
    # or add Vivado's bin\ to PATH
"@
}

switch ($Task) {
    "build" {
        $vivado = Resolve-Vivado
        Write-Host "==> building bitstream with $vivado" -ForegroundColor Cyan
        & $vivado -mode batch -source $BuildTcl -nojournal -log (Join-Path $Root "vivado\build.log")
        if ($LASTEXITCODE -ne 0) { throw "Vivado build failed (exit $LASTEXITCODE)" }
        Write-Host "==> done. Bitstream is under vivado\build\uart_echo.runs\impl_1\" -ForegroundColor Green
    }
    "gui" {
        $vivado = Resolve-Vivado
        $xpr = Join-Path $BuildDir "uart_echo.xpr"
        if (-not (Test-Path $xpr)) { throw "No project at $xpr - run: .\hw.ps1 build" }
        Write-Host "==> opening $xpr" -ForegroundColor Cyan
        Start-Process $vivado -ArgumentList "`"$xpr`""
    }
    "clean" {
        if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force; Write-Host "removed vivado\build\" }
        else { Write-Host "nothing to clean" }
    }
    "help" { Get-Help $PSCommandPath -Detailed }
}
