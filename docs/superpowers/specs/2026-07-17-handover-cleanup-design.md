# Handover cleanup design

**Date:** 2026-07-17
**Goal:** Prepare the repo for handover to an operator/maintainer. Prune it down
to the single operational product, fix loose ends, and give the successor a
task-oriented runbook.

Note: this spec lives in `docs/superpowers/`, which is itself deleted in the
final prune commit. That is intentional; the spec stays reachable in git history.

## Context and decisions already made

- **Audience:** an operator/maintainer, not a developer. They run captures,
  keep the board alive, and make small fixes. Docs optimize for runbooks over
  design history.
- **Pruning policy:** prune hard. Deleted material survives only in git
  history (no history rewrite, no on-disk archive).
- **The kept product:** the single-board pipeline (`aclk_pipeline` target):
  TCLK/WR in on Pmod pins, decode + timestamp, TCLK events to the PS,
  ACLK broadcast over SFP, ACLK received over SFP, decode + timestamp,
  ACLK-Lite out on the Pmod pin — plus the deploy software that operates it
  (capture stack, Redis publishing, stats, analysis plots).
- **Loose work:** the uncommitted `deploy/supercycle_plot.py` polish is real
  improvement and gets committed first.
- Both feature branches (`aclkgt-readout`, `tclk-aclk-pipeline`) are already
  fully merged into main; main is pushed to origin.

Key discovery during design: the pipeline's SFP path is built on the
`aclkgt_gt` GT wizard IP (`vivado/build_aclk_pipeline.tcl` references
`vivado/ip/aclkgt_gt/aclkgt_gt.xci`). Pruning the standalone `aclkgt_*`
experiment targets must NOT delete `vivado/ip/aclkgt_gt/`.

## Execution shape

All work happens on a `handover-cleanup` branch as a series of small,
reviewable commits, merged to main (and pushed) only after the verification
gates pass.

## 1. Loose ends first (before any pruning)

1. Commit the `deploy/supercycle_plot.py` polish (poster font sizing, `$XX`
   event-code notation, legend alpha fix) as its own commit.
2. Fix `.gitignore`: the pattern `deploy/supercycle_*.svg` does not match the
   generated figures (named `deploy/sc_*.svg`); replace it with a pattern that
   does.
3. Delete root-level `aclkgt-hardware-facts.md` (byte-identical duplicate of
   `docs/aclkgt-hardware-facts.md`; the docs/ copy's own fate is decided in
   the prune step).
4. Track `docs/Kerberos_Tutorial_V0_3.pdf` (operators need Kerberos for board
   file transfer).

## 2. The prune

**Keep rule:** if the pipeline bitstream, the capture stack, or a kept test
needs it, it stays; otherwise it goes.

### Keep (anchored on vivado/build_aclk_pipeline.tcl's file list)

- **RTL:** `rtl/aclk_pipeline_bd_top.v` and every module the build script
  pulls in:
  - `rtl/aclk_bridge/`: `crc8_calc.v`, `GEARBOX_16_TO_96.v`, `ACLK_REV.v`,
    `gearbox_96_to_16.v`, `serdec4_9MHz.v`, `TCLK_DESERIALIZER2.v`,
    `TCLK_RCV.v`
  - `rtl/`: `synchronizer.sv`, `async_fifo.sv`, `cdc_gray_count.sv`,
    `cdc_word_pulse.sv`, `wr_timebase.sv`, `wr_timebase_axi.sv`,
    `aclk_lite_bridge.v`
  - `rtl/aclk_readout/`: `aclk_readout_core.sv`, `aclk_readout_axi.sv`
  - `rtl/aclk_lite/`: `tclk_readout_top.sv`, `aclk_lite_encoder.sv`
  - `rtl/aclk_gt/`: `aclk_gt_readout_top.sv`, `aclk_tclk_encoder.v`
  - plus anything these transitively instantiate (verified during the plan,
    not assumed).
- **Build flow:** `vivado/build.tcl`, `vivado/build_aclk_pipeline.tcl`,
  `vivado/ip/aclkgt_gt/`, `constraints/kr260_aclk_pipeline.xdc`,
  `hw.ps1` / `hw.sh` (trimmed to the pipeline target),
  `sim.ps1` / `sim.sh`, `requirements.txt`, `resources/` (if referenced by
  the kept flow; verified in the plan).
- **Deploy (operational):** `readout_common.py`, the manual readers that
  attach to the pipeline's UIO devices as diagnostics (`tclk_read.py`,
  `aclk_read.py`; `clk_read.py` belongs to the deleted standalone target and
  goes unless a kept doc or script depends on it), `wr_time.py`,
  `redis_publish.py`, `redis_sink.py`, `stats_log.py`, `stats_report.py`,
  `stream_archive.py`, `tclk_filter.py`, `run_pipeline.sh`,
  `aclk_pipeline.dts`, `redis-kr260.conf`, `shell.json`, `template.bif`,
  `requirements-board.txt`, `diag.py` (diagnostic).
- **Deploy (analysis):** `plot_stats.py`, `supercycle_plot.py`.
- **Deploy tests:** every `test_*.py` whose module is kept.
- **Testbenches:** every `tb/` directory exercising a kept RTL module, plus
  shared helpers (`runner_common.py`, `cocotb_helpers.py`, `axi_lite_bfm.py`,
  `plot_util.py`, the tx/wr models used by kept benches).
- **Docs:** `docs/aclk-lite-framing.md`, the generated pipeline
  hardware-interface guide (`docs/generated/`), `deploy/capture.md`,
  `deploy/wr.md`, `deploy/redis.md`, `docs/Kerberos_Tutorial_V0_3.pdf`,
  `README.md`, `docs/PROJECT.md` (both rewritten; see section 3).

### Delete (survives only in git history)

- **Bring-up demo RTL:** `uart_echo_bd_top.v`, `uart_echo_top.sv`,
  `uart_receiver.sv`, `uart_transmitter.sv`, `pin_blink.v`, `pl_heartbeat.v`,
  `button_parser.sv`, `debouncer.sv`, `edge_detector.sv`, `counter.sv`,
  `fifo.sv`, `global_timebase.v` — each first checked for use by a kept
  module; anything actually needed moves to the keep list.
- **Stepping-stone BD tops + build scripts:** `tclk_readout_bd_top.v`,
  `clk_readout_bd_top.v`, `aclk_readout_bd_top.v`, `aclk_gen_bd_top.v`,
  `build_tclk.tcl`, `build_clk.tcl`, `build_aclk.tcl`, `build_aclkgen.tcl`,
  `build_pinblink.tcl`, `build_pltest.tcl`, `uart_echo_bd.tcl`.
- **Standalone aclkgt experiment:** `aclk_gt_gen_bd_top.v`,
  `aclk_gt_loop_bd_top.v`, `aclk_gt_rx_bd_top.v`, `aclk_gt_selftest_bd_top.v`,
  their `build_aclkgt_*.tcl` scripts, unused `rtl/aclk_gt/` modules,
  `deploy/aclkgt_read.py`, `deploy/aclkgt_monitor.py`,
  `deploy/aclkgt_sweep.py`, `deploy/aclkgt.md`, `docs/aclkgt-handoff.md`,
  `docs/aclkgt-hardware-facts.md`. (`vivado/ip/aclkgt_gt/` STAYS.)
- **Unused files in kept directories:** `rtl/aclk_bridge/`, `rtl/aclk_lite/`,
  `rtl/aclk_readout/` members not in the build list and not transitively
  needed (verified in the plan).
- **Deploy scraps:** `uart_echo_test.py`, `uart_echo.bif`, `uart_echo.dts`,
  `pltest.py`, `probe.py`, `pinblink.md`, and the per-target docs `tclk.md`,
  `clk.md`, `aclk.md` (still-relevant content folds into the runbook first).
- **Process history:** `docs/superpowers/` entirely (including this spec),
  `docs/FUNCTIONALITY.md` (a 2026-07-02 refactor safety net inventorying
  targets this cleanup deletes; superseded by the rewritten PROJECT.md +
  OPERATIONS.md), `docs/aclk_readout_workflow.excalidraw` + `.png` if they
  describe pruned workflow rather than the pipeline (checked during the
  plan).
- **Testbenches of deleted RTL:** the corresponding `tb/` directories and any
  models only they used.

## 3. Documentation

- **New `docs/OPERATIONS.md`** — the operator runbook, task-organized:
  1. Board, network, and Kerberos setup (pointing at the Kerberos PDF and the
     pscp workflow).
  2. Building the bitstream (or using the last known-good) and what Vivado
     version/quirks to expect.
  3. Flashing: bitstream load, device-tree overlay, UIO devices.
  4. Starting a capture: `run_pipeline.sh`, wr_time guard, crash-restart
     behavior.
  5. Monitoring: stats logs, stats_report reconciliation, Redis streams,
     status/watchdog keys.
  6. Reading the data: Redis conventions (KR260: namespace, event-time IDs),
     stream_archive, plot_stats, supercycle_plot.
  7. Known failure modes, distilled from operational history: WR PPS width
     >= 100 ns requirement, the never-slice-write-a-sensitive-register rule
     (POP double-pop), FIFO overflow symptoms, AXI 16-byte aliasing history,
     Redis >= 7.0 requirement.
- **README.md** — rewritten to describe only the pipeline; points at
  OPERATIONS.md as the operator entry point and PROJECT.md for architecture.
- **docs/PROJECT.md** — rewritten: pipeline-only architecture, build-target
  map reduced to the one target, hardware-verified status.
- **Surviving deploy/*.md** — trimmed of references to deleted targets.
- **Link integrity:** no doc may reference a deleted file.

## 4. Verification gates (after the prune commits)

1. All kept cocotb testbenches pass (`sim.ps1` sweep).
2. The pipeline bitstream builds clean in Vivado from the pruned tree — the
   proof that no needed RTL was deleted.
3. All kept `deploy/test_*.py` pass.
4. Doc link check: every intra-repo link in kept markdown resolves.

## 5. Repo hygiene finish

- Delete merged branches `aclkgt-readout` and `tclk-aclk-pipeline` locally
  and on origin.
- Trim `.gitignore` entries that only served deleted paths (poster/,
  gt_configuration/, rtl/Li_Files/, aclkgt IP patterns for deleted targets —
  keeping the patterns `vivado/ip/aclkgt_gt/` still needs).
- Merge `handover-cleanup` to main and push.

## Out of scope

- No git history rewrite. Everything pruned remains one
  `git log --follow -- <path>` away.
- No new features, no RTL changes beyond deletion.
- No re-verification on hardware (the pipeline was already validated in the
  day-long capture run); gates are sim + build + unit tests + docs.
- The poster/ directory and other untracked-on-purpose material: already
  outside git; disk cleanup there is the user's call, not part of the repo
  handover.
