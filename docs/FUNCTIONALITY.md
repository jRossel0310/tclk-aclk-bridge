# Functionality inventory (refactor safety net)

Snapshot of everything the repo currently does, recorded 2026-07-02 before any
efficiency cleanup. Nothing listed here may be lost by a refactor. Status labels:
**HW-verified** (proven on the board), **sim-validated** (cocotb passes),
**scaffold** (bring-up utility, keep), **legacy-unused** (candidate for removal).

## 1. Build targets (vivado/*.tcl, driven by hw.ps1 / hw.sh)

All builds share `design_name = uart_echo_bd`, derive PL clocks from pl_clk0 with a
clk_wiz MMCM, tie proc_sys_reset `dcm_locked` high, honor `KRIA_BUILD_DIR`, and end
with synth + impl + bitstream. Every bitstream is `uart_echo_bd_wrapper.bit.bin`
(md5-check on the board to tell them apart).

| TCL | Top | What it builds | AXI | Status |
|-----|-----|----------------|-----|--------|
| build_clk.tcl | clk_readout_bd_top | Unified TCLK/ACLK-Lite receiver (serdec4_9MHz + clk_byte_framer + readout), H12 in, 80/40 MHz | 1 @ 0x8000_0000 | HW-verified (current) |
| build_aclkgen.tcl | aclk_gen_bd_top | ACLK-Lite generator (hardcoded trio timeline), H12 out, 80 MHz | none | HW-verified (current) |
| build_tclk.tcl | tclk_readout_bd_top | TCLK-only receiver (TCLK_RCV chain), H12 in, 80/40 MHz | 1 | HW-proven, superseded by clk |
| build_aclk.tcl | aclk_readout_bd_top | ACLK-Lite receiver via clean-room aclk_lite_decoder, 120 MHz | 1 | Superseded (reads only old generator) |
| build_aclkgt_gen.tcl | aclk_gt_gen_bd_top | Gigabit-ACLK GT/SFP transmitter (aclk_gt_frame_gen), 1.25 Gbps 8b10b | none | aclkgt project |
| build_aclkgt_rx.tcl | aclk_gt_rx_bd_top | Gigabit-ACLK GT/SFP receiver + GT_CTRL reg 0xF0 (polarity/loopback/reset) | 1 | aclkgt project |
| build_aclkgt_loop.tcl | aclk_gt_loop_bd_top | Single-board GT near-end PMA loopback (loopback_in=3'b010) | 1 | aclkgt milestone M0 |
| build_aclkgt_selftest.tcl | aclk_gt_selftest_bd_top | Same-board SFP fiber loop, TX sweep fields in GT_CTRL, RX recovery FSM, ILA | 1 | aclkgt self-test |
| build_aclk_pipeline.tcl | aclk_pipeline_bd_top | Integrated TCLK -> GT ACLK -> ACLK-Lite pipeline, two readouts, WR timebase (S_AXI3), H12 in / SFP loop / B10 out | 3 @ 0x8000_0000 + 0x8001_0000 + 0x8002_0000 | Pipeline project |
| build_pltest.tcl | (pl_heartbeat + AXI GPIO) | PL-alive smoke test | 1 GPIO | Scaffold |
| build_pinblink.tcl | (pin_blink + pl_heartbeat) | 0.5 Hz square wave on H12 (pin/bank verification) | 1 GPIO | Scaffold |
| build.tcl + uart_echo_bd.tcl | uart_echo_bd_top | Original UART echo loopback (PS UART Lite <-> PL echo) | UART Lite | Origin skeleton |

## 2. RTL

### Shared primitives (rtl/)
- **synchronizer.sv**: N-stage, N-bit CDC flop chain. Used by nearly everything.
- **async_fifo.sv**: dual-clock Cummings FIFO, Gray pointers, FWFT, sticky overflow.
- **cdc_gray_count.sv**: cross-domain monotonic counter (Gray CDC), used for all diagnostic counters and the timestamps.
- **fifo.sv**: single-clock FWFT FIFO (uart_echo path).
- **edge_detector.sv**, **debouncer.sv**, **button_parser.sv**: input conditioning chain (tb-covered; button_parser not in any current build).
- **counter.sv**: sim-skeleton smoke module; the default target of `sim run` and `sim new` workflow. Keep.
- **pin_blink.v**, **pl_heartbeat.v**: bring-up scaffolds used by build_pinblink/build_pltest.
- **global_timebase.v**: one 64-bit tick distributed bit-identically to two clock domains via two cdc_gray_count instances. No longer instantiated by the pipeline (see wr_timebase below); kept in the repo with its own suite for the standalone tick scheme.
- **uart_receiver.sv / uart_transmitter.sv / uart_echo_top.sv**: 8-N-1 UART echo (origin skeleton, HW-verified via AXI UART Lite loopback).

### wr_timebase / wr_timebase_axi / cdc_word_pulse (White Rabbit timestamps, pipeline build)

The integrated pipeline no longer uses `global_timebase`: `rtl/wr_timebase.sv`
replicas in `clk_40m` and `rx_usrclk2` (plus a monitor in `s_axi_aclk`) each
watch the WR 10 MHz (Pmod1 pin3, E10) and PPS (pin4, E12) and generate
`{sec[31:0], ns[31:0]}` in-domain: ns = 100 ns per 10 MHz edge since PPS plus a
local-clock interpolator cleared at every edge. STRICT validity: ts is 0 unless
armed seconds were loaded at a PPS and both watchdogs are alive; any loss (or a
GT relock, which resets the rx-domain replica) requires the PS to re-arm.
Seconds come from the NTP-synced PS clock via `deploy/wr_time.py arm`.
`rtl/cdc_word_pulse.sv` (toggle-handshake word CDC) carries the arm into each
domain. `rtl/wr_timebase_axi.sv` is the third AXI-Lite slave (S_AXI3 at
0x8002_0000, 16-byte stride): 0x00 STATUS (locked bits, aliveness, arm_pending,
lost_lock sticky), 0x10 SEC_ARM (RW, write arms), 0x20 SEC_NOW / 0x30 NS_NOW
(atomic pair: the SEC_NOW read latches NS_NOW), 0x40 PPS_COUNT, 0x50 CELLS_LAST
(expect 10,000,000), 0x60 CTRL ([0] clear sticky, [1] disarm). Bring-up runbook:
`deploy/wr.md`. Suites: `tb/wr_timebase`, `tb/wr_timebase_axi`,
`tb/cdc_word_pulse`, and the WR-converted `tb/aclk_pipeline_chain`.

### Timing receivers (rtl/aclk_lite/, rtl/aclk_readout/, rtl/aclk_bridge/)
- **aclk_bridge/serdec4_9MHz.v**: inherited HW-proven biphase-mark bit recovery (80 MHz oversample, emits SCLK/SDATA).
- **aclk_bridge/TCLK_RCV.v + TCLK_DESERIALIZER2.v**: inherited TCLK decoder (1-byte events).
- **aclk_bridge/ACLK_REV.v (ACLK_RCV) + GEARBOX_16_TO_96 + crc8_calc**: inherited gigabit-ACLK decoder (8b10b words -> 96-bit frames -> CRC check). Used by all aclkgt builds + pipeline.
- **aclk_bridge/gearbox_96_to_16.v**: TX-side gearbox used by the GT generators.
- **aclk_lite/clk_byte_framer.sv**: length-aware real-framing byte framer (1/2/12-byte frames, per-byte even parity, terminal-idle stop). The authoritative on-wire framing is docs/aclk-lite-framing.md.
- **aclk_lite/clk_rcv.sv**: serdec4_9MHz + clk_byte_framer = the unified decoder (HW-verified on real TCLK and board-to-board ACLK-Lite).
- **aclk_lite/clk_readout_top.sv / tclk_readout_top.sv / aclk_lite_readout_top.sv**: per-protocol adapter tops wiring a decoder to the shared readout; each adds a line-activity diagnostic counter. tclk_readout_top has a USE_EXT_TS parameter (external shared timestamp, default off).
- **aclk_lite/aclk_lite_decoder.sv**: clean-room Manchester decoder. Legacy; only decodes the old clean-room generator, kept for reference.
- **aclk_lite/aclk_lite_encoder.sv**: biphase-mark encoder emitting the real ISD framing (1/2/12-byte frames, 100 ns cells). Generator + pipeline mirror output.
- **aclk_lite/aclk_lite_gen_timeline.sv**: hardcoded generator timeline (TCLK 0x55, ACLK 0xABCD, packet 0x1234 + 0xDEADBEEFCAFE0001, frame_sync trigger, warm-up).
- **aclk_readout/aclk_readout_core.sv**: 64-bit timestamp (pps-clearable) + optional null-drop + 160-bit packing + async_fifo CDC.
- **aclk_readout/aclk_readout_axi.sv**: AXI4-Lite register block, registers spaced 16 bytes (KR260 LPD aliasing quirk). Map: 0x00 STATUS, 0x10 EVENT, 0x20/0x30 DATA_HI/LO, 0x40/0x50 TS_HI/LO, 0x60 POP(W), 0x70 EVENT_COUNT, 0x80 NULL_COUNT, 0x90 ERROR_COUNT, 0xA0 DEBUG, 0xB0 HEARTBEAT, 0xC0 LOCK, 0xD0 FILTER_CFG(W, 256-bit drop-mask), 0xE0 FILTERED_COUNT. Shared by ALL readout builds.

### Gigabit-ACLK path (rtl/aclk_gt/)
- **aclk_gt_frame_gen.v**: compiled-in GT frame generator ({0xBC, EVENT, DATA, CRC8} via gearbox_96_to_16); 3-entry hardcoded ROM.
- **aclk_gt_readout_top.sv**: ACLK_RCV -> adapter -> shared aclk_readout_axi.
- **aclk_tclk_encoder.v**: live TCLK-event -> gigabit-ACLK frame encoder (per-code count RAM, CDC toggle handshake); Icarus-simmable, used by the pipeline.

### BD tops (rtl/*_bd_top.v)
One plain-Verilog wrapper per build target (see table above). The GT tops each contain:
per-domain async-assert/sync-deassert reset chains, cdc_gray_count diagnostic
counters, a DEBUG word 0xA0 (per-build bit layout, decoded by aclkgt_read.py), and
(selftest + pipeline) a SEARCH/LOCKED/RECOVER byte-align recovery FSM with
LOSS_WINDOW=512 / RECOVER_LEN=512. sfp_tx_disable driven 0 (laser on).
aclk_pipeline_bd_top additionally instantiates aclk_lite_bridge (rx event ->
async_fifo -> aclk_lite_encoder mirror on B10) and the wr_timebase /
wr_timebase_axi trio (S_AXI3), replacing the earlier global_timebase instance
(see the wr_timebase module summary above).

### Removed 2026-07-02 (efficiency-cleanup branch)
- **rtl/Li_Files/**: byte-identical copy of rtl/aclk_bridge/, untracked and git-ignored. Deleted (it was never tracked, so there is no git history for it).
- **rtl/aclk_bridge/** unused members, deleted (history retrievable via git): BitEncoder.v, FrameEncoder.v, fake_data.v, lfsr80.v, TimelineGenerator.v, aclk_data_source.v, top_module.v, ack_stimulus_gen.v. (aclk_data_source.v was the reference for aclk_tclk_encoder.v, which remains.)

## 3. Testbenches (tb/, cocotb 2.0 + Icarus, run via sim.ps1 / sim.sh)

33 suites, one per module/chain; 10 have SV wrappers (tb_*.sv) for multi-clock DUTs;
19 emit matplotlib plots to sim_build/<module>/plots/. Shared models: tclk_tx_model.py
(biphase cells), clk_tx_model.py (real multi-byte framing), manchester_tx_model.py
(legacy clean-room), aclk_tx_model.py (GT frames + CRC), wr_model.py (WR 10 MHz/PPS
stimulus, used by wr_timebase / wr_timebase_axi / aclk_pipeline_chain), axi_lite_bfm.py
(AXI master), plot_util.py (4 plot helpers; save_line_plot added 2026-07-02, adopted by
the aclk_lite_encoder suite), runner_common.py (shared runner scaffold used by all 33
runners), cocotb_helpers.py (shared _b/start_clock helpers).

Coverage highlights (suite: what it proves):
- clk_rcv: unified decoder on mixed 1/2/12-byte frames + parity errors.
- clk_readout / tclk_readout / aclk_lite_readout / aclkgt_readout: decoder -> readout -> AXI chains (incl. error counts, event filter drop, debug activity).
- aclk_lite_encoder + aclk_lite_gen_loopback: generator waveform matches golden model; TX -> serdec -> framer round trip.
- aclk_rcv / aclkgt_gen / aclkgt_gen_loop: inherited GT decoder alignment, CRC error path, frame-gen vs model, gen -> rcv loop.
- aclk_readout / aclk_readout_axi / aclk_readout_ext_ts: FIFO integrity, AXI register map, external-timestamp option.
- aclk_lite_bridge: real events recovered, nulls suppressed, back-to-back events.
- aclk_pipeline_chain: full pure-RTL pipeline (TCLK in -> both readouts, WR timebase).
- wr_timebase / wr_timebase_axi / cdc_word_pulse: WR ns interpolation + PPS/sec rollover, STRICT lock/unlock and re-arm, S_AXI3 register map (incl. atomic SEC_NOW/NS_NOW pair), the arm toggle-handshake CDC.
- async_fifo / synchronizer / fifo / global_timebase: CDC primitives (backpressure, overflow latch, latency, shared monotonic ts).
- uart_receiver / uart_transmitter / uart_echo_top, counter, debouncer, edge_detector, button_parser: skeleton + input conditioning.
- aclk_gen_bd_top, aclk_tclk_encoder_loop: generator wrapper activity, encoder -> TCLK_RCV loop.

## 4. Driver scripts (repo root)

- **sim.ps1 / sim.sh** (functionally matched): `setup` (venv + requirements), `run -Module/-m <name> -Sim icarus|verilator`, `wave` (GTKWave on latest FST), `test` (run + wave), `new <name>` (scaffold rtl + tb), `list` (auto-discover tb/*/runner.py), `clean`, `help`. OSS_CAD_SUITE resolution via env var or PATH.
- **hw.ps1**: `build` (batch Vivado, 12-attempt antivirus-flake retry, runs from a space-free dir, then bootgen .bit -> .bit.bin, MD5/SHA256, build-manifest.json with git commit), `deploy` (scp bin + design-mapped readers via pyMap: tclk/aclk/clk -> their reader + tclk_filter.py, uart_echo -> uart_echo_test.py, plus aclkgt_loop/aclkgt_rx/aclkgt_selftest/aclk_pipeline entries, all mapped builds now also ship readout_common.py), `gui`, `clean`. Flags: -Tcl, -Name, -Vivado, -BuildRoot, -DeployHost.
- **hw.sh**: lite subset (build with 6 retries to .bit only, gui, clean; uart_echo default; no bootgen/deploy). Known feature drift vs hw.ps1.

## 5. Deploy (deploy/)

Readers (all: UIO/devmem mmap, watchdog thread that flags AXI hangs after 2 s,
startup register probe with heartbeat trust-check, 1 ms STATUS poll loop, POP-driven
event drain, 1 Hz stats line, --drop hardware filter config via tclk_filter.py):
- **clk_read.py**: unified receiver reader (25 ns tick), prints ts/dt/event/tclk/has_data + sig_err.
- **tclk_read.py**: TCLK reader (25 ns tick by default, --tick-ns override for non-WR builds, --wr to print WR sec:ns UTC timestamps for the pipeline build, tclk_edges stat).
- **aclk_read.py**: clean-room ACLK reader (8.33 ns tick).
- **aclkgt_read.py**: GT reader (16 ns tick, --tick-ns), plus GT_CTRL control: --gtctrl, --txdiff/--txpost/--txpre TX driver settings, --gtreset RX re-init, GT link-health decode of DEBUG 0xA0.
- **aclkgt_monitor.py**: long-run link endurance monitor (read-only, wrap-corrected counters, CSV log, HEALTHY/MARGINAL/UNSTABLE verdict, --interval/--report).
- **aclkgt_sweep.py**: TX driver sweep (applies combos via GT_CTRL, dwell + sample, ranks by alignment/events/disperr, prints best).
- **tclk_filter.py** (+ test_tclk_filter.py): pure drop-mask helpers parse_drop_codes / filter_cfg_word, unit-tested, imported by all readers.
- **readout_common.py** (+ test_readout_common.py): shared register map, watchdog RegIO, and drain loop, used by all 4 readers + aclkgt_monitor.py + aclkgt_sweep.py. Adds wr_split / wr_utc (WR sec:ns decode, 0 renders UNSYNC) and the stream_events wr= path.
- **wr_time.py** (+ test_wr_time.py): White Rabbit timebase control over the S_AXI3 UIO block (0x8002_0000): status (lock + HW-vs-system delta), arm (writes floor(now)+1 mid-second so the next PPS loads it), disarm, clear-sticky.
- **redis_sink.py** (+ test_redis_sink.py): background Redis writer. Bounded in-process queue + writer thread pipelines, per event record, XADD (event-time ID with a per-stream monotonic guard) + HSET/HINCRBY of a per-code index + a periodic status/watchdog refresh; submit() never blocks (drops oldest, counted) so a Redis stall cannot stall the UIO drain; auto-reconnects. redis-py imported lazily so PC unit tests need no server.
- **redis_publish.py** (+ test_redis_publish.py): per-source publisher. Drains one UIO readout via readout_common.drain_events, drops UNSYNC events, and builds KR260-namespaced records: stream KR260:tclk / KR260:aclk (from uio4 / uio5) plus a per-event-code index KR260:event:<src>:0x<CODE>. Config deploy/redis-kr260.conf (persistence off); runbook deploy/redis.md; board dep deploy/requirements-board.txt.
- **diag.py / probe.py / pltest.py / uart_echo_test.py**: bring-up diagnostics (UART Lite step-trace, RAM-vs-peripheral probe, PL-alive counter check, AXI UART echo test).

Artifacts: **uart_echo.dts** (single-UIO overlay @ 0x8000_0000, used by ALL single-readout builds), **aclk_pipeline.dts** (three-UIO overlay 0x8000_0000 + 0x8001_0000 + 0x8002_0000: TCLK readout, ACLK readout, WR timebase), **uart_echo.bif / template.bif** (bootgen recipes), **shell.json** (XRT_FLAT metadata); requirements-board.txt (board-only pip deps: redis-py).

Runbooks: clk.md, tclk.md, aclk.md, aclkgt.md (M0 loopback, M1/M2 two-board fiber, sweep workflow), wr.md (White Rabbit sec:ns bring-up: E10/E12 wiring, arm, --wr readers), pinblink.md, README.md (generic load flow: xmutil unloadapp, fpgautil -b <bin> -o uart_echo.dtbo, md5 check first).

## 6. Constraints (constraints/)

Per-build XDCs. Pin facts to preserve: H12 = the shared Pmod line (TCLK/ACLK-Lite
in, generator out, pinblink out); B10 = pipeline ACLK-Lite mirror out; E10/E12/D10/D11
= scope debug pins; GT refclk Y6/Y5 (156.25 MHz MGTREFCLK0_224), RX T2/T1, TX R4/R3;
SFP sideband Y10 (TX_DISABLE, load-bearing), A10 (TX_FAULT), J12 (RX_LOS), W10
(MOD_ABS); all single-ended user I/O LVCMOS33. Clock groups: async_ps_vs_rx (pl_clk0
vs MMCM outputs) in the baseband builds; GT variants add rx/tx_usrclk2 + 50 MHz
freerun groups; kr260_aclk_pipeline.xdc is the 3-way superset.

## 7. Docs and IP

- docs/PROJECT.md (architecture + status map), docs/aclk-lite-framing.md (AUTHORITATIVE on-wire framing), docs/aclkgt-handoff.md, docs/aclkgt-hardware-facts.md, docs/generated/ (pipeline interface guide), docs/superpowers/ (specs + plans per feature).
- vivado/ip/: aclkgt_gt.xci (+ .veo) GTH IP, ila_gt.xci, gen_aclkgt_gt.tcl regeneration script. vivado/uart_echo_bd.tcl: exported BD for the skeleton.
- resources/: Fermilab TCLK/ACLK specs (reference PDFs).
