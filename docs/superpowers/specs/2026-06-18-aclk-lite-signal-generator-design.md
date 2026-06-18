# ACLK-Lite Signal Generator bitstream - design

Date: 2026-06-18
Part: xck26-sfvc784-2LV-c (KR260), Vivado 2024.2

## Purpose

Build a SECOND, independent KR260 bitstream that is the SIGNAL GENERATOR for the
ACLK-Lite receiver built in `2026-06-18-aclk-lite-readout-build-design.md`. It
emits a Manchester-encoded ACLK-Lite stream OUT of PMOD1 pin 1 = package pin H12
(LVCMOS33), the exact same pin the receiver uses as its input. Wiring the two
boards' H12 pins together (plus a common ground) verifies the receiver end to end
on real hardware.

The generated stream must be bit-compatible with what
`rtl/aclk_lite/aclk_lite_decoder.sv` decodes. The existing receiver build and all
TCLK/receiver files stay untouched; this is a parallel set of new files.

## Encoding contract (confirmed against the decoder)

Confirmed by reading `aclk_lite_decoder.sv` and `tb/manchester_tx_model.py`:

- Standard Manchester per bit: a bit `b` is two half-bits `{~b, b}` (first half
  `~b`, second half `b`), `OVERSAMPLE` oversample-clock cycles per FULL bit
  (HALF = OVERSAMPLE/2 cycles each half).
- Idle = line held steady HIGH (a deliberate Manchester violation) = the frame
  delimiter / gap between frames.
- Frame = start bit (value 0) + payload sent MSB first + one even-parity bit
  (parity = XOR of all payload bits), then return to idle high.
- Three frame lengths the decoder accepts (decoder counts start+payload+parity):
  - 8 payload bits  (10 captured) -> legacy TCLK event: `event_id = {8'h00, payload[7:0]}`, `is_tclk=1`
  - 16 payload bits (18 captured) -> ACLK event:        `event_id = payload[15:0]`
  - 80 payload bits (82 captured) -> ACLK event + data:  `event_id = payload[79:64]`, `data = payload[63:0]`
  - Any other length -> decoder flags parity/length error.

`tb/manchester_tx_model.py` `frame_levels(payload, length, flip_bit)` is the golden
reference for the per-clk line waveform: `bits = [0] + payload_MSB_first + [parity]`,
each bit -> `[1-b]*HALF + [b]*HALF`, idle = `1`.

## Clocking / rate

Reuse the receiver build's proven clocking topology verbatim:

- PS Zynq US+ with board preset, `pl_clk0 = 100 MHz` as the only PS PL clock used.
- A clk_wiz MMCM derives a single ~120 MHz oversample clock (`clk_os`) from
  `pl_clk0` INSIDE the PL (`PRIM_SOURCE = No_buffer`, `PRIM_IN_FREQ` = pl_clk0's
  exact realized rate to avoid BD 41-238).
- MMCM `resetn` from `pl_resetn0` so it re-locks after a runtime fpgautil load.
- An auto `proc_sys_reset` with `dcm_locked` tied HIGH provides the design `rstn`
  (proven on the tclk/uart_echo/receiver builds; gating reset on MMCM lock wedged
  the bus on hardware).
- `set_clock_groups -asynchronous` between `clk_pl_*` and `clk_out*clk_wiz*`.

`OVERSAMPLE = 12` at 120 MHz => ~10 MHz Manchester bit rate, matching the receiver
so the two boards' bit rates agree within the decoder's tolerance.

NOTE: unlike the receiver, the generator has NO AXI slave (Event source decision
below), so there is NO LPD AXI master, NO SmartConnect, and `s_axi_*` is absent.
The PS is present ONLY to source `pl_clk0` and `pl_resetn0` for the MMCM + reset.

## Resolved design decisions

1. **Event source: hardcoded timeline, no AXI.** A self-contained PL state machine
   cycles through a fixed event list and transmits on boot. No PS interaction.
2. **Test pattern: fixed recognizable trio, slow.** Repeat three frames that
   exercise all three decoder paths, separated by a short idle gap, then a long
   idle (~1 ms) before the trio repeats:
   - TCLK 8-bit:  `event_id = 0x55`
   - ACLK 16-bit: `event_id = 0xABCD`
   - ACLK 80-bit: `event_id = 0x1234`, `data = 0xDEADBEEFCAFE0001`
3. **Output drive: plain LVCMOS33 push-pull on H12.** Short (< ~30 cm)
   board-to-board jumper with common ground via the two carrier cards' Pmod GND
   pins. No series resistor / level shifter. Both ends are 3.3 V LVCMOS.
4. **Debug pins: frame-sync trigger + divided clock.** `frame_sync_dbg` (a pulse
   at the start of each trio, for a clean scope trigger) and `clkos_dbg`
   (clk_os / 1024 ~117 kHz, confirms the MMCM is alive).

## Architecture

Three new RTL units, mirroring the receiver's structure. No receiver/TCLK file is
edited.

### 1. `rtl/aclk_lite/aclk_lite_encoder.sv` (reusable Manchester encoder)

- Params: `OVERSAMPLE = 12` (HALF = OVERSAMPLE/2 = 6).
- Inputs: `clk`, `rstn` (async, active-low), `start` (1-cycle strobe),
  `payload[79:0]`, `length[6:0]` (one of 8/16/80).
- Outputs: `line` (idle HIGH), `busy`.
- Behavior: on `start` (ignored while `busy`), it latches `payload`/`length`,
  computes even parity over the low `length` payload bits, and serializes
  `start(0) -> payload MSB-first -> parity`. Each FULL bit drives `line = ~b` for
  HALF cycles then `line = b` for HALF cycles. When the parity bit's second half
  completes, `line` returns to idle HIGH and `busy` deasserts. The emitted per-clk
  waveform is identical to `manchester_tx_model.frame_levels(payload, length)`.

### 2. `rtl/aclk_lite/aclk_lite_gen_timeline.sv` (hardcoded event source)

- Params: `OVERSAMPLE = 12`, plus timing params `IDLE_GAP` (short inter-frame
  idle in clk_os cycles, comfortably > 1.5 bits = 18 cycles, e.g. 64) and
  `TRIO_GAP` (long idle before repeat, ~1 ms = ~120000 cycles).
- Inputs: `clk` (clk_os), `rstn`.
- Outputs: `line` (to H12), `frame_sync` (1-cycle pulse at trio start).
- Instantiates one `aclk_lite_encoder`. An FSM: pulse `frame_sync`, send frame 0
  (8-bit), wait `IDLE_GAP` after `busy` clears, send frame 1 (16-bit), wait,
  send frame 2 (80-bit), wait `TRIO_GAP`, repeat. The encoder's idle-high `line`
  is the module's `line` output (idle is just "not busy").

### 3. `rtl/aclk_gen_bd_top.v` (plain-Verilog BD wrapper, no AXI)

Mirrors `rtl/aclk_readout_bd_top.v` but with no AXI interface.

- Inputs: `clk_os` (120 MHz from BD clk_wiz), `rstn` (from proc_sys_reset).
- Outputs: `aclk_out` (-> H12), `frame_sync_dbg` (-> Pmod), `clkos_dbg`
  (clk_os / 1024 -> Pmod, MMCM-alive scope diagnostic).
- Instantiates `aclk_lite_gen_timeline #(.OVERSAMPLE(12))`, wires its `line` to
  `aclk_out` and `frame_sync` to `frame_sync_dbg`, and divides `clk_os` by 1024
  for `clkos_dbg` (same divided-clock idea as the receiver wrapper).

## Data flow

```
PS pl_clk0 (100 MHz) --clk_wiz MMCM--> clk_os (120 MHz) --> aclk_gen_bd_top
PS pl_resetn0 --> proc_sys_reset (dcm_locked=1) --> rstn --> aclk_gen_bd_top
aclk_lite_gen_timeline (FSM walks trio) --> aclk_lite_encoder --> line
line --> aclk_out --> H12 pad --> jumper --> receiver board H12 --> decoder
frame_sync --> frame_sync_dbg --> Pmod (scope trigger)
clk_os/1024 --> clkos_dbg --> Pmod (alive)
```

## Build

`vivado/build_aclkgen.tcl`, mirroring `vivado/build_aclk.tcl`:

- `proj_name = aclkgen`, `design_name = uart_echo_bd` (so the overlay/bitstream
  name `uart_echo_bd_wrapper.bit.bin` is unchanged), `part = xck26-sfvc784-2LV-c`.
- RTL sources: `aclk_lite/aclk_lite_encoder.sv`,
  `aclk_lite/aclk_lite_gen_timeline.sv`, `aclk_gen_bd_top.v`. (The generator
  instantiates only the encoder/timeline, which have no `synchronizer.sv`
  dependency; the decoder and `synchronizer.sv` are sim-only, in the loopback tb.)
- PS + clk_wiz MMCM + proc_sys_reset exactly as in `build_aclk.tcl`, MINUS the
  SmartConnect, the LPD AXI master enable, and all `S_AXI`/`maxihpm0_lpd_aclk`
  wiring. Keep `PSU__FPGA_PL0_ENABLE=1`, `PL0_REF_CTRL__FREQMHZ=100`.
- BD ports: `aclk_out`, `frame_sync_dbg`, `clkos_dbg` (all `-dir O`).
- `constraints/kr260_aclkgen.xdc`:
  - `aclk_out` -> PACKAGE_PIN H12, LVCMOS33
  - `clkos_dbg` -> PACKAGE_PIN E10, LVCMOS33 (reuses receiver Pmod1 pin)
  - `frame_sync_dbg` -> PACKAGE_PIN E12, LVCMOS33 (reuses receiver Pmod1 pin)
  - `set_clock_groups -asynchronous` for `clk_pl_*` vs `clk_out*clk_wiz*`
  - Verify connector positions against the carrier-card silkscreen.
- Build command: `.\hw.ps1 build -Tcl vivado\build_aclkgen.tcl -Name aclkgen`
- Deliverable: `uart_echo_bd_wrapper.bit.bin` + its MD5 to load on the second board.

## Testing (TDD; the loopback sim is the key gate)

cocotb 2.0 + per-module `runner.py` (icarus default), mirroring
`tb/aclk_lite_decoder/`.

### `tb/aclk_lite_encoder/` (unit, written first)

- `runner.py` compiles `rtl/aclk_lite/aclk_lite_encoder.sv` only,
  `hdl_toplevel = aclk_lite_encoder`.
- `test_aclk_lite_encoder.py`: drive `start` + `payload`/`length`, capture `line`
  on each clk, and assert the captured level sequence (from the first driven bit
  through return-to-idle) equals `frame_levels(payload, length)` evaluated at
  OVERSAMPLE=12, for length in {8, 16, 80}. Also assert idle is steady HIGH before
  `start` and after `busy` clears, and that `start` during `busy` is ignored.
- Model reuse WITHOUT editing the shared file: inside this test set
  `manchester_tx_model.OVERSAMPLE = 12` and `manchester_tx_model.HALF = 6` before
  calling `frame_levels` (module-level rebind in the test process only).

### `tb/aclk_lite_gen_loopback/` (integration, the key test)

- `tb_aclk_gen_loopback.sv`: a tiny SV top that instantiates
  `aclk_lite_gen_timeline #(.OVERSAMPLE(12))` and
  `aclk_lite_decoder #(.OVERSAMPLE(12))`, wiring `timeline.line -> decoder.line`,
  both on one sim clk. (Use small `IDLE_GAP`/`TRIO_GAP` overrides so the sim runs
  fast.) Exposes the decoder's `event_valid`/`event_id`/`data_valid`/`data`/
  `parity_error`/`is_tclk`.
- `runner.py` compiles `synchronizer.sv`, `aclk_lite_encoder.sv`,
  `aclk_lite_gen_timeline.sv`, `aclk_lite_decoder.sv`, and the tb top.
- `test_aclk_gen_loopback.py`: run long enough to capture >= 2 full trios; assert
  the decoded events are exactly, in order and repeating:
  `(0x0055, None, is_tclk=1)`, `(0xABCD, None, 0)`,
  `(0x1234, 0xDEADBEEFCAFE0001, 0)`, with zero `parity_error`. On completion emit
  a matplotlib plot (recovered line for one trio + an events-over-time / occupancy
  graph) under `sim_build/aclk_lite_gen_loopback/plots/` (project convention that
  cocotb tests emit graphs).

## Out of scope (YAGNI)

- No AXI control, no PS-side generator script (hardcoded timeline boots and runs).
- No back-to-back / max-rate stress pattern (slow recognizable trio only).
- No White Rabbit / PPS, no level shifting hardware.
- No edits to receiver or TCLK RTL, builds, XDC, or their tests.

## Risks / things to verify at bring-up

- H12 as an OUTPUT: H12 is used as an input on the receiver; confirm it is a
  general user IO usable as LVCMOS33 output on the carrier (expected yes).
- Bit-rate tolerance: both boards run their own MMCM from their own pl_clk0;
  the ~120 MHz rates may differ by tens of ppm. The decoder's mid-bit/idle gap
  thresholds (0.75 / 1.5 bit) tolerate this for short frames; 80-bit frames are
  the worst case. Verify on hardware.
- Pmod pin reuse (E10/E12): confirm against the carrier-card silkscreen since this
  board is the generator, not the receiver.
```
