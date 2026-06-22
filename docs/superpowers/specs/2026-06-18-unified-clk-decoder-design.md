# Unified ACLK/TCLK decoder + "read-both" bitstream

Date: 2026-06-18
Status: approved (design)

## Goal

Produce one new KR260 bitstream that decodes BOTH real Fermilab TCLK and real
ACLK-Lite from a single H12 input, feeding the existing shared readout, and
re-align the signal-generator board to emit the real ISD framing so it is readable
by the same decoder. This delivers the ACLK-Lite spec's "one IP core decodes either
interface without firmware change" on the real line.

## Background

Two on-wire framings are in play (see `docs/aclk-lite-framing.md`, the authoritative
reference pinned from the PIP-II ISD):

- Real TCLK and real ACLK-Lite share the same Manchester line code (100 ns cells,
  MSB-first, byte-oriented: each byte = start + 8 data + even parity, frames end on
  2 terminal idle-1 cells; frame length 1 byte = TCLK event, 2 = ACLK event,
  12 = full packet event+data).
- The current `rtl/aclk_lite/aclk_lite_decoder.sv` is a clean-room approximation
  (standard Manchester, DC-high idle, single whole-frame parity). It cannot decode
  the real line (it returned zero events from live TCLK), and the existing generator
  board emits this same clean-room framing.

The shipped TCLK readout already decodes real TCLK on hardware using
`serdec4_9MHz` (80 MHz bit recovery) + `TCLK_DESERIALIZER2` (40 MHz, 1-byte framer).
The unified decoder keeps that proven front end and replaces only the 1-byte
deserializer with a length-aware multi-byte framer.

## Decisions (locked during brainstorming)

1. **Unified real decoder** (not dual-decoder): one `serdec`-fed length-aware
   decoder reads real TCLK and real ACLK-Lite from one input.
2. **Reflash the generator** to the real ISD framing so it matches the unified
   decoder (the generator currently emits the clean-room framing).
3. Scope = the decoder + a board build (new bitstream) + the generator
   re-alignment. One input pin (H12); the source is swapped (real TCLK or generator).
4. New, separate build target; the shipped `build_tclk` / `build_aclk` and their
   decoders/readout tops stay intact (retire later once `build_clk` is HW-proven).
5. Sequence: framer + build first (HW-verify on real TCLK immediately), then the
   generator re-alignment (HW-verify ACLK).

## Architecture

```
H12 (real TCLK or real-framed ACLK-Lite, swapped)
  -> serdec4_9MHz        (CLK_80M, biphase Manchester bit recovery; REUSED, proven)
       SCLK + SDATA
  -> clk_byte_framer     (CLK_40M, NEW: byte-oriented length-aware framing)
       event_valid / event_id[15:0] / data_valid / data[63:0] / parity_error / is_tclk
  -> adapter (in clk_readout_top)   flags = {.., is_tclk, has_data}
  -> aclk_readout_axi    (shared: timestamp + async FIFO + AXI-Lite, 16-byte map, filter)
  -> PS over UIO         (clk_read.py)
```

Clocking, reset (dcm_locked tied high), SmartConnect, async clock groups, and the
16-byte register map all reuse the proven `build_tclk.tcl` skeleton. `serdec` runs at
80 MHz and the framer + readout at 40 MHz (the rates proven on the real TCLK line),
made by an MMCM from pl_clk0.

## Components

### New RTL

**`rtl/aclk_lite/clk_byte_framer.sv`** - consumes `serdec`'s recovered NRZ bit
stream (`SCLK` strobe + `SDATA`) on `clk_40m`. Behavior:
- Idle = logical-1 cells. Detect a start cell (0) after idle.
- Read a byte: start (0) + 8 data bits MSB-first + 1 even-parity bit. Check parity.
- After a byte's parity cell, peek the next cell: `0` = start of another byte
  (continue accumulating); `1` = frame terminated (the 2 terminal idle cells).
- On termination, dispatch by accumulated byte count:
  - 1 byte  -> `event_id = {8'h00, byte0}`, `is_tclk = 1`, `event_valid`. No data.
  - 2 bytes -> `event_id = {byte0, byte1}`, `event_valid`. No data.
  - 12 bytes -> `event_id = {byte0, byte1}`, `data = {byte2..byte9}`,
    `event_valid` + `data_valid`. Bytes 10 (CRC8) and 11 (control) captured but
    ignored (CRC validation deferred per the framing doc).
  - any other byte count, or a per-byte parity failure -> `parity_error` (one-cycle
    strobe), frame dropped, no `event_valid`.
- Outputs match `aclk_lite_decoder` exactly so it is a drop-in for the readout
  adapter: `event_valid, event_id[15:0], data_valid, data[63:0], parity_error,
  is_tclk`.

**`rtl/aclk_lite/clk_rcv.sv`** - thin wrapper instantiating `serdec4_9MHz`
(`CLK_80M`, `RATE=1` for 10 MHz) + `clk_byte_framer` (`CLK_40M`), mirroring the
structure of `TCLK_RCV`. Exposes the raw line + the two clocks + reset, and the
event/data interface, plus `SIG_ERR` from serdec for diagnostics.

**`rtl/aclk_lite/clk_readout_top.sv`** - `clk_rcv` -> adapter -> shared
`aclk_readout_axi`, mirroring `tclk_readout_top`. `DROP_NULL = 0` (no on-wire null
code; every decoded event is buffered). flags = `{14'b0, is_tclk, has_data}`. Wires
`mmcm_locked` (-> 0xC0 LOCK) and the line-activity DEBUG word + `dbg_hb`, same as the
TCLK top. Parity-error edge handling: `clk_byte_framer.parity_error` is already a
one-cycle strobe, so it feeds the readout's error counter directly (no sticky-latch
edge-detect needed, unlike the serdec PERR path).

**`rtl/clk_readout_bd_top.v`** - plain-Verilog BD wrapper around
`clk_readout_top` (X_INTERFACE attributes for the inferred AXI4-Lite slave), with the
external line pin, the 80/40 clocks, `mmcm_locked`, and the scope dbg pins. Mirrors
`tclk_readout_bd_top.v`.

### Board build

**`vivado/build_clk.tcl`** - mirrors `build_tclk.tcl`: `proj_name = clk`,
`design_name = uart_echo_bd` (overlay/bitstream name unchanged), an MMCM making
80 + 40 MHz from pl_clk0, the external line on H12, SmartConnect on the LPD master,
`dcm_locked` tied high, `mmcm_locked` -> readout. Sources = shared readout chain +
`serdec4_9MHz` + `clk_byte_framer` + `clk_rcv` + `clk_readout_top` +
`clk_readout_bd_top`.

**`constraints/kr260_clk.xdc`** - line input on H12 (LVCMOS33), the same scope dbg
pins (E10/E12/D10/D11), async clock groups for `clk_pl_*` vs the MMCM outputs. Mirrors
`kr260_tclk.xdc`.

### PS reader

**`deploy/clk_read.py`** - drains the shared 16-byte register map; `TICK_NS = 25.0`
(40 MHz timestamp). Prints each event with `is_tclk` / `has_data`: 8-bit code for
TCLK events, full 16-bit event id + 64-bit data for ACLK events. Reuses
`tclk_filter.py` for the drop-mask. Effectively `aclk_read.py` retuned to 40 MHz and
the 16-bit/64-bit event format.

### Generator re-alignment

The generator board's Manchester encoder (on the generator branch) is changed to emit
the REAL framing: `serdec`-compatible Manchester cells at 100 ns, per-byte (start 0 +
8 data MSB-first + even parity), bytes back-to-back with no inter-byte stop, 2 terminal
idle-1 cells at frame end, and frame types of 1 / 2 / 12 bytes. The 12-byte packet
carries event (bytes 0-1) + data (bytes 2-9) + a CRC8 byte + a control byte (the CRC
may be a fixed/placeholder value initially, since the decoder ignores it). Validated by
loopback sim against `clk_byte_framer`.

## Data flow and error handling

- One event per `event_valid`; `data_valid` accompanies the 12-byte (data-bearing)
  frame. The adapter sets `flags = {14'b0, is_tclk, has_data}`.
- `parity_error` (per-byte parity fail or malformed byte count) pulses one cycle, is
  counted in ERROR_COUNT, and never asserts `event_valid`, so bad frames never enter
  the FIFO.
- No on-wire null code, so `DROP_NULL = 0` and NULL_COUNT stays 0. (The configurable
  drop-mask filter remains available for selectively dropping event codes.)
- FIFO overflow sets the sticky STATUS overflow bit.
- PS read sequence is the shared one: poll STATUS, while not empty read
  EVENT/DATA_*/TS_*, write POP.

## Testing

- **Unit:** `tb/clk_byte_framer/` drives a recovered-bit-stream (`SCLK`/`SDATA`)
  model and checks event/data/is_tclk/parity for 1-, 2-, and 12-byte frames, plus a
  per-byte parity error and a malformed byte count.
- **Front-end + full chain:** extend `tb/tclk_tx_model.py` (which already emits
  `serdec`-compatible TCLK cells) to produce multi-byte real-framed ACLK frames; drive
  `serdec` -> `clk_byte_framer` -> readout -> AXI in a full-chain tb mirroring
  `tb/aclk_lite_readout`, checking ids/data/flags/timestamps/counts for a mix of
  1/2/12-byte frames. Emit the matplotlib plot per project convention.
- **Generator loopback (sim):** the re-aligned generator encoder -> `serdec` ->
  `clk_byte_framer` -> decoded events match injected events (proves generator and
  decoder agree before hardware).
- **Hardware:** build `build_clk`, load it, plug the real office TCLK into H12 and
  verify decoded events against the TCLK event table (`resources/Tclk/`); then plug the
  reflashed generator and verify ACLK events + 64-bit data. TCLK is HW-verifiable
  immediately; ACLK after the generator reflash.

## Out of scope

- CRC8 validation (poly/init unspecified in the docs; decoder exposes EVENT/DATA and
  ignores CRC for now - cross-check against `crc8_calc.v` poly 0x2F or a capture later).
- Retiring `build_tclk` / `build_aclk` (kept intact until `build_clk` is HW-proven).
- The PS Redis bridge.
- The optional 10 MHz sync input (post-decode alignment; does not affect decoding).

## Assumptions to validate at bring-up

- `serdec4_9MHz` recovers ACLK-Lite cells identically to TCLK cells (same line code
  per the ISD); confirmed by the framing doc, verified on hardware once a real-framed
  ACLK source (the reflashed generator) is connected.
- The 12-byte packet's byte layout (event 0-1, data 2-9, CRC 10, control 11) matches
  the real source; verified against the office TCLK (1-byte path) immediately and the
  generator (12-byte path) after reflash.
