# Project status and architecture

Snapshot of the kria-2-hardware timing-readout project as of 2026-06-22: what each
piece is, how they fit together, which builds exist, and what is hardware-verified
vs. simulation-only vs. legacy. For the bit-level on-wire framing, see the
authoritative reference [aclk-lite-framing.md](aclk-lite-framing.md).

## The goal

Receive Fermilab accelerator timing events on a KR260 and get them to software:
- **TCLK** - the legacy 10 MHz Manchester (biphase-mark) event clock; 8-bit events.
- **ACLK-Lite** - the PIP-II down-converted timing stream, same Manchester line code
  as TCLK; 16-bit events optionally carrying a 64-bit data packet.

Both arrive on one baseband line (Pmod pin **H12**, LVCMOS33). A single decoder reads
either, timestamps each event in the PL, buffers it across the clock-domain crossing,
and exposes it to the PS over AXI4-Lite (read via a UIO device in Linux).

## End-to-end architecture (the current `build_clk` path)

```
H12 line ─► serdec4_9MHz ─► clk_byte_framer ─► clk_readout_top ─► aclk_readout_axi ─► AXI4-Lite ─► PS/UIO ─► clk_read.py
            (80 MHz, bit     (40 MHz, byte      (adapter +          (timestamp +
             recovery)        framing +          flags)              async FIFO +
                              length detect)                         register block)
```

- **`rtl/aclk_bridge/serdec4_9MHz.v`** - inherited, hardware-proven biphase-mark bit
  recovery. Oversamples the line at 80 MHz and emits a recovered bit clock (`SCLK`) +
  data (`SDATA`). Frame-agnostic: it just recovers cells.
- **`rtl/aclk_lite/clk_byte_framer.sv`** - the length-aware byte framer (40 MHz).
  Accumulates bytes (each = start + 8 data MSB-first + even parity) until the
  2-terminal-idle-cell stop, then dispatches by byte count: 1 byte = TCLK event
  `{0x00, b0}`; 2 = ACLK event `{b0, b1}`; 12 = ACLK event + 64-bit data (bytes 2-9;
  the CRC byte 10 and control byte 11 are captured but ignored). Outputs
  `event_valid / event_id[15:0] / data_valid / data[63:0] / parity_error / is_tclk`.
- **`rtl/aclk_lite/clk_rcv.sv`** - thin wrapper = `serdec4_9MHz` + `clk_byte_framer`.
- **`rtl/aclk_lite/clk_readout_top.sv`** - adapter (sets `flags = {is_tclk, has_data}`)
  + the shared readout; `DROP_NULL=0` (no on-wire null code, keep every event).
- **`rtl/aclk_readout/aclk_readout_core.sv`** - 64-bit hardware timestamp counter
  (free-running in the rx domain, latched per event; a `pps` input can zero it) +
  null-drop packer + dual-clock `async_fifo`. Packs a 160-bit FIFO word
  `{FLAGS, TS[63:0], EVENT[15:0], DATA[63:0]}`.
- **`rtl/aclk_readout/aclk_readout_axi.sv`** - the AXI4-Lite face: a small read-mostly
  register block + a 256-bit event drop-mask filter. **Registers are spaced 16 bytes
  apart** (a hardware quirk: the hand-written module-reference AXI4-Lite slave only
  returns data at 16-byte-aligned offsets on the KR260 LPD path). Map:

  | Offset | Reg | | Offset | Reg |
  |--------|-----|-|--------|-----|
  | 0x00 | STATUS (empty, overflow) | | 0x80 | NULL_COUNT |
  | 0x10 | EVENT `{FLAGS, EVENT}` | | 0x90 | ERROR_COUNT |
  | 0x20 | DATA_HI | | 0xA0 | DEBUG (line activity) |
  | 0x30 | DATA_LO | | 0xB0 | HEARTBEAT (rx clock alive) |
  | 0x40 | TS_HI | | 0xC0 | LOCK (MMCM locked) |
  | 0x50 | TS_LO | | 0xD0 | FILTER_CFG (W: drop-mask) |
  | 0x60 | POP (W) | | 0xE0 | FILTERED_COUNT |
  | 0x70 | EVENT_COUNT | | | |

## Decoders in the tree (and which one is current)

| Decoder | What | Status |
|---------|------|--------|
| `clk_byte_framer` + `clk_rcv` | **Unified** real-line decoder (TCLK + ACLK-Lite via serdec) | **Current; HW-verified** |
| `aclk_bridge/TCLK_RCV.v` (serdec + `TCLK_DESERIALIZER2`) | Biphase TCLK, 1-byte events | HW-proven; reused by `build_tclk` |
| `aclk_lite/aclk_lite_decoder.sv` | Clean-room Manchester ADM (own recovery, single parity) | Legacy; decodes only the old clean-room generator, not the real line |
| `aclk_bridge/ACLK_REV.v` (`ACLK_RCV`) | Gigabit ACLK over a GT transceiver (8b10b, gearbox, CRC-8) | Sim-only; no GT front-end wired on the KR260 |

The clean-room `aclk_lite_decoder` is now superseded by `clk_byte_framer` and kept
only for reference; retiring it (and its `manchester_tx_model.py`) is open cleanup.

## The ACLK-Lite signal generator (`build_aclkgen`)

A second KR260 transmits a hardcoded test stream so the receiver can be exercised
without the real accelerator. **HW-verified board-to-board** against `build_clk`.

- **`rtl/aclk_lite/aclk_lite_encoder.sv`** - biphase-mark cell engine emitting the real
  ISD framing (per-byte start + 8 MSB-first + even parity, byte-oriented, idle =
  continuous 1-cells), `frame_type` selects 1/2/12-byte frames; CRC/control bytes are
  fixed 0x00 placeholders (the decoder ignores them).
- **`rtl/aclk_lite/aclk_lite_gen_timeline.sv`** - drives the encoder with a repeating
  trio: TCLK `0x55`, ACLK event `0xABCD`, full packet `0x1234` + `0xDEADBEEFCAFE0001`;
  emits a `frame_sync` scope trigger; one-shot warm-up lets the receiver's serdec lock.
- Output on H12; wire the generator board's H12 to the receiver board's H12.

## Build targets (`vivado/`)

All builds reuse `design_name = uart_echo_bd`, so every bitstream is named
`uart_echo_bd_wrapper.bit.bin` and loads with the same overlay. **md5-check on the
board to tell builds apart.** Each build derives its PL clocks from `pl_clk0` with a
clk_wiz MMCM (a runtime fpgautil load does not reprogram PS PL clocks), ties the
proc_sys_reset `dcm_locked` high, and uses an AXI SmartConnect on the LPD master.

| TCL | Name | What it builds | Status |
|-----|------|----------------|--------|
| `build_clk.tcl` | clk | **Unified TCLK/ACLK receiver** (serdec + clk_byte_framer + readout), H12 in | **Current; HW-verified** |
| `build_aclkgen.tcl` | aclkgen | **ACLK-Lite generator**, H12 out (no AXI) | **Current; HW-verified** |
| `build_tclk.tcl` | tclk | TCLK-only receiver (TCLK_RCV + readout) | Superseded by clk; HW-proven |
| `build_aclk.tcl` | aclk | ACLK-Lite receiver via the clean-room `aclk_lite_decoder` | Superseded; reads only the old generator |
| `build_pltest.tcl` | pltest | PL heartbeat / AXI bring-up smoke test | Bring-up scaffold |
| `build_pinblink.tcl` | pinblink | LED/pin blink | Bring-up scaffold |
| `build.tcl` + `uart_echo_bd.tcl` | uart_echo | Original UART echo loopback | Origin skeleton |

## Deploy + readers (`deploy/`)

- **`clk_read.py`** - current reader for `build_clk`; drains the register block, prints
  events with `is_tclk` / `has_data`, 40 MHz (25 ns) timestamp tick. `--drop 07,0F`
  sets the hardware drop-mask via `tclk_filter.py`. Runbook: `deploy/clk.md`.
- `tclk_read.py` / `aclk_read.py` - per-build readers for the superseded tclk/aclk
  builds (40 MHz / 120 MHz ticks respectively).
- `tclk_filter.py` (+ `test_tclk_filter.py`) - drop-mask helpers, shared by the readers.
- `diag.py`, `probe.py`, `pltest.py`, `uart_echo_test.py` - bring-up diagnostics from
  earlier phases (kept for reference).
- `uart_echo.dts`, `*.bif`, `template.bif` - device-tree overlay source + bootgen
  recipes. `hw.ps1` writes the `.bif` automatically during packaging.

## Testing

Simulation is the inner loop: cocotb 2.0 + Icarus, one `tb/<module>/` per module,
each emitting a matplotlib plot. The load-bearing timing testbenches:

| Testbench | Covers |
|-----------|--------|
| `tb/clk_rcv` | unified decoder: 1/2/12-byte frames + parity errors via a real-framing TX model |
| `tb/clk_readout` | full chain decoder -> readout -> AXI |
| `tb/aclk_lite_encoder` | generator encoder waveform == golden biphase model |
| `tb/aclk_lite_gen_loopback` | generator -> serdec -> clk_byte_framer (proves TX/RX agree) |
| `tb/tclk_rcv`, `tb/aclk_rcv` | the inherited TCLK / GT-ACLK decoders |
| `tb/aclk_readout_axi`, `tb/async_fifo` | the readout + CDC FIFO |

Shared models: `tb/tclk_tx_model.py` (biphase-mark cells), `tb/clk_tx_model.py`
(real multi-byte framing), `tb/manchester_tx_model.py` (legacy clean-room),
`tb/axi_lite_bfm.py`. `aclk_lite_decoder`'s `aclk_lite_readout` chain still simulates
against the clean-room model.

## What is verified, where

- **HW-verified:** the unified receiver decodes the real lab TCLK line; the generator
  and receiver decode the ACLK-Lite trio board-to-board on H12.
- **Sim-only:** the GT-based `ACLK_RCV` gigabit path (no transceiver front-end wired).
- **Deferred / open:**
  - CRC8 validation in `clk_byte_framer` (poly unconfirmed for ACLK-Lite; decoder
    ignores the CRC byte for now - see aclk-lite-framing.md).
  - Retiring the legacy clean-room `aclk_lite_decoder` + `build_aclk` + the old
    single-protocol readers once `build_clk` fully replaces them.
  - The PS-side bridge that drains the FIFO and publishes events (e.g. to Redis).
  - A real ACLK-Lite source from the accelerator (today only the generator board).

## History (specs + plans)

Each feature went through brainstorm -> spec -> plan -> subagent-driven TDD. The
records live under `docs/superpowers/specs/` and `docs/superpowers/plans/`:
TCLK board bring-up, the TCLK event filter, the ACLK-Lite readout build, the ACLK-Lite
signal generator, the unified clk decoder, and the generator real-framing re-alignment.
