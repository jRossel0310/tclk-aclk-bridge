# ACLK-Lite readout board build (parallel to TCLK)

Date: 2026-06-18
Status: approved (design)

## Goal

Add a board-buildable ACLK-Lite readout that timestamps decoded events and hands
them to the PS over AXI4-Lite, generated the same way as the existing TCLK readout
and switchable with it through `hw.ps1`, while leaving the entire TCLK path
untouched. After this work, `.\hw.ps1 build -Tcl vivado\build_aclk.tcl -Name aclk`
produces an ACLK bitstream the same way `build_tclk.tcl` produces the TCLK one.

## Background and decoder choice

The repo has two distinct "ACLK" decoders:

- `rtl/aclk_bridge/ACLK_REV.v` (`module ACLK_RCV`): the GT / transceiver based
  gigabit ACLK receiver (16-bit data + K-chars, gearbox, CRC-8, alignment).
- `rtl/aclk_lite/aclk_lite_decoder.sv` (the ADM): a Manchester decoder that
  recovers a 10 MHz Manchester stream by oversampling.

The `resources/Aclk/ACLK-Lite Interface Specification.pdf` is decisive. It defines
the ACLK-Lite Decoder Module (ADM) as a Manchester decoder using "the same
Manchester encoding as TCLK," which decodes BOTH legacy 8-bit TCLK events AND
16-bit (+ 64-bit data) ACLK events from one line "without modifications to the
FPGA firmware." The same document calls out the gigabit GT path (`ACLK_RCV`) as a
separate, higher-rate option that is out of scope. Therefore the ACLK readout
wraps the ADM (`aclk_lite_decoder.sv`), not `ACLK_RCV`.

The ADM readout chain already exists and is sim-validated:
`rtl/aclk_lite/aclk_lite_readout_top.sv` wires ADM -> adapter -> the shared
`aclk_readout_axi`, and `tb/aclk_lite_readout/` covers the full chain. What is
missing is the board build: no `build_aclk.tcl`, no `aclk_readout_bd_top.v`, no
XDC, no deploy reader, and no way to select the target.

## Decisions (locked during brainstorming)

1. ACLK decoder = the Manchester ADM (`aclk_lite_decoder.sv`), per the spec.
2. Build structure = a separate `vivado/build_aclk.tcl` mirroring `build_tclk.tcl`.
   `build_tclk.tcl` is NOT edited; TCLK stays provably intact. Switching targets
   means pointing `hw.ps1 -Tcl` at the other file.
3. Board input = external pin only, sharing TCLK's H12 line (ACLK-Lite rides the
   same TTL interface as TCLK per the spec). No on-board stimulus generator.

## What stays intact (must not be edited)

`vivado/build_tclk.tcl`, `constraints/kr260_tclk.xdc`,
`rtl/aclk_lite/tclk_readout_top.sv`, `rtl/tclk_readout_bd_top.v`,
`deploy/tclk_read.py`, and the shared readout RTL
(`rtl/aclk_readout/aclk_readout_axi.sv`, `aclk_readout_core.sv`,
`rtl/async_fifo.sv`, `rtl/cdc_gray_count.sv`, `rtl/synchronizer.sv`). The shared
readout is already parameterized for both paths (`DROP_NULL`, the 16-byte register
map, the event filter), so no change is needed there. A TCLK build after this work
must be byte-for-byte equivalent to a TCLK build before it.

## Architecture (ACLK path)

```
H12 (external Manchester line)
   -> aclk_lite_decoder (ADM)            event_valid / event_id[15:0] /
                                         data_valid / data[63:0] /
                                         parity_error / is_tclk
   -> adapter (in aclk_lite_readout_top) flags = {.., is_tclk, has_data}
   -> aclk_readout_axi (shared)          timestamp + async FIFO + AXI4-Lite,
                                         16-byte register map, event filter
   -> PS (UIO at 0x8000_0000)            aclk_read.py
```

Decoder behavior, per spec: the ADM auto-detects frame length. 8 payload bits ->
legacy TCLK event (`event_id = {0x00, code}`, no data); 16 payload bits -> ACLK
event; 16 + 64 payload bits -> ACLK event + 64-bit data. The ACLK top sets
`DROP_NULL = 1` (drop and count 0xFF nulls, the ACLK convention), which is the
readout default and the opposite of the TCLK top (`DROP_NULL = 0`).

The AXI register map is the shared 16-byte-spaced map already used by TCLK
(STATUS 0x00, EVENT 0x10, DATA_HI 0x20, DATA_LO 0x30, TS_HI 0x40, TS_LO 0x50,
POP 0x60, EVENT_COUNT 0x70, NULL_COUNT 0x80, ERROR_COUNT 0x90, DEBUG 0xA0,
HEARTBEAT 0xB0, LOCK 0xC0, FILTER_CFG 0xD0 W, FILTERED_COUNT 0xE0 R). The event
filter (drop-mask) is therefore available to ACLK for free.

## Clocking

Mirror the proven `build_tclk.tcl` topology, but with a single MMCM output instead
of 80/40 MHz:

- An MMCM (clk_wiz) makes one oversample clock (target ~100 to 120 MHz, in the
  spec's 100 to 125 MHz oversample range) from `pl_clk0`. `PRIM_IN_FREQ` is set
  from `pl_clk0`'s exact realized Hz (the BD 41-238 fix).
- That oversample clock is the rx domain (runs the decoder and the readout's event
  side, so the hardware timestamp ticks at the oversample rate). `s_axi_aclk =
  pl_clk0`, asynchronous to the rx clock; all PS<->rx crossings go through the
  readout's async FIFO + gray counters.
- Reuse every hardware bring-up fix from the TCLK build: MMCM `resetn` from
  `pl_resetn0` (so the MMCM re-locks after a runtime fpgautil load), `dcm_locked`
  tied high on the auto `proc_sys_reset` (gating it on MMCM lock wedged the LPD
  bus), `set_clock_groups -asynchronous` between `clk_pl_*` and the MMCM output,
  SmartConnect on the LPD master (not the auto interconnect + protocol converter,
  which dropped read data on non-16-byte-aligned offsets), 16-byte register
  spacing, and `mmcm_locked -> 0xC0 LOCK`.

`OVERSAMPLE` (the decoder parameter = oversample-clock cycles per Manchester bit)
is computed from the chosen oversample clock divided by the line bit rate and is
documented as tunable at bring-up. It is the one value that cannot be validated
until a real ACLK-Lite source is connected, so it is called out as an assumption.

## New files (each mirrors a TCLK counterpart)

1. `rtl/aclk_readout_bd_top.v` (mirrors `rtl/tclk_readout_bd_top.v`): plain-Verilog
   block-design wrapper around `aclk_lite_readout_top`, with the X_INTERFACE
   attributes that let Vivado infer the AXI4-Lite slave. Ports: the external
   `aclk` line, the oversample clock, `rstn`, `mmcm_locked`, the AXI4-Lite slave,
   and the scope dbg pins. `pps` tied 0.
2. `vivado/build_aclk.tcl` (mirrors `vivado/build_tclk.tcl`): `proj_name = aclk`,
   `design_name = uart_echo_bd` (so the bitstream name and overlay are unchanged),
   sources = the shared readout chain + `aclk_lite_decoder.sv` +
   `aclk_lite_readout_top.sv` + `aclk_readout_bd_top.v`, one MMCM output clock,
   single external `aclk` pin, the XDC below.
3. `constraints/kr260_aclk.xdc` (mirrors `kr260_tclk.xdc`): `aclk` on H12, the same
   scope dbg pins, and `set_clock_groups -asynchronous` for `clk_pl_*` vs the MMCM
   output.
4. `deploy/aclk_read.py` (reuses `deploy/tclk_read.py` structure): polls STATUS,
   reads EVENT/DATA/TS/FLAGS, writes POP; interprets the full 16-bit event id and
   the 64-bit data payload (vs TCLK's 8-bit-only events) and reports `is_tclk` /
   `has_data` from FLAGS. Carries the same `--drop` filter helper support.
5. `deploy/aclk.md` (mirrors `deploy/tclk.md`): the ACLK build + load + read
   runbook.

## Edited files (ACLK-only; never used by the TCLK path)

- `rtl/aclk_lite/aclk_lite_readout_top.sv`: bring to parity with `tclk_readout_top`.
  Today it instantiates `aclk_readout_axi` without `mmcm_locked` (leaving it
  floating) and without `dbg_hb`. Add: `mmcm_locked` input wired through to the
  readout, `dbg_hb` output, and a line-activity edge-count diagnostic packed into
  the readout's DEBUG word (analogous to the TCLK top's `tclk_dbg_word`). Keep
  `DROP_NULL = 1`. This module is used only by the ACLK build, so TCLK is
  unaffected.
- `hw.ps1`: add `"aclk" = @("aclk_read.py")` (plus any filter helper) to the deploy
  `$pyMap`.

## Data flow and error handling

- One event per `event_valid` strobe; `data_valid` accompanies events that carried
  a 64-bit payload. The adapter sets `flags = {14'b0, is_tclk, has_data}`.
- `parity_error` (bad parity or malformed frame length) pulses one cycle and is
  counted in ERROR_COUNT; it never asserts `event_valid`, so bad frames never enter
  the FIFO.
- 0xFF null events are dropped and counted in NULL_COUNT (`DROP_NULL = 1`).
- FIFO overflow sets the sticky STATUS overflow bit; the PS sees lost-event
  indication without a hang.
- The PS read sequence is the shared one: poll STATUS, while not empty read
  EVENT/DATA_*/TS_*, then write POP.

## Testing

- Extend `tb/aclk_lite_readout/` (TDD) to cover the updated top: `mmcm_locked`
  passthrough to the LOCK register path and the DEBUG activity word, in addition to
  the existing event/data/flags/timestamp/counter checks. Keep it sim-green
  (Icarus, cocotb 2.0) and emit the matplotlib plot per the project convention.
- `aclk_lite_decoder` already has its own decoder-level testbench
  (`tb/aclk_lite_decoder/`); no decoder logic changes here.
- `build_aclk.tcl`, `kr260_aclk.xdc`, and `aclk_read.py` are not unit-testable;
  they follow the proven TCLK patterns and are verified by a successful Vivado
  build (synth + impl + bitstream) and bootgen run.
- Hardware end-to-end verification (decode of a live ACLK-Lite line) is deferred
  until a real ACLK-Lite transmitter is available; documented as a known gap, the
  same posture the TCLK readout had before a live TCLK source was found.

## Out of scope

- The gigabit GT / `ACLK_RCV` path and any SFP / transceiver bring-up.
- An on-board Manchester stimulus generator (input is external-pin-only).
- Reconciling the ADM's exact bit-level framing against the official spec timing
  diagrams beyond what the decoder already implements; the decoder's framing is a
  documented assumption to be validated against a live source.
- The PS-side Redis bridge.

## Assumptions to validate at bring-up

- The ADM's Manchester framing matches a real ACLK-Lite / TCLK line (the decoder
  header itself flags this as "reconcile with the official spec before trusting a
  real external source").
- `OVERSAMPLE` and the oversample-clock frequency match the real line bit rate.
- ACLK-Lite is physically present on the same H12 / LVCMOS33 interface as TCLK.
