<#
.SYNOPSIS
  Hardware-build task wrapper for the Kria KR260 flow (Vivado). The hardware
  counterpart to sim.ps1. (git bash users: use ./hw.sh instead.)

.EXAMPLE
  .\hw.ps1 build                # RTL -> bitstream + bootgen + hash via vivado/build.tcl
  .\hw.ps1 build -Tcl vivado\build_tclk.tcl -Name tclk           # build + bootgen + hash
  .\hw.ps1 build -Tcl vivado\build_pinblink.tcl -Name pinblink   # a different design
  .\hw.ps1 deploy -Name tclk -DeployHost ubuntu@kria             # scp .bit.bin + readers
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
    [ValidateSet("build", "gui", "clean", "deploy", "help")]
    [string]$Task = "build",

    [string]$Vivado = "",

    # Which build tcl to run (default vivado\build.tcl) and the project name used
    # for the build dir / runs folder. Lets the same AV-retry wrapper drive other
    # designs, e.g. -Tcl vivado\build_pinblink.tcl -Name pinblink.
    [string]$Tcl  = "",
    [string]$Name = "uart_echo",

    # Where build artifacts land. Default is repo-local ./build/kria; the per-design
    # dir is $BuildRoot\$Name. Override -BuildRoot to relocate (e.g. a space-free
    # scratch dir) - the build tcls honor it via the KRIA_BUILD_DIR env var.
    [string]$BuildRoot = "",

    # Target for `deploy` (scp), e.g. ubuntu@kria or "ubuntu@[fe80::..%6]".
    [string]$DeployHost = ""
)

$ErrorActionPreference = "Stop"
$Root     = $PSScriptRoot
if ($Tcl) {
    $BuildTcl = if ([System.IO.Path]::IsPathRooted($Tcl)) { $Tcl } else { Join-Path $Root $Tcl }
} else {
    $BuildTcl = Join-Path $Root "vivado\build.tcl"
}
# Build dir is repo-local by default (./build/kria/<Name>), one dir per design.
# The build task exports it as KRIA_BUILD_DIR, which the build tcls honor.
# NOTE: Vivado's IP Integrator (block design) breaks when the project path
# contains spaces, and this repo lives under "Summer 2026". The build task
# Push-Locations into a space-free parent and runs batch from there; if a user
# still hits a space-path issue, pass -BuildRoot to a space-free directory.
if (-not $BuildRoot) { $BuildRoot = Join-Path $Root "build\kria" }
$BuildDir = Join-Path $BuildRoot $Name

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

function Resolve-Bootgen {
    param([string]$VivadoPath)
    # Prefer bootgen sitting next to the resolved vivado launcher (same bin dir).
    if ($VivadoPath) {
        $bin = Split-Path $VivadoPath -Parent
        foreach ($exe in @("bootgen.bat", "bootgen.exe", "bootgen")) {
            $cand = Join-Path $bin $exe
            if (Test-Path $cand) { return $cand }
        }
    }
    $cmd = Get-Command bootgen -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw @"
bootgen not found. It ships with Vivado/Vitis. Either add Vivado 2024.2's bin
directory to PATH, e.g.
    `$env:PATH += ';C:\Xilinx\Vivado\2024.2\bin'
or pass -Vivado pointing at that bin's vivado launcher so bootgen is found beside it.
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
        if (-not $ok) { throw "Vivado build did not complete." }

        # --- package the bitstream with bootgen ---------------------------------
        $impl = Join-Path $BuildDir "$Name.runs\impl_1"
        $bit  = Get-ChildItem -Path $impl -Filter *_wrapper.bit -File -ErrorAction SilentlyContinue |
                Select-Object -First 1
        if (-not $bit) {
            throw "No *_wrapper.bit found in $impl. The build reported success but produced no bitstream - check the impl_1 run log."
        }

        # Write a temp .bif naming the ACTUAL generated .bit, right next to it. Doing
        # this post-build (not before) avoids the old create_project -force wipe of the
        # runs dir that used to delete a manually-copied .bif.
        $bifPath = Join-Path $impl "$($bit.BaseName).bif"
        @"
// auto-generated by hw.ps1; bootgen recipe for $($bit.Name)
all:
{
    $($bit.Name)
}
"@ | Set-Content -Encoding ASCII $bifPath

        $bootgen = Resolve-Bootgen $vivado
        # -image takes the .bif recipe (NOT the .bit); bootgen runs from $impl so a
        # bare filename resolves there.
        $bootgenArgs = @("-arch", "zynqmp", "-process_bitstream", "bin", "-image", (Split-Path $bifPath -Leaf))
        Write-Host "==> packaging with bootgen: $bootgen $($bootgenArgs -join ' ')" -ForegroundColor Cyan
        Push-Location $impl
        try { & $bootgen @bootgenArgs } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw "bootgen failed (exit $LASTEXITCODE) in $impl" }

        $bin = Join-Path $impl "$($bit.Name).bin"   # <name>.bit.bin
        if (-not (Test-Path $bin)) {
            throw "bootgen reported success but $bin is missing - check bootgen output above."
        }

        # --- hashes + manifest --------------------------------------------------
        $md5    = (Get-FileHash -Algorithm MD5    $bin).Hash.ToLower()
        $sha256 = (Get-FileHash -Algorithm SHA256 $bin).Hash.ToLower()

        $commit = (git -C $Root rev-parse --short HEAD 2>$null)
        if ($LASTEXITCODE -ne 0 -or -not $commit) { $commit = $null }

        $manifest = [ordered]@{
            design       = $Name
            timestamp    = (Get-Date).ToString("o")
            gitCommit    = $commit
            projectPath  = $BuildDir
            bit          = $bit.FullName
            bin          = $bin
            md5          = $md5
            sha256       = $sha256
            tcl          = $BuildTcl
            bootgen      = "$bootgen $($bootgenArgs -join ' ')"
        }
        $manifestPath = Join-Path $BuildDir "build-manifest.json"
        $manifest | ConvertTo-Json | Set-Content -Encoding UTF8 $manifestPath

        Write-Host ""
        Write-Host "Build complete." -ForegroundColor Green
        Write-Host "BIT:     $($bit.FullName)"
        Write-Host "BIN:     $bin"
        Write-Host "MD5:     $md5"
        Write-Host "SHA256:  $sha256"
        Write-Host "MANIFEST:$manifestPath"
        Write-Host ""
        Write-Host "Board check: md5sum ~/$($bit.Name).bin  (must equal MD5 above)" -ForegroundColor DarkGray
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
    "deploy" {
        if (-not $DeployHost) { throw "deploy needs -DeployHost, e.g. .\hw.ps1 deploy -Name tclk -DeployHost ubuntu@kria" }
        $impl = Join-Path $BuildDir "$Name.runs\impl_1"
        $bin  = Get-ChildItem -Path $impl -Filter *_wrapper.bit.bin -File -ErrorAction SilentlyContinue |
                Select-Object -First 1
        if (-not $bin) { throw "No *_wrapper.bit.bin in $impl. Run: .\hw.ps1 build -Tcl <tcl> -Name $Name" }

        # Design -> Python deploy files (scp'd alongside the .bit.bin). Add entries as
        # designs are added; default copies just the reader+filter pair for tclk.
        $deployDir = Join-Path $Root "deploy"
        $pyMap = @{
            "tclk"      = @("tclk_read.py", "tclk_filter.py")
            "aclk"      = @("aclk_read.py", "tclk_filter.py")
            "clk"       = @("clk_read.py", "tclk_filter.py")
            "uart_echo" = @("uart_echo_test.py")
        }
        $pyFiles = @()
        if ($pyMap.ContainsKey($Name)) {
            foreach ($f in $pyMap[$Name]) {
                $p = Join-Path $deployDir $f
                if (-not (Test-Path $p)) { throw "deploy file missing: $p" }
                $pyFiles += $p
            }
        } else {
            Write-Host "note: no Python deploy files mapped for '$Name'; copying the .bit.bin only." -ForegroundColor Yellow
        }

        $files = @($bin.FullName) + $pyFiles
        Write-Host "==> scp to ${DeployHost}:~" -ForegroundColor Cyan
        $files | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        & scp @($files + "${DeployHost}:~")
        if ($LASTEXITCODE -ne 0) { throw "scp failed (exit $LASTEXITCODE)" }
        Write-Host "==> copied. Board load (manual, UIO + overlay):" -ForegroundColor Green
        Write-Host "    md5sum ~/$($bin.Name)"
        Write-Host "    sudo xmutil unloadapp"
        Write-Host "    sudo fpgautil -b ~/$($bin.Name) -o uart_echo.dtbo"
    }
    "help" { Get-Help $PSCommandPath -Detailed }
}
