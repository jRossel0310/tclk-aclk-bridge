<#
.SYNOPSIS
  Hardware-build task wrapper for the Kria KR260 flow (Vivado). The hardware
  counterpart to sim.ps1. (git bash users: use ./hw.sh instead.)

.EXAMPLE
  .\hw.ps1 build                # RTL -> bitstream via vivado/build.tcl (batch)
  .\hw.ps1 build -Tcl vivado\build_pinblink.tcl -Name pinblink   # a different design
  .\hw.ps1 gui                  # open the generated project in the Vivado GUI
  .\hw.ps1 clean                # delete the build dir

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

    [string]$Vivado = "",

    # Which build tcl to run (default vivado\build.tcl) and the project name used
    # for the build dir / runs folder. Lets the same AV-retry wrapper drive other
    # designs, e.g. -Tcl vivado\build_pinblink.tcl -Name pinblink.
    [string]$Tcl  = "",
    [string]$Name = "uart_echo"
)

$ErrorActionPreference = "Stop"
$Root     = $PSScriptRoot
if ($Tcl) {
    $BuildTcl = if ([System.IO.Path]::IsPathRooted($Tcl)) { $Tcl } else { Join-Path $Root $Tcl }
} else {
    $BuildTcl = Join-Path $Root "vivado\build.tcl"
}
# Build in a space-free directory: Vivado's IP Integrator (block design) breaks
# when the project path contains spaces, and this repo lives under "Summer 2026".
# Derived from -Name (one dir per design); the build task exports it as
# KRIA_BUILD_DIR, which the build tcls honor.
$BuildDir = Join-Path $env:USERPROFILE "kria-builds\$Name"

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
        $env:KRIA_BUILD_DIR = $BuildDir
        $parent = Split-Path $BuildDir -Parent
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
        $log = Join-Path $parent "build.log"

        # Vivado's batch IP-Integrator rule init intermittently fails to read its
        # own .tcl files ("couldn't read file ...: No error", "bd::utils::*"),
        # usually antivirus scanning Vivado's many small script files mid-load.
        # It fails fast (~30s, before synthesis), so retry ONLY that flake; never
        # retry a real synth/impl failure.
        # "couldn't read file" is the common thread across all these AV-induced
        # transient read failures (utils_dbg.tcl, aximm xgui, rule .tcl, ...).
        $bdFlakeSignatures = @(
            "couldn't read file",
            "create_bd_design' failed",
            "Error in initialization of Rule object",
            "Failed to load customization data",
            "Failed to load feature",
            "bd::utils::"
        )
        $maxAttempts = 12
        $ok = $false
        for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
            Write-Host "==> building bitstream with $vivado (attempt $attempt/$maxAttempts)" -ForegroundColor Cyan
            Write-Host "    output dir: $BuildDir" -ForegroundColor DarkGray
            # Run from a space-free CWD; IP Integrator also chokes on spaces in cwd.
            Push-Location $parent
            try {
                & $vivado -mode batch -source $BuildTcl -nojournal -log $log
            } finally { Pop-Location }
            if ($LASTEXITCODE -eq 0) { $ok = $true; break }

            $logText = if (Test-Path $log) { Get-Content $log -Raw } else { "" }
            $isFlake = $false
            foreach ($sig in $bdFlakeSignatures) { if ($logText -like "*$sig*") { $isFlake = $true; break } }
            if ($isFlake -and $attempt -lt $maxAttempts) {
                Write-Host "==> block-design init flaked (Vivado batch IPI bug); retrying..." -ForegroundColor Yellow
                continue
            }
            throw "Vivado build failed (exit $LASTEXITCODE) - see $log"
        }
        if ($ok) {
            Write-Host "==> done. Bitstream: $BuildDir\$Name.runs\impl_1\uart_echo_bd_wrapper.bit" -ForegroundColor Green
        }
    }
    "gui" {
        $vivado = Resolve-Vivado
        $xpr = Join-Path $BuildDir "$Name.xpr"
        if (-not (Test-Path $xpr)) { throw "No project at $xpr - run: .\hw.ps1 build" }
        Write-Host "==> opening $xpr" -ForegroundColor Cyan
        Start-Process $vivado -ArgumentList "`"$xpr`""
    }
    "clean" {
        if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force; Write-Host "removed $BuildDir" }
        else { Write-Host "nothing to clean" }
    }
    "help" { Get-Help $PSCommandPath -Detailed }
}
