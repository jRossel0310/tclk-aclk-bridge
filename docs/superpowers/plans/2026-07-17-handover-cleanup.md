# Handover Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prune the repo to the single operational product (the `aclk_pipeline` KR260 bitstream + its capture software), fix loose ends, and add an operator runbook, per `docs/superpowers/specs/2026-07-17-handover-cleanup-design.md`.

**Architecture:** Pure-subtraction cleanup on the `handover-cleanup` branch (already created, spec committed). Keep = the file list in `vivado/build_aclk_pipeline.tcl` + the deploy capture stack + tests/testbenches for kept modules + operator docs. Everything else is deleted (survives in git history; no history rewrite). Three verification gates: cocotb sim sweep, deploy pytest, and a full Vivado bitstream build.

**Tech Stack:** Git, PowerShell 5.1 (Windows), cocotb + Icarus via `sim.ps1`, pytest, Vivado 2024.2 via `hw.ps1`.

## Global Constraints

- All work on branch `handover-cleanup`; merge to `main` + push only in the final task after all gates pass.
- No git history rewrite. Deletions are ordinary commits.
- `vivado/ip/aclkgt_gt/` and `vivado/ip/gen_aclkgt_gt.tcl` MUST survive: the pipeline build reads `vivado/ip/aclkgt_gt/aclkgt_gt.xci`.
- Never use em dashes in any file written for this repo (project style rule).
- Deviation from the spec, resolved by the spec's own keep rule ("if the pipeline bitstream, the capture stack, or a kept test needs it, it stays"): `vivado/build.tcl` is the standalone uart_echo demo builder, not shared infrastructure, so it is DELETED and `hw.ps1`/`hw.sh` defaults retarget to `build_aclk_pipeline.tcl`. `constraints/kr260.xdc` (uart_echo's constraints) goes with it. `resources/Aclk/` + `resources/Tclk/` (ISD/TCLK reference PDFs) are KEPT (referenced by kept docs); only the EECS 151 ILA course PDF is deleted.
- `docs/superpowers/` (including this plan and the spec) is deleted in the LAST commit of Task 8, after everything else is done, because execution reads this file.
- Repo files use LF; the "LF will be replaced by CRLF" git warning on Windows is normal and ignorable.

---

### Task 1: Loose ends (commit before any pruning)

**Files:**
- Modify: `deploy/supercycle_plot.py` (already modified in the working tree; just commit it)
- Modify: `.gitignore` (fix the supercycle figure pattern)
- Delete: `aclkgt-hardware-facts.md` (repo root; byte-identical duplicate of `docs/aclkgt-hardware-facts.md`)
- Add: `docs/Kerberos_Tutorial_V0_3.pdf` (already on disk, untracked)

**Interfaces:**
- Consumes: nothing.
- Produces: a clean working tree (`git status --short` empty except intentionally untracked artifacts) so every later prune commit is pure deletion.

- [ ] **Step 1: Commit the supercycle_plot polish**

The working tree already contains the finished change (poster font sizing keyed off a `title_y`/`sub_y`/`ax_hist`/`ax_raster` size dict, `$XX` event-code notation in `_hex`, `_solid_legend` alpha fix, `sub_text` override). Do not edit it; just commit it:

```powershell
git add deploy/supercycle_plot.py
git commit -m "feat(analysis): poster rev0.2 sizing, `$XX event notation, solid legend swatches"
```

Expected: 1 file changed, ~42 insertions, ~22 deletions.

- [ ] **Step 2: Fix the .gitignore figure pattern**

In `.gitignore`, the block at the end reads:

```
# capture-analysis artifacts (data pulled from the board + generated figures)
deploy/events-*.csv
deploy/overnight-*.csv
deploy/supercycle_*.svg
```

Replace the last line so it matches the figures the tool actually writes (`deploy/sc_1F_hist.svg` etc.):

```
deploy/sc_*.svg
deploy/sc_*.png
```

- [ ] **Step 3: Verify the pattern works**

```powershell
git status --short
```

Expected: NO `deploy/sc_*.svg` lines appear as untracked anymore. Remaining untracked: `aclkgt-hardware-facts.md`, `docs/Kerberos_Tutorial_V0_3.pdf` (both handled next).

- [ ] **Step 4: Delete the root duplicate facts file, track the Kerberos PDF**

First re-verify the duplicate is still byte-identical, then delete:

```powershell
fc.exe /b aclkgt-hardware-facts.md docs\aclkgt-hardware-facts.md
Remove-Item aclkgt-hardware-facts.md -Confirm:$false
git add docs/Kerberos_Tutorial_V0_3.pdf .gitignore
```

Expected from `fc.exe`: "FC: no differences encountered". (If it differs, STOP and diff them; do not delete.)

- [ ] **Step 5: Commit and confirm clean tree**

```powershell
git commit -m "chore(repo): fix figure ignore pattern, drop duplicate facts file, track Kerberos tutorial"
git status --short
```

Expected: empty output from `git status --short`.

---

### Task 2: Prune RTL, build scripts, and constraints

**Files:**
- Delete (rtl root): `rtl/uart_echo_bd_top.v`, `rtl/uart_echo_top.sv`, `rtl/uart_receiver.sv`, `rtl/uart_transmitter.sv`, `rtl/fifo.sv`, `rtl/pin_blink.v`, `rtl/pl_heartbeat.v`, `rtl/button_parser.sv`, `rtl/debouncer.sv`, `rtl/edge_detector.sv`, `rtl/counter.sv`, `rtl/global_timebase.v`, `rtl/aclk_gen_bd_top.v`, `rtl/aclk_gt_gen_bd_top.v`, `rtl/aclk_gt_loop_bd_top.v`, `rtl/aclk_gt_rx_bd_top.v`, `rtl/aclk_gt_selftest_bd_top.v`, `rtl/aclk_readout_bd_top.v`, `rtl/clk_readout_bd_top.v`, `rtl/tclk_readout_bd_top.v`
- Delete (rtl/aclk_lite): `aclk_lite_decoder.sv`, `aclk_lite_gen_timeline.sv`, `aclk_lite_readout_top.sv`, `clk_byte_framer.sv`, `clk_rcv.sv`, `clk_readout_top.sv`
- Delete (rtl/aclk_gt): `aclk_gt_frame_gen.v`
- Delete (vivado): `build.tcl`, `uart_echo_bd.tcl`, `build_tclk.tcl`, `build_clk.tcl`, `build_aclk.tcl`, `build_aclkgen.tcl`, `build_pinblink.tcl`, `build_pltest.tcl`, `build_aclkgt_gen.tcl`, `build_aclkgt_loop.tcl`, `build_aclkgt_rx.tcl`, `build_aclkgt_selftest.tcl`, `vivado/ip/ila_gt/` (whole dir)
- Delete (constraints): `kr260.xdc`, `kr260_aclk.xdc`, `kr260_aclkgen.xdc`, `kr260_aclkgt.xdc`, `kr260_aclkgt_rx.xdc`, `kr260_clk.xdc`, `kr260_pinblink.xdc`, `kr260_tclk.xdc`
- Modify: `hw.ps1` (default `-Name`/`-Tcl` and usage examples), `hw.sh` (default build tcl), `vivado/README.md` and `constraints/README.md` (strip references to deleted targets)

**Keep untouched (the pipeline closure, verified against the build script during planning):** `rtl/aclk_pipeline_bd_top.v`, `rtl/aclk_lite_bridge.v`, `rtl/synchronizer.sv`, `rtl/async_fifo.sv`, `rtl/cdc_gray_count.sv`, `rtl/cdc_word_pulse.sv`, `rtl/wr_timebase.sv`, `rtl/wr_timebase_axi.sv`, all 7 files in `rtl/aclk_bridge/` (note: `ACLK_REV.v` contains `module ACLK_RCV`, it is load-bearing), `rtl/aclk_readout/aclk_readout_core.sv`, `rtl/aclk_readout/aclk_readout_axi.sv`, `rtl/aclk_lite/tclk_readout_top.sv`, `rtl/aclk_lite/aclk_lite_encoder.sv`, `rtl/aclk_gt/aclk_gt_readout_top.sv`, `rtl/aclk_gt/aclk_tclk_encoder.v`, `vivado/build_aclk_pipeline.tcl`, `vivado/ip/aclkgt_gt/`, `vivado/ip/gen_aclkgt_gt.tcl`, `constraints/kr260_aclk_pipeline.xdc`.

**Interfaces:**
- Consumes: clean tree from Task 1.
- Produces: an rtl/vivado/constraints tree containing only the pipeline target; `hw.ps1 build` with no arguments builds the pipeline. Task 7's Vivado gate proves this closed.

- [ ] **Step 1: Delete the RTL files**

```powershell
git rm rtl/uart_echo_bd_top.v rtl/uart_echo_top.sv rtl/uart_receiver.sv rtl/uart_transmitter.sv rtl/fifo.sv rtl/pin_blink.v rtl/pl_heartbeat.v rtl/button_parser.sv rtl/debouncer.sv rtl/edge_detector.sv rtl/counter.sv rtl/global_timebase.v rtl/aclk_gen_bd_top.v rtl/aclk_gt_gen_bd_top.v rtl/aclk_gt_loop_bd_top.v rtl/aclk_gt_rx_bd_top.v rtl/aclk_gt_selftest_bd_top.v rtl/aclk_readout_bd_top.v rtl/clk_readout_bd_top.v rtl/tclk_readout_bd_top.v
git rm rtl/aclk_lite/aclk_lite_decoder.sv rtl/aclk_lite/aclk_lite_gen_timeline.sv rtl/aclk_lite/aclk_lite_readout_top.sv rtl/aclk_lite/clk_byte_framer.sv rtl/aclk_lite/clk_rcv.sv rtl/aclk_lite/clk_readout_top.sv
git rm rtl/aclk_gt/aclk_gt_frame_gen.v
```

- [ ] **Step 2: Delete build scripts, ILA IP, and constraints**

```powershell
git rm vivado/build.tcl vivado/uart_echo_bd.tcl vivado/build_tclk.tcl vivado/build_clk.tcl vivado/build_aclk.tcl vivado/build_aclkgen.tcl vivado/build_pinblink.tcl vivado/build_pltest.tcl vivado/build_aclkgt_gen.tcl vivado/build_aclkgt_loop.tcl vivado/build_aclkgt_rx.tcl vivado/build_aclkgt_selftest.tcl
git rm -r vivado/ip/ila_gt
git rm constraints/kr260.xdc constraints/kr260_aclk.xdc constraints/kr260_aclkgen.xdc constraints/kr260_aclkgt.xdc constraints/kr260_aclkgt_rx.xdc constraints/kr260_clk.xdc constraints/kr260_pinblink.xdc constraints/kr260_tclk.xdc
```

Note: if `git rm -r vivado/ip/ila_gt` reports some files untracked (generated products are gitignored), remove the leftovers from disk with `Remove-Item -Recurse -Force vivado\ip\ila_gt`.

- [ ] **Step 3: Retarget hw.ps1 / hw.sh defaults to the pipeline**

In `hw.ps1`:
- Change the parameter default `[string]$Name = "uart_echo"` to `[string]$Name = "aclk_pipeline"`.
- Find where the default TCL path is set (search for `build.tcl`) and point it at `vivado\build_aclk_pipeline.tcl`.
- Update the comment-block usage examples (lines mentioning `build_tclk.tcl`, `build_pinblink.tcl`, `-Name tclk`, `-Name pinblink`) to a single pipeline example:

```powershell
#   .\hw.ps1 build                                  # pipeline bitstream + bootgen + hash
#   .\hw.ps1 deploy -DeployHost ubuntu@aclk-timestamper.fnal.gov
```

In `hw.sh`: change `BUILD_TCL="$ROOT/vivado/build.tcl"` to `BUILD_TCL="$ROOT/vivado/build_aclk_pipeline.tcl"` and update its usage comment the same way.

- [ ] **Step 4: Strip deleted-target references from vivado/README.md and constraints/README.md**

Open each; delete rows/sections describing removed targets (tclk, clk, aclk standalone, aclkgen, pinblink, pltest, uart_echo, aclkgt_*). What remains should describe only `build_aclk_pipeline.tcl` / `kr260_aclk_pipeline.xdc` and the shared conventions (KRIA_BUILD_DIR, space-free path warning).

- [ ] **Step 5: Verify no dangling references in the kept build flow**

```powershell
Select-String -Path vivado\build_aclk_pipeline.tcl, hw.ps1, hw.sh, constraints\kr260_aclk_pipeline.xdc -Pattern 'uart_echo|pinblink|pltest|build_tclk|build_clk\.|build_aclk\.|aclkgen|aclkgt_gen|aclkgt_loop|aclkgt_rx|aclkgt_selftest|ila_gt|global_timebase|clk_byte_framer|clk_rcv|frame_gen'
```

Expected: no output EXCEPT hits inside `build_aclk_pipeline.tcl` that are comments or the `design_name`/wrapper naming (the historical shared design name is `uart_echo_bd`; if the build tcl still uses that internal name, LEAVE IT: renaming the BD would change the wrapper/bitstream name the deploy docs rely on. Comment-only mentions are fine). Any hit that is an actual `file join`/source reference to a deleted file must be investigated before proceeding.

- [ ] **Step 6: Commit**

```powershell
git commit -m "chore(prune): remove non-pipeline RTL, build targets, and constraints; hw scripts default to aclk_pipeline"
```

---

### Task 3: Prune testbenches, run the sim sweep (verification gate 1)

**Files:**
- Delete (tb dirs): `tb/aclk_gen_bd_top/`, `tb/aclk_lite_decoder/`, `tb/aclk_lite_gen_loopback/`, `tb/aclk_lite_readout/`, `tb/aclkgt_gen/`, `tb/aclkgt_gen_loop/`, `tb/button_parser/`, `tb/clk_rcv/`, `tb/clk_readout/`, `tb/counter/`, `tb/debouncer/`, `tb/edge_detector/`, `tb/fifo/`, `tb/global_timebase/`, `tb/uart_echo_top/`, `tb/uart_receiver/`, `tb/uart_transmitter/`
- Delete (tb helpers): `tb/manchester_tx_model.py` (only the deleted `aclk_lite_decoder`/`aclk_lite_readout` benches used it; verified during planning)
- Keep: `tb/aclk_lite_bridge/`, `tb/aclk_lite_encoder/`, `tb/aclk_pipeline_chain/`, `tb/aclk_rcv/`, `tb/aclk_readout/`, `tb/aclk_readout_axi/`, `tb/aclk_readout_ext_ts/`, `tb/aclk_tclk_encoder_loop/`, `tb/aclkgt_readout/`, `tb/async_fifo/`, `tb/cdc_word_pulse/`, `tb/synchronizer/`, `tb/tclk_rcv/`, `tb/tclk_readout/`, `tb/wr_timebase/`, `tb/wr_timebase_axi/`, and helpers `runner_common.py`, `cocotb_helpers.py`, `axi_lite_bfm.py`, `plot_util.py`, `aclk_tx_model.py`, `clk_tx_model.py`, `tclk_tx_model.py`, `wr_model.py`

**Interfaces:**
- Consumes: pruned RTL tree from Task 2 (kept benches must only reference kept RTL).
- Produces: `sim.ps1 list` shows exactly the 16 kept modules; the full sweep passes.

- [ ] **Step 1: Delete the tb directories and the orphaned model**

```powershell
git rm -r tb/aclk_gen_bd_top tb/aclk_lite_decoder tb/aclk_lite_gen_loopback tb/aclk_lite_readout tb/aclkgt_gen tb/aclkgt_gen_loop tb/button_parser tb/clk_rcv tb/clk_readout tb/counter tb/debouncer tb/edge_detector tb/fifo tb/global_timebase tb/uart_echo_top tb/uart_receiver tb/uart_transmitter
git rm tb/manchester_tx_model.py
```

(Same note as Task 2 about gitignored leftovers, e.g. `__pycache__`: clean with `Remove-Item -Recurse -Force` if the dirs remain on disk.)

- [ ] **Step 2: Check kept benches import nothing deleted**

```powershell
Select-String -Path tb\*\*.py, tb\*.py -Pattern 'manchester_tx_model|uart_|button_parser|debouncer|edge_detector|global_timebase' | Where-Object { $_.Path -notmatch 'sim_build|__pycache__' }
```

Expected: no output. Any hit means a kept bench depends on something deleted; restore that file (`git checkout HEAD~1 -- <path>`) and note it.

- [ ] **Step 3: Run the full sim sweep (gate 1)**

```powershell
$modules = @('async_fifo','synchronizer','cdc_word_pulse','wr_timebase','wr_timebase_axi','tclk_rcv','aclk_rcv','aclk_lite_encoder','aclk_lite_bridge','aclk_readout','aclk_readout_axi','aclk_readout_ext_ts','aclk_tclk_encoder_loop','aclkgt_readout','tclk_readout','aclk_pipeline_chain')
$failed = @()
foreach ($m in $modules) { .\sim.ps1 run -Module $m; if ($LASTEXITCODE -ne 0) { $failed += $m } }
"FAILED: $($failed -join ', ')"
```

Expected: `FAILED: ` (empty list). If a module fails, diagnose before proceeding (most likely cause: a bench referenced a deleted RTL file in its runner's source list). Note: some benches emit matplotlib plots on completion; that is normal.

- [ ] **Step 4: Commit**

```powershell
git commit -m "chore(prune): remove testbenches of deleted RTL; kept sim sweep passes (16 modules)"
```

---

### Task 4: Prune deploy/, run the deploy tests (verification gate 3, first pass)

**Files:**
- Delete: `deploy/uart_echo_test.py`, `deploy/uart_echo.bif`, `deploy/uart_echo.dts`, `deploy/pltest.py`, `deploy/probe.py`, `deploy/clk_read.py`, `deploy/aclkgt_read.py`, `deploy/aclkgt_monitor.py`, `deploy/aclkgt_sweep.py`, `deploy/aclkgt.md`, `deploy/pinblink.md`
- Defer to Task 6 (content folds into the runbook first): `deploy/tclk.md`, `deploy/clk.md`, `deploy/aclk.md`
- Keep: everything else in deploy/ (capture stack, `aclk_pipeline.dts`, `run_pipeline.sh`, `shell.json`, `template.bif`, `redis-kr260.conf`, `requirements-board.txt`, `diag.py`, `tclk_read.py`, `aclk_read.py`, `wr_time.py`, analysis tools, all 9 `test_*.py`)

**Interfaces:**
- Consumes: nothing from Tasks 2-3 (deploy is independent of RTL pruning).
- Produces: a deploy/ tree whose every script serves the pipeline; pytest green.

- [ ] **Step 1: Delete**

```powershell
git rm deploy/uart_echo_test.py deploy/uart_echo.bif deploy/uart_echo.dts deploy/pltest.py deploy/probe.py deploy/clk_read.py deploy/aclkgt_read.py deploy/aclkgt_monitor.py deploy/aclkgt_sweep.py deploy/aclkgt.md deploy/pinblink.md
```

- [ ] **Step 2: Check kept deploy code imports nothing deleted**

```powershell
Select-String -Path deploy\*.py, deploy\run_pipeline.sh -Pattern 'clk_read(?!out)|probe|pltest|uart_echo|aclkgt_' | Where-Object { $_.Line -notmatch '^\s*#' }
```

Expected: no output (comment-only hits are fine; `tclk_read`/`readout` are excluded by the pattern but eyeball any hits before dismissing).

- [ ] **Step 3: Run the deploy test suite**

```powershell
.venv\Scripts\python.exe -m pytest deploy -q
```

(If `.venv` does not exist yet on this machine, run `.\sim.ps1 setup` first.)
Expected: all tests pass, 0 failures. These 9 test files all target kept modules, so no test deletions are expected; if pytest errors on a missing module, a deletion broke an import and must be fixed (restore or patch).

- [ ] **Step 4: Commit**

```powershell
git commit -m "chore(prune): remove non-pipeline deploy scripts and docs; deploy pytest green"
```

---

### Task 5: Write the operator runbook `docs/OPERATIONS.md`

**Files:**
- Create: `docs/OPERATIONS.md`
- Read as sources (do not modify yet): `deploy/README.md`, `deploy/capture.md`, `deploy/wr.md`, `deploy/redis.md`, `deploy/tclk.md`, `deploy/aclk.md`, `deploy/clk.md`, `docs/generated/tclk-aclk-pipeline-hardware-interface-guide.md`

**Interfaces:**
- Consumes: the surviving deploy docs (still on disk; tclk/clk/aclk.md not yet deleted, so their foldable content is available).
- Produces: `docs/OPERATIONS.md`, the single operator entry point that Task 6's rewritten README links to.

- [ ] **Step 1: Write docs/OPERATIONS.md**

Structure and content sources (pull the exact commands from the named sections; do not invent commands):

```markdown
# KR260 pipeline operations runbook

One page for the person running this system. Architecture background:
docs/PROJECT.md. Register-level details:
docs/generated/tclk-aclk-pipeline-hardware-interface-guide.pdf.

## 1. What you are operating
[3-sentence recap: TCLK + WR PPS/10MHz in on Pmod pins, decode + timestamp,
TCLK events to the PS, ACLK broadcast + received over the SFP fiber loop,
decoded + timestamped, ACLK-Lite out on Pmod B10. Events publish to Redis
Streams on the board.]

## 2. One-time setup
### Accounts, network, Kerberos
[From deploy/README.md section "Copying files to aclk-timestamper": kinit,
klist, pscp (PuTTY, GSSAPI) laptop->board and board->laptop commands.
Point at docs/Kerberos_Tutorial_V0_3.pdf.]
### Board prerequisites
[From deploy/redis.md "One-time setup on the board": redis-server >= 7.0
requirement, redis-kr260.conf install, requirements-board.txt pip install.
From deploy/wr.md "Sync": chrony/systemd-timesyncd NTP requirement.]

## 3. Build the bitstream (PC, Vivado 2024.2)
[.\hw.ps1 build  (now defaults to the pipeline). KRIA_BUILD_DIR override and
the no-spaces-in-path warning from hw.ps1's comments. Where the .bit.bin +
hash land, from deploy/README.md "Build (PC)" and "Verifying the load
matches your build".]

## 4. Load on the board
[From deploy/README.md "Load on the board (UIO + overlay, preferred)" plus
deploy/tclk.md section 3 and deploy/wr.md "Load": dtbo from
aclk_pipeline.dts, shell.json, xmutil/fpgautil commands, and the
`grep . /sys/class/uio/uio*/name` check listing tclk_readout /
aclk_readout / wr_timebase.]

## 5. Wiring
[From deploy/tclk.md "Wiring" (TCLK pin) + deploy/wr.md "Wiring (Pmod 1)"
(10 MHz + PPS pins) + the ACLK-Lite output pin from deploy/aclk.md
"Input"/pin notes. State the WR PPS width >= 100 ns requirement here.]

## 6. Run a capture
[From deploy/capture.md sections 1-3 verbatim where possible: wr_time.py arm,
run_pipeline.sh launch under tmux/nohup, ARCHIVE env var, stop procedure.]

## 7. Monitor and verify
[From deploy/capture.md section 4 (stats_report.py error check) and
deploy/redis.md "Verify": XLEN/XRANGE checks, status/watchdog keys,
stats-tclk.jsonl / stats-aclk.jsonl reconciliation meaning
(published / missed / failed-CRC).]

## 8. Get the data out
[From deploy/redis.md (KR260: namespace, event-time stream IDs, per-code
index hashes) + deploy/capture.md section 5 (pull CSVs, plot_stats.py,
supercycle_plot.py examples).]

## 9. Known failure modes
[Write these from the repo's operational history; keep each to 2-4 lines:
- WR PPS narrower than ~100 ns is invisible to the 40 MHz sampler: the
  timebase never arms. Fix at the source or stretch the pulse.
- Never write PL registers via Python mmap slice assignment: glibc memcpy
  can issue two AXI stores and double-pop the event FIFO. Always use the
  single-store pulse()/write helpers in readout_common.py.
- Reader FIFO overflow: symptoms in stats (missed > 0), cause is a stalled
  reader; run_pipeline.sh's restart loops are the mitigation. Restart the
  publisher window, do not power-cycle first.
- Redis older than 7.0 rejects the <ms>-* stream ID syntax the publisher
  uses; upgrade Redis, do not patch the publisher.
- AXI reads that alias every 16 bytes were the historical "register reads 0"
  trap; registers are spaced 16 bytes apart on purpose. Keep any new
  registers on that grid.
- wr_time.py guard: the strict timebase unlocks permanently on any PPS/10MHz
  dropout, and the guard window restarts it; a capture with an unlocked
  timebase is invalid, check stats_report before trusting data.]

## 10. When something else breaks
[diag.py usage line; sim.ps1 sweep as the regression net after RTL edits;
pytest deploy for the software; pointer to the register map PDF.]
```

Every bracketed block must be replaced with real prose/commands pulled from the named source sections. No placeholders may remain.

- [ ] **Step 2: Verify every command in the runbook exists**

For each script the runbook invokes, confirm the file exists in the tree (`tclk_read.py`, `aclk_read.py`, `wr_time.py`, `redis_publish.py`, `run_pipeline.sh`, `stats_report.py`, `stream_archive.py`, `plot_stats.py`, `supercycle_plot.py`, `diag.py`):

```powershell
Select-String -Path docs\OPERATIONS.md -Pattern '\b[a-z_]+\.(py|sh)\b' -AllMatches | ForEach-Object { $_.Matches.Value } | Sort-Object -Unique | ForEach-Object { if (-not (Test-Path "deploy\$_")) { "MISSING: $_" } }
```

Expected: no `MISSING:` lines (ignore false hits on non-deploy names like `build.tcl`; only deploy scripts matter here).

- [ ] **Step 3: Commit**

```powershell
git add docs/OPERATIONS.md
git commit -m "docs(operations): task-oriented operator runbook for the pipeline"
```

---

### Task 6: Docs prune and rewrite

**Files:**
- Delete: `docs/FUNCTIONALITY.md`, `docs/aclkgt-handoff.md`, `docs/aclkgt-hardware-facts.md`, `docs/aclk_readout_workflow.excalidraw`, `docs/aclk_readout_workflow.png`, `deploy/tclk.md`, `deploy/clk.md`, `deploy/aclk.md`, `resources/ILA Setup Guide _ EECS 151 FPGA Project.pdf`
- Modify (rewrite): `README.md`, `docs/PROJECT.md`
- Modify (trim): `deploy/README.md`, `deploy/capture.md`, `deploy/wr.md`, `deploy/redis.md`
- Keep: `docs/aclk-lite-framing.md`, `docs/generated/` (md + pdf), `docs/Kerberos_Tutorial_V0_3.pdf`, `resources/Aclk/`, `resources/Tclk/`

**Interfaces:**
- Consumes: `docs/OPERATIONS.md` from Task 5 (README links to it; per-target md content already folded in).
- Produces: a docs set that references only kept files; Task 7 link-checks it.

- [ ] **Step 1: Verify the workflow diagram is safe to delete**

```powershell
Select-String -Path README.md, docs\*.md, deploy\*.md -Pattern 'aclk_readout_workflow'
```

Expected: hits only in files being rewritten this task (or none). If a kept-as-is file references it, remove that reference during the trim step.

- [ ] **Step 2: Delete**

```powershell
git rm docs/FUNCTIONALITY.md docs/aclkgt-handoff.md docs/aclkgt-hardware-facts.md docs/aclk_readout_workflow.excalidraw docs/aclk_readout_workflow.png deploy/tclk.md deploy/clk.md deploy/aclk.md "resources/ILA Setup Guide _ EECS 151 FPGA Project.pdf"
```

- [ ] **Step 3: Rewrite README.md**

Keep the existing README's strong parts (title, the one-paragraph what-it-is, the signal-chain diagram, which already describe the pipeline) and change:
- The "New here?" callout points operators at `docs/OPERATIONS.md` FIRST, architecture readers at `docs/PROJECT.md`.
- The build/quickstart section shows only: `.\sim.ps1 setup`, `.\sim.ps1 run -Module aclk_pipeline_chain`, `.\hw.ps1 build`, then "see docs/OPERATIONS.md".
- Delete any table/list rows for removed targets (uart_echo, pinblink, standalone tclk/clk/aclk, aclkgen, aclkgt_*).
- Status section: one line, "hardware-validated in a 15.6 h dual-source capture (5.55 M events/source, zero loss), 2026-07-16".

- [ ] **Step 4: Rewrite docs/PROJECT.md**

Rewrite to describe only the pipeline: the signal chain (TCLK/WR in, tclk_readout_top decode + timestamp, aclk_tclk_encoder to the GT, aclkgt_gt SFP loop, aclk_gt_readout_top decode, aclk_readout_axi register blocks, wr_timebase(+_axi) timebase, aclk_lite_bridge + aclk_lite_encoder output), the build-target map reduced to `build_aclk_pipeline.tcl`, the module-to-file table for the kept RTL only, what is HW-verified (day-long capture) vs sim-only, and pointers to `docs/aclk-lite-framing.md` + the generated interface guide. Preserve any pinout tables that survive (or move them into OPERATIONS.md section 5 and link).

- [ ] **Step 5: Trim the surviving deploy docs**

In `deploy/README.md`, `deploy/capture.md`, `deploy/wr.md`, `deploy/redis.md`: delete sections about removed targets (deploy/README.md's "Example: uart_echo (original demo)" section goes; any `clk_read.py`, `probe.py`, `aclkgt_*` mentions go), and fix references to `tclk.md`/`clk.md`/`aclk.md` to point at `docs/OPERATIONS.md` instead.

- [ ] **Step 6: Commit**

```powershell
git add -A
git commit -m "docs(handover): pipeline-only README + PROJECT, prune historical and per-target docs"
```

---

### Task 7: Full verification gates

**Files:** none created; fixes only if a gate fails.

**Interfaces:**
- Consumes: the fully pruned tree.
- Produces: evidence that the handover tree is self-consistent (sim green, pytest green, bitstream builds, links resolve). Do not proceed to Task 8 until ALL four pass.

- [ ] **Step 1: Link check over kept markdown**

```powershell
.venv\Scripts\python.exe -c "
import re, pathlib
bad = []
for md in pathlib.Path('.').rglob('*.md'):
    s = str(md).replace('\\', '/')
    if any(x in s for x in ('sim_build', '.venv', 'poster/', 'build/', 'docs/superpowers')): continue
    text = md.read_text(encoding='utf-8', errors='replace')
    for m in re.finditer(r'\]\(([^)#h][^)#]*)', text):
        target = (md.parent / m.group(1).strip()).resolve()
        if not target.exists(): bad.append(f'{md}: {m.group(1)}')
print('\n'.join(bad) if bad else 'ALL LINKS OK')
"
```

Expected: `ALL LINKS OK`. Fix any broken link in place (docs/superpowers is excluded: it is deleted in Task 8).

- [ ] **Step 2: Grep for stragglers**

```powershell
Select-String -Path README.md, docs\*.md, deploy\*.md, deploy\*.py, deploy\*.sh, tb\*\*.py, tb\*.py, vivado\*.tcl, hw.ps1, hw.sh, sim.ps1, sim.sh -Pattern 'uart_echo|pinblink|pltest|button_parser|aclkgt_gen|aclkgt_loop|aclkgt_rx\b|aclkgt_selftest|aclkgt_monitor|aclkgt_sweep|clk_byte_framer|aclk_lite_decoder|aclk_lite_readout_top|global_timebase|FUNCTIONALITY' | Where-Object { $_.Path -notmatch '__pycache__|sim_build' }
```

Expected: only benign hits (the internal Vivado `design_name uart_echo_bd` in `build_aclk_pipeline.tcl` and its mention in docs where the wrapper/bitstream filename is explained; historical mentions inside commit-adjacent prose are fine if the file they point to exists). Every other hit gets fixed.

- [ ] **Step 3: Sim sweep (gate 1, full rerun)**

Run the same 16-module loop as Task 3 Step 3. Expected: `FAILED: ` (empty).

- [ ] **Step 4: Deploy pytest (gate 3, full rerun)**

```powershell
.venv\Scripts\python.exe -m pytest deploy -q
```

Expected: all pass.

- [ ] **Step 5: Vivado bitstream build (gate 2, the big one)**

```powershell
.\hw.ps1 build
```

This now defaults to `vivado\build_aclk_pipeline.tcl` / `-Name aclk_pipeline` (Task 2). Runtime is tens of minutes; run it in the background and check the result. Expected: completes with a bitstream + `.bit.bin` under `build\kria\aclk_pipeline\` and a printed hash, zero synthesis errors. THIS is the proof that no needed RTL/constraint/IP file was deleted. If it fails on a missing file, `git checkout <commit> -- <path>` the file back, re-run, and record the correction.

- [ ] **Step 6: Commit any fixes**

```powershell
git status --short
git add -A
git commit -m "fix(handover): corrections surfaced by verification gates"
```

(Skip the commit if the tree is clean because all gates passed first try.)

---

### Task 8: Hygiene finish and merge

**Files:**
- Modify: `.gitignore` (drop entries that only served deleted paths)
- Delete: `docs/superpowers/` (specs + plans, including this plan)
- Branches: delete `aclkgt-readout` and `tclk-aclk-pipeline` locally and on origin; merge `handover-cleanup` into `main`; push.

**Interfaces:**
- Consumes: all gates green from Task 7.
- Produces: `main` = the handover tree, pushed; no stale branches.

- [ ] **Step 1: Trim .gitignore**

Remove these entries (their targets no longer exist in the tree or served deleted flows): `gt_configuration/`, `rtl/Li_Files/`, `poster/`, `.superpowers/`, and any `vivado/ip/` pattern that names only `ila_gt`. KEEP: the `vivado/ip/aclkgt_gt/` patterns (IP still present), `/build/`, sim/vivado artifact patterns, the deploy csv/figure patterns, the `.vscode` block, `project_1//*.xpr` (still guards against GUI accidents). Note: `gt_configuration/`, `rtl/Li_Files/`, and `poster/` still exist ON DISK as untracked dirs; removing their ignore lines would make them appear in `git status`, so KEEP those three lines if the dirs are still on disk (check with `Test-Path`), and note in the commit message which were kept for that reason.

- [ ] **Step 2: Delete docs/superpowers**

```powershell
git rm -r docs/superpowers
git commit -am "chore(handover): drop process history and stale ignore entries"
```

- [ ] **Step 3: Delete the merged historical branches**

Both were verified fully merged into main during planning (`git log main..<branch>` is empty for both):

```powershell
git branch -d aclkgt-readout tclk-aclk-pipeline
git push origin --delete aclkgt-readout
```

(`tclk-aclk-pipeline` has no origin counterpart; only `origin/aclkgt-readout` exists. If `git branch -d` refuses, STOP and check `git log main..<branch>` rather than forcing with `-D`.)

- [ ] **Step 4: Merge to main and push**

```powershell
git checkout main
git merge --no-ff handover-cleanup -m "chore: handover cleanup (prune to the pipeline product + operator runbook)"
git push origin main
git branch -d handover-cleanup
```

Expected: fast-forward-style merge commit, push succeeds, `git status --short` clean.

- [ ] **Step 5: Final sanity**

```powershell
git branch -a
Get-ChildItem -Name
```

Expected: only `main` locally (plus origin/main), and the root listing shows: `README.md`, `constraints/`, `deploy/`, `docs/`, `hw.ps1`, `hw.sh`, `requirements.txt`, `resources/`, `rtl/`, `sim.ps1`, `sim.sh`, `tb/`, `vivado/` (plus untracked-on-disk `build/`, `sim_build/`, `poster/`, `gt_configuration/` which are ignored).
