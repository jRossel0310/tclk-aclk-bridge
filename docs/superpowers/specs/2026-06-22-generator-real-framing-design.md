# Re-align the signal generator to the real ISD framing

Date: 2026-06-22
Status: approved (design)

## Goal

Rewrite the ACLK-Lite signal generator so it emits the REAL biphase-mark,
byte-oriented framing that the shipped unified decoder (`build_clk`,
`serdec4_9MHz` + `clk_byte_framer`) reads. This lets the generator board and the
`build_clk` receiver board talk end to end and closes the deferred ACLK hardware
verification. Rewrite in place (retires the clean-room generator). Golden reference
= the already-built `tb/clk_tx_model.py` / `tb/tclk_tx_model.py`.

## Background

The current generator (`rtl/aclk_lite/aclk_lite_encoder.sv` +
`aclk_lite_gen_timeline.sv`, build `vivado/build_aclkgen.tcl`) emits the CLEAN-ROOM
framing: standard Manchester (bit b -> two half-bits {~b, b}), DC-high idle, one
even-parity bit over the whole frame, lengths 8/16/80. Only the clean-room
`aclk_lite_decoder.sv` reads it. The shipped unified decoder cannot.

The authoritative real framing (see `docs/aclk-lite-framing.md`): the same
Manchester line code as TCLK (`serdec`-decodable biphase-mark), 100 ns cells,
byte-oriented - each byte = start(0) + 8 data MSB-first + even parity, bytes
back-to-back, frame ends on 2 terminal idle-1 cells. Frame length selects type:
1 byte = TCLK event, 2 = ACLK event, 12 = full packet (event bytes 0-1, data bytes
2-9, CRC byte 10, control byte 11). Idle is a continuous-1s square wave (not DC).

## Decisions (locked during brainstorming)

1. **Rewrite in place** (`aclk_lite_encoder.sv` + `aclk_lite_gen_timeline.sv` +
   `aclk_gen_bd_top.v` + `build_aclkgen.tcl` + the encoder/loopback tests). The
   clean-room generator is retired.
2. **Fixed placeholder CRC/control bytes**: byte 10 = 0x00, byte 11 = 0x00. The
   decoder ignores both; the real CRC8 polynomial is unconfirmed for ACLK-Lite, so
   a computed value would be speculative.
3. **Generator cell clock = 80 MHz** (down from 120 MHz), `SAMPLES_PER_CELL = 8`
   (4 per half-cell) = 100 ns cells, bit-identical to `tclk_tx_model.biphase_samples`
   and what the receiver's `serdec` (also 80 MHz) expects.
4. Keep the same test trio values (TCLK 0x55, ACLK event 0xABCD, full packet event
   0x1234 + data 0xDEADBEEFCAFE0001) - the values already seen decoding.
5. Leave the now-orphaned clean-room `aclk_lite_decoder.sv` untouched (retiring it
   is separate cleanup).

## Architecture

The generator is a free-running biphase-mark cell engine driving H12. It ALWAYS
emits cells so the receiver's `serdec` keeps carrier lock: idle = continuous 1-cells,
a frame = its bytes' cells, then back to 1-cells. A timeline FSM feeds it the test
trio with idle gaps. No PS/AXI interaction (boots and transmits).

```
aclk_lite_gen_timeline (test trio + idle gaps + frame_sync)
  -> aclk_lite_encoder (biphase-mark cell engine, byte-oriented framing)
       line (idle = continuous 1-cells, 100 ns)
  -> aclk_gen_bd_top -> H12
```

On the receiver board (already shipped, unchanged): H12 -> serdec4_9MHz ->
clk_byte_framer -> shared readout -> clk_read.py.

## Components

### `rtl/aclk_lite/aclk_lite_encoder.sv` (rewritten)

Two changes from the clean-room version:

1. **Bit-cell encoding: biphase-mark.** Per 100 ns cell: a level transition at every
   cell boundary, plus an extra mid-cell transition iff the cell's bit is 1 (none for
   0). `SAMPLES_PER_CELL = 8` clk cycles per cell (HALF = 4 per half-cell). Idle emits
   continuous 1-cells (the carrier square wave), never DC. This is bit-identical to
   `tclk_tx_model.biphase_samples` driven at 8 samples/cell.

2. **Framing: byte-oriented.** New interface:
   `clk, rstn, start, event[15:0], data[63:0], frame_type[1:0], line, busy`.
   - `frame_type`: 0 = TCLK (1 byte), 1 = ACLK event (2 bytes), 2 = full (12 bytes).
   - Byte assembly, MSB-first:
     - TCLK: byte0 = event[7:0].
     - ACLK event: byte0 = event[15:8], byte1 = event[7:0].
     - Full: byte0 = event[15:8], byte1 = event[7:0], byte2 = data[63:56], ...,
       byte9 = data[7:0], byte10 = 8'h00 (CRC placeholder), byte11 = 8'h00 (control).
   - Each byte serializes as start(0) + 8 data bits MSB-first + even parity
     (parity = XOR of the 8 data bits), back-to-back with NO inter-byte gap.
   - After the last byte, return to emitting idle 1-cells (the 2 terminal idle cells
     the framer keys on come from this idle stream).
   - `start` while `busy` is ignored. `busy` is high for the duration of a frame.

### `rtl/aclk_lite/aclk_lite_gen_timeline.sv` (rewritten)

Same hardcoded trio, now driving the new encoder interface:
- frame0: frame_type=0 (TCLK), event=0x0055.
- frame1: frame_type=1 (ACLK event), event=0xABCD.
- frame2: frame_type=2 (full), event=0x1234, data=0xDEADBEEFCAFE0001.
Idle gaps between frames (the encoder emitting 1-cells; gap must exceed the 2 terminal
cells the framer needs - a comfortable margin), `frame_sync` 1-cycle pulse at the start
of each trio (scope trigger), ~1 ms trio repeat. The timeline holds `event`/`data`/
`frame_type` stable and pulses `start`, waiting on `busy` between frames (same handshake
shape as today).

### `rtl/aclk_gen_bd_top.v` + `vivado/build_aclkgen.tcl` (retuned)

Retune the clk_wiz MMCM from 120 MHz to 80 MHz (the cell-engine clock). Keep the H12
output and the `frame_sync_dbg` / `clkos_dbg` scope pins. The build's design_name and
bitstream packaging are unchanged from the current generator build.

## Data flow and error handling

- The encoder emits a continuous biphase stream; there is no error path on the TX side
  (it always produces well-formed frames). `busy` provides backpressure to the timeline.
- The terminal idle cells that end a frame are simply the encoder returning to the
  idle 1-cell stream; no explicit stop-cell logic is needed.

## Testing

- **Unit (`tb/aclk_lite_encoder/`, rewritten):** drive the encoder for each
  frame_type, capture `line` once per cell, and assert the captured cell stream is
  bit-identical to `clk_tx_model` / `tclk_tx_model` golden samples for the 1-, 2-, and
  12-byte frames. Emit the matplotlib plot per project convention.
- **Loopback (`tb/aclk_lite_gen_loopback/`, rewritten) - the key gate:** the rewritten
  encoder -> `serdec4_9MHz` (80 MHz) -> `clk_byte_framer` (40 MHz) -> assert the decoded
  events match the injected trio (event ids, the 64-bit data, is_tclk/has_data flags).
  This proves the generator and the shipped unified decoder agree in simulation before
  hardware.
- **BD top (`tb/aclk_gen_bd_top/`, updated):** confirm the wrapper elaborates and drives
  `aclk_out` from the timeline at the new clock.
- **Hardware:** build the generator bitstream, wire its H12 output to the `build_clk`
  receiver board's H12 input, and confirm the trio (0x55 as TCLK, 0xABCD as ACLK event,
  0x1234 + 0xDEADBEEFCAFE0001 as full packet) decodes via `clk_read.py`. Closes the
  deferred ACLK end-to-end verification.

## Out of scope

- Computing a real CRC8 (placeholder 0x00; poly unconfirmed for ACLK-Lite).
- Retiring `aclk_lite_decoder.sv` / `manchester_tx_model.py` (now orphaned but left
  intact; separate cleanup).
- Any change to the shipped receiver (`build_clk`, `serdec4_9MHz`, `clk_byte_framer`,
  `clk_readout_*`).
- A PS-driven (AXI-configurable) event source; the timeline stays hardcoded.

## Assumptions to validate at bring-up

- The 80 MHz / 8-samples-per-cell biphase output is recovered by the receiver's
  `serdec` identically to real TCLK (same line code per the framing doc); confirmed in
  the loopback sim and on hardware board-to-board.
- The idle-gap length between frames provides at least the 2 terminal idle cells the
  framer requires (the trio uses a comfortable margin).
