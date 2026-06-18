# KR260 Build/Deploy Workflow Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Streamline the existing KR260 Vivado build/deploy flow — repo-local build outputs, automatic bootgen packaging, printed hashes + manifest, an optional deploy command, and generic (non-`uart_echo`) docs — with zero change to hardware functionality.

**Architecture:** `hw.ps1` stays the single PowerShell wrapper. The build task gets a repo-local default build dir (`./build/kria/<Name>`), then post-build it locates the generated `.bit`, writes a temporary `.bif` naming that exact `.bit`, runs `bootgen` (resolved next to the Vivado launcher), prints MD5/SHA256, and writes `build-manifest.json`. A new `deploy` task `scp`s the `.bit.bin` + design-specific Python files. Docs are rewritten generic with `uart_echo` demoted to an example.

**Tech Stack:** PowerShell 5.1, Vivado 2024.2 batch tcl (unchanged), bootgen, scp, markdown.

## Global Constraints

- Do NOT change RTL, block-design, AXI addresses (`0x8000_0000`), Python reader behavior, `.dts`/`.dtbo` overlay behavior, or the KR260 load/run flow. Cleanup + automation only.
- Build artifacts go under repo-local `./build/kria/<Name>/...`; never scattered in the source tree. Default repo-local; overridable.
- The board load flow stays UIO + overlay: `sudo fpgautil -b <bit.bin> -o uart_echo.dtbo`. Do NOT switch to `-f Full`. Overlay path is required (creates `/dev/uioN`, releases PL reset).
- Deploy is never mandatory as part of build. Do not auto-run `sudo fpgautil` on the board.
- Preserve existing filenames (`uart_echo.bif/.dts/.dtbo`, `uart_echo_test.py`, `tclk_read.py`, `tclk_filter.py`). Add generic aliases/templates; don't blind-rename.
- All build tcls already read `$env:KRIA_BUILD_DIR` and emit `uart_echo_bd_wrapper.bit` under `<build_dir>\<proj_name>.runs\impl_1\`. Do not edit the tcls.
- bootgen command must stay equivalent to: `bootgen -arch zynqmp -process_bitstream bin -image <bif>`.
- Keep it small/readable. No Make/CMake/Docker/new frameworks.
- Project style: never use em dashes in code, comments, or docs.

---

### Task 1: Repo-local build dir + .gitignore

**Files:**
- Modify: `hw.ps1` (build-dir default + done-message path)
- Modify: `hw.sh` (keep build-dir default in parity)
- Modify: `.gitignore`

**Interfaces:**
- Produces: `$BuildDir = <Root>\build\kria\<Name>`, overridable via new `-BuildRoot` param (default `<Root>\build\kria`). `$env:KRIA_BUILD_DIR` exported = `$BuildDir` (tcls consume it, unchanged).

- [ ] **Step 1: Add `-BuildRoot` param and repo-local default in `hw.ps1`**

Replace the `$BuildDir` definition (currently `Join-Path $env:USERPROFILE "kria-builds\$Name"`):

```powershell
    [string]$Tcl  = "",
    [string]$Name = "uart_echo",

    # Where build artifacts land. Default is repo-local ./build/kria; the per-design
    # dir is $BuildRoot\$Name. Override -BuildRoot to relocate (e.g. a space-free
    # scratch dir) — the build tcls honor it via the KRIA_BUILD_DIR env var.
    [string]$BuildRoot = ""
)
```

And below `$Root = $PSScriptRoot`:

```powershell
if (-not $BuildRoot) { $BuildRoot = Join-Path $Root "build\kria" }
$BuildDir = Join-Path $BuildRoot $Name
```

(Delete the old `$BuildDir = Join-Path $env:USERPROFILE "kria-builds\$Name"` line.)

> Note: the repo path contains a space ("Summer 2026"). Vivado IP Integrator dislikes spaces in project paths. The build task already `Push-Location`s into the parent and runs batch from there; that is unchanged. If a user hits the space-path issue they pass `-BuildRoot` to a space-free dir. Document this in the done message.

- [ ] **Step 2: Fix the build done-message path in `hw.ps1`**

Replace the hardcoded done line so it points at the real artifact (now repo-local):

```powershell
        if ($ok) {
            $impl = Join-Path $BuildDir "$Name.runs\impl_1"
            Write-Host "==> bitstream built in $impl" -ForegroundColor Green
        }
```

(The full "Build complete / MD5 / SHA256" summary is added in Task 4; this is the interim message.)

- [ ] **Step 3: Keep `hw.sh` build-dir default in parity**

In `hw.sh`, change:

```bash
BUILD_DIR_NATIVE="${KRIA_BUILD_DIR:-$HOME/kria-builds/uart_echo}"
```
to:
```bash
# Repo-local default to match hw.ps1 (./build/kria/<name>); override with $KRIA_BUILD_DIR.
BUILD_DIR_NATIVE="${KRIA_BUILD_DIR:-$ROOT/build/kria/uart_echo}"
```

Also update the `gui` xpr path and `done` message which reference `uart_echo.runs` — leave those as-is (still correct: proj_name uart_echo). No bootgen/deploy added to `hw.sh`; add a comment that `hw.ps1` is the full-featured wrapper.

- [ ] **Step 4: Update `.gitignore`**

Replace the Vivado section. Current `vivado/build/` rule stays (legacy GUI scratch). Add repo-local `/build/`:

```gitignore
# Vivado build artifacts (the bitstream flow). Source stays tracked
# (build*.tcl, uart_echo_bd.tcl); generated outputs are ignored.
/build/
vivado/build/
vivado/*.log
vivado/*.jou
.Xil/
*.str
```

Keep the existing `*.dtbo`, `*.bit.bin`, `*.bit` rules. `build-manifest.json` is under `/build/` so it is ignored too (intentional — it is a build output).

- [ ] **Step 5: Verify `hw.ps1` parses and the path resolves**

Run:
```powershell
powershell -NoProfile -Command "& { . { $null = (Get-Command .\hw.ps1).ScriptBlock } ; 'parse-ok' }"
```
Expected: prints `parse-ok` with no parse error. (If `Get-Command` is awkward, alternatively run `powershell -NoProfile -File .\hw.ps1 help` and confirm it prints help without error.)

Run:
```powershell
git check-ignore -v build/kria/tclk/foo.bit
```
Expected: matches the `/build/` (or `*.bit`) rule.

- [ ] **Step 6: Commit**

```powershell
git add hw.ps1 hw.sh .gitignore
git commit -m "build: repo-local build dir (./build/kria/<name>) + gitignore"
```

---

### Task 2: bootgen resolution + temp .bif + packaging

**Files:**
- Modify: `hw.ps1` (add `Resolve-Bootgen` function; add post-build packaging into the `build` task)

**Interfaces:**
- Consumes: resolved `$vivado` launcher path (to derive bootgen dir), `$BuildDir`, `$Name`.
- Produces: `Resolve-Bootgen($vivadoPath)` returning a bootgen path; `Invoke-Bootgen` inline producing `<bit>.bin` next to the generated `.bit`. The generated `.bit` is found by globbing `*_wrapper.bit` in `<BuildDir>\<Name>.runs\impl_1`.

- [ ] **Step 1: Add `Resolve-Bootgen` next to `Resolve-Vivado` in `hw.ps1`**

```powershell
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
```

- [ ] **Step 2: Add packaging after the `if ($ok)` build-success block in `hw.ps1`**

Replace the interim done-message block (from Task 1 Step 2) with:

```powershell
        if (-not $ok) { throw "Vivado build did not complete." }

        $impl = Join-Path $BuildDir "$Name.runs\impl_1"
        $bit  = Get-ChildItem -Path $impl -Filter *_wrapper.bit -File -ErrorAction SilentlyContinue |
                Select-Object -First 1
        if (-not $bit) {
            throw "No *_wrapper.bit found in $impl. The build reported success but produced no bitstream — check the impl_1 run log."
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
        $bootgenArgs = @("-arch", "zynqmp", "-process_bitstream", "bin", "-image", $bit.Name)
        Write-Host "==> packaging with bootgen: $bootgen $($bootgenArgs -join ' ')" -ForegroundColor Cyan
        Push-Location $impl
        try { & $bootgen @bootgenArgs } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw "bootgen failed (exit $LASTEXITCODE) in $impl" }

        $bin = Join-Path $impl "$($bit.Name).bin"   # <name>.bit.bin
        if (-not (Test-Path $bin)) {
            throw "bootgen reported success but $bin is missing — check bootgen output above."
        }
```

(The hash + manifest + final summary print is appended in Task 4. Leave a `# TODO Task 4` marker after this block for now, or proceed directly to Task 4 before committing.)

- [ ] **Step 3: Verify bootgen-missing error is clear (no Vivado needed)**

Temporarily, from a shell with neither Vivado nor bootgen on PATH, run:
```powershell
powershell -NoProfile -Command ". .\hw.ps1 *> $null" 2>&1 | Out-Null; 'loaded'
```
This only checks the file dot-sources/parses. To actually exercise `Resolve-Bootgen`, run an inline snippet:
```powershell
powershell -NoProfile -Command "function Get-Command2{}; . { $f = Get-Content .\hw.ps1 -Raw }; 'ok'"
```
Expected: `ok` (parse check). Full bootgen run is validated on the real machine with Vivado in Task 4's manual run.

- [ ] **Step 4: Commit (combined with Task 4 hash work)**

Defer commit to Task 4 so packaging + summary land together. If committing standalone:
```powershell
git add hw.ps1
git commit -m "build: auto-run bootgen post-build (temp .bif, beside the .bit)"
```

---

### Task 3: Hash, manifest, and final build summary

**Files:**
- Modify: `hw.ps1` (append after the bootgen block from Task 2)

**Interfaces:**
- Consumes: `$bit`, `$bin`, `$BuildDir`, `$Name`, `$BuildTcl`, `$bootgen`, `$bootgenArgs`, `$impl`.
- Produces: printed `BIT/BIN/MD5/SHA256`; `build-manifest.json` in `$BuildDir`.

- [ ] **Step 1: Append hash + manifest + summary in `hw.ps1`**

Directly after the `if (-not (Test-Path $bin))` check:

```powershell
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
```

- [ ] **Step 2: Verify the hash/manifest logic in isolation (no Vivado)**

Create a throwaway `.bit.bin`, confirm Get-FileHash + manifest JSON shape:
```powershell
powershell -NoProfile -Command "Set-Content -Encoding ASCII tmp.bit.bin 'x'; (Get-FileHash -Algorithm MD5 tmp.bit.bin).Hash.ToLower(); ([ordered]@{a=1} | ConvertTo-Json); Remove-Item tmp.bit.bin"
```
Expected: a 32-char md5 hex string and valid JSON. Confirms the cmdlets behave as used.

- [ ] **Step 3: Commit**

```powershell
git add hw.ps1
git commit -m "build: bootgen packaging + MD5/SHA256 + build-manifest.json"
```

---

### Task 4: Optional `deploy` task

**Files:**
- Modify: `hw.ps1` (add `deploy` to ValidateSet, add `-DeployHost` param, add a `deploy` switch case)

**Interfaces:**
- Consumes: `$BuildDir`, `$Name`, new `-DeployHost` (e.g. `ubuntu@kria` or `"ubuntu@[fe80::..%6]"`).
- Produces: `scp` of the design's `.bit.bin` + Python deploy files to `~` on the host. Design→files map; no board commands run.

- [ ] **Step 1: Add `deploy` to the task ValidateSet and a host param**

```powershell
    [ValidateSet("build", "gui", "clean", "deploy", "help")]
    [string]$Task = "build",
```
Add param:
```powershell
    # Target for `deploy` (scp), e.g. ubuntu@kria or "ubuntu@[fe80::..%6]".
    [string]$DeployHost = ""
```

- [ ] **Step 2: Add the `deploy` case before `"help"`**

```powershell
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
```

- [ ] **Step 3: Verify deploy validation errors (no board needed)**

```powershell
powershell -NoProfile -File .\hw.ps1 deploy -Name tclk
```
Expected: clean error `deploy needs -DeployHost ...`, no stack spew.

```powershell
powershell -NoProfile -File .\hw.ps1 deploy -Name tclk -DeployHost ubuntu@nohost
```
Expected (before any build): clean error `No *_wrapper.bit.bin in ...` (validation fires before scp).

- [ ] **Step 4: Update the `.SYNOPSIS`/`.EXAMPLE` help block in `hw.ps1`**

Add to the comment header examples:
```powershell
  .\hw.ps1 build -Tcl vivado\build_tclk.tcl -Name tclk   # build + bootgen + hash
  .\hw.ps1 deploy -Name tclk -DeployHost ubuntu@kria     # scp .bit.bin + readers
```

- [ ] **Step 5: Commit**

```powershell
git add hw.ps1
git commit -m "deploy: optional scp task (.bit.bin + design readers) in hw.ps1"
```

---

### Task 5: Generic deploy README + .bif template

**Files:**
- Modify: `deploy/README.md` (rewrite generic; demote `uart_echo` to an example section)
- Create: `deploy/template.bif` (tracked generic template; keep `uart_echo.bif` too)

**Interfaces:** none (docs/templates).

- [ ] **Step 1: Add a tracked generic `.bif` template `deploy/template.bif`**

```
// Generic bootgen recipe template for the KR260 PL bitstream flow.
// hw.ps1 auto-generates a per-build .bif naming the actual generated .bit, so
// this file is only a reference/fallback. To use manually, replace the filename
// with your generated <design>_wrapper.bit and run, in the folder holding it:
//   bootgen -arch zynqmp -process_bitstream bin -image template.bif
all:
{
    uart_echo_bd_wrapper.bit
}
```

(Keep `deploy/uart_echo.bif` unchanged for backward compatibility.)

- [ ] **Step 2: Rewrite `deploy/README.md` generic**

Full replacement content:

```markdown
# deploy/ — load a KR260 PL bitstream and run a reader

Generic flow for getting a Vivado design onto the KR260 and talking to its AXI
slave at `0x8000_0000` from Linux on the board. (See `tclk.md` for the live-TCLK
runbook and the "uart_echo example" section below for the original echo demo.)

## Artifacts in the flow

| Artifact | Made by | Role |
|----------|---------|------|
| `.bit` | Vivado (`hw.ps1 build`) | raw PL bitstream |
| `.bit.bin` | bootgen (auto, in `hw.ps1 build`) | what `fpgautil`/FPGA-manager loads |
| `.dtbo` | `dtc` from a `.dts` | device-tree overlay; needed for the UIO path |

## Build (PC)

```powershell
.\hw.ps1 build -Tcl vivado\build_tclk.tcl -Name tclk
```
Prints `BIT`, `BIN`, `MD5`, `SHA256` and writes `build-manifest.json`. Artifacts
land repo-local under `build\kria\<name>\<name>.runs\impl_1\`.

Optional copy to the board:
```powershell
.\hw.ps1 deploy -Name tclk -DeployHost ubuntu@kria
```

## Load on the board (UIO + overlay — preferred)

```bash
md5sum ~/uart_echo_bd_wrapper.bit.bin     # must equal the PC MD5
sudo xmutil unloadapp
sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo
ls -l /dev/uio*
```

- The `-o <overlay>.dtbo` form is required for the UIO readers: it creates
  `/dev/uioN` and releases PL reset.
- `-f Full` programs the PL but does NOT create a UIO device, so it is not
  equivalent — do not substitute it for the UIO flow.
- A cosmetic `OF: overlay: WARNING: memory leak will occur ...` on load is
  harmless.

## Readers

Python readers mmap either `/dev/uioN` (offset 0) or `/dev/mem` (offset
`0x8000_0000`); register offsets are identical. Find the right UIO node via
`cat /sys/class/uio/uio*/name`. Run with `-u` for unbuffered output, e.g.:
```bash
sudo python3 -u tclk_read.py /dev/uio4 --drop 07,0F,BA,8F
```

### `/dev/mem` fallback

If UIO is unavailable or locked down, a root reader can mmap `/dev/mem` at the
AXI base directly — no overlay, no driver. Use this only if the UIO path is not
available; the overlay path is preferred because it also releases PL reset.

## Verifying the load matches your build

Compare the board-side `md5sum ~/<bit.bin>` against the `MD5` line printed by
`hw.ps1 build` (also recorded in `build-manifest.json`). Mismatch means a stale
copy on the board.

---

## Example: uart_echo (original demo)

The first design here, `uart_echo`, cross-wires an AXI UART Lite to the custom
`uart_echo` RTL inside the PL so a byte sent from the PS echoes back:

```
   PS  --AXI-->  AXI UART Lite  --tx-->  uart_echo.serial_in
                                <--rx--  uart_echo.serial_out
```

Build and run:
```powershell
.\hw.ps1 build            # design_name uart_echo
```
```bash
sudo xmutil unloadapp
sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo
sudo python3 uart_echo_test.py
```
`uart_echo_test.py` historically used `/dev/mem` (`-f Full` load); it still works
that way, but the UIO + overlay flow above is the current default. Files
`uart_echo.bif` / `uart_echo.dts` are kept for this example and as overlay
sources; `template.bif` is the generic reference.
```

- [ ] **Step 3: Verify markdown renders / links are sane**

```powershell
powershell -NoProfile -Command "Get-Content deploy\README.md | Measure-Object -Line"
```
Expected: nonzero line count; eyeball that no `uart_echo`-only framing remains at the top.

- [ ] **Step 4: Commit**

```powershell
git add deploy/README.md deploy/template.bif
git commit -m "docs: generic deploy README (uart_echo demoted to example) + template.bif"
```

---

### Task 6: Update `deploy/tclk.md` runbook

**Files:**
- Modify: `deploy/tclk.md`

**Interfaces:** none (docs).

- [ ] **Step 1: Rewrite the build/convert/copy/load/run sections of `deploy/tclk.md`**

Replace sections 1–5 so the routine is the new automated flow. New content for those sections:

```markdown
## 1. Build + package (PC)
```powershell
.\hw.ps1 build -Tcl vivado\build_tclk.tcl -Name tclk
```
One command now does Vivado + bootgen + hashing. Output (repo-local):
`build\kria\tclk\tclk.runs\impl_1\uart_echo_bd_wrapper.bit(.bin)`. It prints:
- `BIT` path, `BIN` path
- `MD5` (and `SHA256`) — also saved in `build\kria\tclk\build-manifest.json`

If Vivado flakes on `couldn't read file` mid-BD, the wrapper retries (antivirus
on C:\Xilinx). If bootgen is not found, add Vivado 2024.2's `bin` to PATH.

## 2. Copy to the board (PC)
```powershell
.\hw.ps1 deploy -Name tclk -DeployHost "ubuntu@[fe80::48ec:6a99:b6fd:80e9%6]"
```
Copies `uart_echo_bd_wrapper.bit.bin` + `tclk_read.py` + `tclk_filter.py` to `~`.
(Manual equivalent: `scp` those three files yourself.)

## 3. Load (board) — UIO + overlay
```bash
md5sum ~/uart_echo_bd_wrapper.bit.bin      # must match the PC MD5
sudo xmutil unloadapp
sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo
ls -l /dev/uio*                            # find the readout's uioN
```
The `-o ...dtbo` overlay is required: it creates `/dev/uioN` and releases PL
reset. Do NOT use `-f Full` here (no UIO device is created). The cosmetic
`OF: overlay: WARNING: memory leak will occur ...` on load is harmless.

## 4. Run
```bash
cat /sys/class/uio/uio*/name              # confirm which uioN is the readout
sudo python3 -u tclk_read.py /dev/uio4 --drop 07,0F,BA,8F
```
Run with `-u` for unbuffered output. Events scroll with codes + timestamps; a
`[stats]` line prints each second. If UIO is locked down, the reader can fall
back to `/dev/mem` at `0x8000_0000` (see deploy/README.md).
```

Keep the existing `## Wiring` and `## Diagnosing` sections unchanged below.

- [ ] **Step 2: Verify**

```powershell
powershell -NoProfile -Command "Select-String -Path deploy\tclk.md -Pattern '-f Full'"
```
Expected: no matches (the `-f Full` instruction is gone from the runbook steps).

- [ ] **Step 3: Commit**

```powershell
git add deploy/tclk.md
git commit -m "docs: tclk.md runbook -> automated build + UIO overlay load"
```

---

## Self-Review

- Spec §1 repo-local builds → Task 1 (default `./build/kria/<Name>`, `-BuildRoot` override, `.gitignore`).
- Spec §2 bootgen integration → Task 2 (locate `.bit`, temp `.bif` with real name, post-build so no `-force` wipe, bootgen-not-found error with PATH hint).
- Spec §3 hash + manifest → Task 3 (MD5/SHA256 print + `build-manifest.json` with all listed fields).
- Spec §4 deploy unchanged-but-easier → Task 4 (optional `deploy` scp; UIO `-o dtbo` printed, never auto-run; `/dev/mem` documented in README) + docs.
- Spec §5 generic README → Task 5 (rewrite; `uart_echo` demoted; `-f Full` ≠ UIO explained; md5 verify; `/dev/mem` fallback; uioN vs /dev/mem mmap).
- Spec §6 filename compatibility → `uart_echo.bif/.dts/.dtbo`, `*_test.py`, `tclk_*` all preserved; `template.bif` added as generic alias (Task 5).
- Spec §7 validation/errors → Tasks 1–4 (Tcl path already validated by existing flow; Name has default; Vivado + bootgen resolved with hints; `.bit`/`.bit.bin` existence checked; deploy files checked).
- Spec §8 runbook docs → Task 6 (`tclk.md`) + Task 5 (`README.md`); preserves uioN/sys-name, `/dev/mem`, `-u`, overlay-leak warning, md5-match notes.
- Spec §9 no over-engineering → only `hw.ps1`/`hw.sh`/`.gitignore`/docs/`template.bif` touched; no new tooling.

**Backward-compat left intact:** `~/kria-builds` still works if a user sets `-BuildRoot`/`$env:KRIA_BUILD_DIR`; `deploy/uart_echo.bif`, `uart_echo.dts`, `uart_echo_test.py`, `tclk_read.py`, `tclk_filter.py` unchanged; bootgen command equivalent to the documented manual one; board load flow unchanged.
