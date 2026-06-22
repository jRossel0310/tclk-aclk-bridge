# ACLK-Lite / TCLK on-wire framing (authoritative reference)

Source: answers extracted from `ED0016516 PIP-II Timing ISD.pdf` and
`resources/Aclk/` (ACLK-Lite Interface Specification, ACORN-DOC-2204 "Beam
Synchronous for the Rest of Us"), cross-checked against the hardware-proven
`rtl/aclk_bridge/serdec4_9MHz.v` + `TCLK_DESERIALIZER2.v`. Captured 2026-06-18.

This is the framing a real-line-compatible decoder must implement. The existing
`rtl/aclk_lite/aclk_lite_decoder.sv` is a clean-room approximation and does NOT
match this (see "Gap vs current decoder" below).

## Physical layer
- ACLK-Lite uses the **same Manchester line code as legacy TCLK**, including for
  the extended 96-bit frame. `serdec4_9MHz` recovers the bit stream the same way
  it does for TCLK (SCLK + SDATA), provided the framer does not assume every frame
  ends after one byte.
- **Bit-cell period = 100 ns** (10 MHz) for ordinary ACLK-Lite / TCLK. Oversample
  ratio at a 100-125 MHz FPGA clock is 10-12.5 samples/cell. (Our KR260 board runs
  a 120 MHz oversample clock = 12 samples/cell.) A beam-synchronous LCLK-Lite
  variant runs at 10.16 MHz (~98.4 ns/cell).
- **Bit order: MSB first.** Each byte is serialized MSB..LSB, then its parity.
  Example: bytes 0x9D then 0xD2 yield event 0x9DD2.
- **Idle = logical-1 cells** on the decoded Manchester stream (physically still
  transitioning; it is NOT a DC-high wire).

## Frame structure (byte-oriented, contiguous)
- Each byte = `start (logical 0) + 8 data bits (MSB first) + 1 even-parity bit`
  = 10 cells.
- Non-final bytes run **back-to-back**: the next byte's start (0) cell follows
  immediately after the previous byte's parity cell. There are NO stop cells
  between bytes within a frame.
- A frame ends with **2 terminal idle-1 cells**. These give the familiar
  1.2 / 2.2 / 12.2 us frame times and distinguish "terminated" from "another byte
  follows".

### Length detection (the decode rule)
After each byte's parity cell, sample the next cell:
- `0` -> it is the start bit of another byte; continue accumulating.
- `1` -> frame terminated (the 2 terminal idle cells); dispatch by byte count.

Note this is exactly what `TCLK_DESERIALIZER2`'s `110` acquisition pattern already
keys on: idle `1 1` then start `0`. The legacy start qualifier remains valid.

### Frame types
| Payload bytes | Cells | Nominal time | Meaning | Decoder output |
|---|---|---|---|---|
| 1  | 1 start + 8 + 1 parity + 2 terminal = 12  | 1.2 us  | TCLK event       | `EVENT = {8'h00, byte0}`, no data |
| 2  | 2 x 10 + 2 terminal = 22                  | 2.2 us  | ACLK event       | `EVENT = {byte0, byte1}`, no data |
| 12 | 12 x 10 + 2 terminal = 122                | 12.2 us | Full ACLK packet | `EVENT = bytes0-1`, `DATA = bytes2-9` |

Other byte counts = malformed -> error. (A 16-bit event-only AEM transmitter mode
is "under consideration / out of scope as of March 2025".)

## Frame content (full 12-byte packet)
Logical layout (identical to the gigabit ACLK frame in ACORN-DOC-2204 Fig. 9):
```
Byte 0-1 : Event[15:0]
Byte 2-9 : Dataframe[63:0]
Byte 10  : CRC8
Byte 11  : Control Character[7:0]
```
- The Lite decoder exposes only **EVENT[15:0]** and **DATA[63:0]** (bytes 0-9). It
  consumes/ignores the CRC and control bytes internally.
- `DATA` is exactly **64 bits** and is **opaque** at the decoder layer. Its
  sub-structure (pulse ID, SMID/TMID, RF domain, buckets-since-PPS, ns-since-PPS,
  arbitrary payload) is **event-specific**, decoded by the consumer per event code,
  NOT a universal bit partition.
- **Legacy TCLK events are `0x00ZZ`** (only the low byte sent in legacy mode).
- **No reserved on-wire null code.** `0xFFFF` is just the idle/default value on the
  EVENT output bus while EVENT-valid is low; it is not a defined "no event" packet.

## Error checking (two layers)
- **Serial transport: one even-parity bit per byte.** Implementable now.
  (Waveform check: 0x9D has five 1s -> parity 1 = six = even; 0xD2 has four 1s ->
  parity 0 = even.)
- **Logical 96-bit packet: a dedicated CRC8 byte (byte 10).** The doc does NOT
  specify the polynomial / init / reflection / coverage, and does not confirm
  `0x2F`. So CRC validation is deferred: the decoder can expose EVENT/DATA and
  ignore CRC for now. Likely reusable: the gigabit `ACLK_RCV` path's
  `rtl/aclk_bridge/crc8_calc.v` (CRC-8 poly 0x2F) computes the checksum over the
  identical logical packet - confirm against the encoder RTL or a known-good
  capture before trusting it.

## Idle / spacing / decoder responsibilities
- Idle state between frames: logical-1 cells.
- Minimum start-to-start spacing: TCLK 1.2 us, event-only 2.2 us, full packet
  12.2 us. (The 12.2 us is stated in the ACLK-Lite collision section.)
- **Collision handling is encoder-side only** (the AEM buffers up to 4 events). The
  decoder just decodes the serial stream; it does not arbitrate.
- The **optional 10 MHz sync input does not affect decoding** (byte order, parity,
  termination). It is a post-decode alignment aid (gates decoded events to a precise
  local 10 MHz / beam-synchronous reference; pure oversampling has ~20 ns recovered-
  clock jitter).

## Gap vs the current `aclk_lite_decoder.sv` (why it fails on the real line)
The existing ADM is a clean-room approximation that disagrees with the real framing
on three points, which is why it decoded zero events from a real TCLK line:
1. It samples its **own** standard-Manchester with a **DC-high idle**; the real line
   is `serdec`-recoverable Manchester with **logical-1-cell idle**.
2. It uses **one parity bit over the whole payload**; the real frame uses
   **per-byte parity**.
3. It has **no CRC/control bytes**; the real full packet is 12 bytes.

## Implied design
A real-line-compatible decoder = **`serdec4_9MHz` (reused, proven)** + a new
**byte-oriented length-aware framer** that generalizes `TCLK_DESERIALIZER2`
(start-detect + 8 data + parity for one byte) to accumulate bytes until the
2-terminal-cell stop, then dispatch on byte count {1, 2, 12} into EVENT/DATA.

Generator note: the second-board signal generator currently emits the clean-room
convention (single parity, DC-high idle). To exercise the real-framing decoder
board-to-board, the generator must emit this real framing too (per-byte parity,
byte-oriented, 2 terminal idle cells, serdec-compatible Manchester).
