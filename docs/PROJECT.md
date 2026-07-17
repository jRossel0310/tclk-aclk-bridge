# Project status and architecture

The kria-2-hardware repo is one product: the **aclk_pipeline** bitstream plus the
board-side capture software. This page describes that pipeline, how the RTL blocks fit
together, the one build target, and what is hardware-verified vs. simulation-only. For
the operator runbook (load, wire, run a capture, get the data out) see
[OPERATIONS.md](OPERATIONS.md). For the bit-level ACLK-Lite on-wire framing see the
authoritative reference [aclk-lite-framing.md](aclk-lite-framing.md). For the full
register maps of both readouts and the WR monitor slave, see the generated interface
guide at `generated/tclk-aclk-pipeline-hardware-interface-guide.pdf`.

## The goal

Receive Fermilab accelerator timing events on one KR260, put an absolute time on each,
and get them to software, while also republishing the stream over the accelerator's
timing-link transports so the whole loop can be exercised on a single board:

- **TCLK** - the legacy ~10 MHz Manchester (biphase-mark) event clock; 8-bit event
  codes (written `$XX`).
- **ACLK** - the gigabit timing stream, carried here over the SFP+ GT transceiver.
- **ACLK-Lite** - the PIP-II down-converted Manchester stream, mirrored out a Pmod pin
  as a scope probe.
- **White Rabbit** - a 10 MHz + PPS reference that disciplines the shared `{sec, ns}`
  UTC timebase stamped onto every event.

## End-to-end architecture (the `build_aclk_pipeline` path)

```
TCLK (H12) + WR 10 MHz (E10) + WR PPS (E12)
  -> tclk_readout_top       decode TCLK, WR-timestamp each event
       -> aclk_readout_axi (S_AXI @ 0x8000_0000)  -> PS/UIO  (tclk_read.py, redis_publish.py --src tclk)
       -> aclk_tclk_encoder re-encode TCLK events into ACLK frames
            -> aclkgt_gt (GTH TX) -> SFP+ --external fiber loop--> aclkgt_gt (GTH RX)
                 -> aclk_gt_readout_top    decode ACLK, WR-timestamp on the same timeline
                      -> aclk_readout_axi (S_AXI2 @ 0x8001_0000) -> PS/UIO (aclk_read.py, redis_publish.py --src aclk)
                      -> aclk_lite_bridge -> aclk_lite_encoder -> ACLK-Lite Manchester out (B10)

wr_timebase (x2 replicas) + wr_timebase_axi (S_AXI3 @ 0x8002_0000, wr_time.py)
  disciplines the shared {sec, ns} that both readouts stamp.
```

- **TCLK decode.** `serdec4_9MHz` recovers biphase-mark cells (80 MHz oversample);
  `TCLK_DESERIALIZER2` + `TCLK_RCV` frame them into 8-bit TCLK events.
  `tclk_readout_top` wraps that decoder plus the shared readout and the WR timestamp.
- **WR timebase.** `wr_timebase` turns the WR 10 MHz + PPS into a free-running
  `{sec, ns}` counter with strict validity (a timestamp of 0 means "not WR-synced when
  stamped", surfaced to software as UNSYNC). Two `wr_timebase` replicas feed the two
  readouts; `wr_timebase_axi` is the monitor / arm register slave.
- **Readout.** Both readouts share `aclk_readout_core.sv` (64-bit hardware timestamp
  latched per event + a null-drop packer + a dual-clock `async_fifo`) and
  `aclk_readout_axi.sv` (the AXI4-Lite register block + a 256-bit event drop-mask
  filter). Registers are spaced **16 bytes apart** because the hand-written
  module-reference AXI4-Lite slave only returns data at 16-byte-aligned offsets on the
  KR260 LPD path.
- **TCLK -> ACLK re-encode.** `aclk_tclk_encoder` gearboxes decoded TCLK events into
  ACLK frames (8b10b, CRC-8, the gearbox pair) for the GT.
- **GT / SFP.** `aclkgt_gt` is the Xilinx GT wizard IP (GTH, 1.25 Gbps, 8b10b, 156.25
  MHz refclk), committed as a generated `.xci` under `vivado/ip/aclkgt_gt/`. It TX's the
  re-encoded ACLK out the SFP+; an external fiber jumper loops TX back to RX.
- **ACLK decode.** `ACLK_REV.v` (`ACLK_RCV`) + the gearboxes + `crc8_calc` decode the
  received ACLK; `aclk_gt_readout_top` wraps that with the second readout and WR stamp.
- **ACLK-Lite mirror.** `aclk_lite_bridge` adapts decoded ACLK events into the ACLK-Lite
  encoder's interface; `aclk_lite_encoder` drives the biphase-mark Manchester output on
  Pmod pin B10 as a scope probe.

The three AXI4-Lite slaves (`S_AXI`, `S_AXI2`, `S_AXI3`) are inferred from the integrated
`aclk_pipeline_bd_top.v` and fanned out from the PS LPD master by a single SmartConnect.
The pinout is in [OPERATIONS.md](OPERATIONS.md) section 5.

### `aclk_readout_axi` register map (both readouts, 16-byte spacing)

| Offset | Reg | | Offset | Reg |
|--------|-----|-|--------|-----|
| 0x00 | STATUS (empty, overflow) | | 0x80 | NULL_COUNT |
| 0x10 | EVENT `{FLAGS, EVENT}` | | 0x90 | ERROR_COUNT |
| 0x20 | DATA_HI | | 0xA0 | DEBUG (line / GT activity) |
| 0x30 | DATA_LO | | 0xB0 | HEARTBEAT (rx clock alive) |
| 0x40 | TS_HI | | 0xC0 | LOCK (MMCM / WR locked) |
| 0x50 | TS_LO | | 0xD0 | FILTER_CFG (W: drop-mask) |
| 0x60 | POP (W) | | 0xE0 | FILTERED_COUNT |
| 0x70 | EVENT_COUNT | | | |

Full field-level detail for every register (both readouts, the WR monitor slave, the
GT-health DEBUG word, the GT_CTRL bits) is in the generated interface guide.

## Build target (`vivado/`)

There is **one** build. `hw.ps1 build` defaults to it; a bare `.\hw.ps1 build` is all
you need.

| TCL | Name | What it builds |
|-----|------|----------------|
| `build_aclk_pipeline.tcl` | aclk_pipeline | The integrated single-board TCLK -> WR-timestamp -> ACLK(SFP loop) -> ACLK-Lite pipeline, three AXI4-Lite readout/monitor slaves |

The block design keeps the historical internal name `design_name = uart_echo_bd`, so the
bitstream file is **`uart_echo_bd_wrapper.bit.bin`** and the board overlay/UIO identity is
unchanged. The name is cosmetic. The build derives its 80/40 MHz event-domain clocks and a
50 MHz GT free-run clock from `pl_clk0` with clk_wiz MMCMs (a runtime `fpgautil` load does
not reprogram PS PL clocks), ties the proc_sys_reset `dcm_locked` high, and uses an AXI
SmartConnect on the LPD master (the auto interconnect corrupts AXI4->AXI4-Lite read data on
this hardware).

## Module-to-file map (what the bitstream is built from)

`vivado/build_aclk_pipeline.tcl` sources exactly these RTL files plus the GT IP:

| File | Role |
|------|------|
| `rtl/aclk_bridge/serdec4_9MHz.v` | biphase-mark bit recovery (80 MHz oversample) |
| `rtl/aclk_bridge/TCLK_DESERIALIZER2.v`, `TCLK_RCV.v` | TCLK byte framing -> 8-bit events |
| `rtl/aclk_bridge/ACLK_REV.v` (`ACLK_RCV`) | gigabit ACLK decode over the GT (8b10b) |
| `rtl/aclk_bridge/GEARBOX_16_TO_96.v`, `gearbox_96_to_16.v`, `crc8_calc.v` | ACLK encode/decode gearboxes + CRC-8 |
| `rtl/aclk_lite/tclk_readout_top.sv` | TCLK decode + WR timestamp + readout top |
| `rtl/aclk_gt/aclk_gt_readout_top.sv` | ACLK decode + WR timestamp + readout top |
| `rtl/aclk_gt/aclk_tclk_encoder.v` | TCLK event -> ACLK frame re-encoder |
| `rtl/aclk_lite_bridge.v` | ACLK event -> ACLK-Lite encoder adapter |
| `rtl/aclk_lite/aclk_lite_encoder.sv` | ACLK-Lite biphase-mark Manchester output |
| `rtl/wr_timebase.sv`, `wr_timebase_axi.sv` | shared WR `{sec, ns}` timebase + monitor slave |
| `rtl/aclk_readout/aclk_readout_core.sv`, `aclk_readout_axi.sv` | shared readout core + AXI4-Lite face |
| `rtl/synchronizer.sv`, `async_fifo.sv`, `cdc_gray_count.sv`, `cdc_word_pulse.sv` | CDC primitives |
| `rtl/aclk_pipeline_bd_top.v` | integrated block-design top (infers the 3 AXI slaves) |
| `vivado/ip/aclkgt_gt/aclkgt_gt.xci` | GT wizard IP (GTH, 1.25 Gbps, 8b10b, 156.25 MHz refclk) |

**Testbench-support only (not in the bitstream):** `rtl/aclk_lite/clk_rcv.sv` +
`rtl/aclk_lite/clk_byte_framer.sv` are a unified TCLK/ACLK-Lite baseband decoder used by
`tb/aclk_lite_bridge` to feed the bridge with realistically framed events. They are not
sourced by the build and are not part of the pipeline hardware.

## What is verified, where

- **HW-verified:** the full pipeline ran a **15.6 h dual-source capture on 2026-07-16**
  (5.55 M events/source, zero loss), decoding real Fermilab TCLK, looping ACLK over the
  SFP fiber, and publishing both readouts on the shared WR timeline into Redis. This is
  the load-bearing hardware validation. TCLK decode, the WR timebase arm/lock, the GT
  SFP loop, and both readouts are all exercised by it.
- **Sim-only:** the cocotb suite is the inner loop. `tb/aclk_pipeline_chain` exercises
  the end-to-end chain; per-block testbenches cover `tclk_rcv`, `aclk_rcv`,
  `aclk_readout_axi`, `async_fifo`, `aclk_lite_encoder`, `aclk_lite_bridge`,
  `wr_timebase`, and the encoders.
- **Deferred / open:** ACLK CRC-8 poly confirmation against a real accelerator ACLK
  source (today ACLK is exercised only via the board's own re-encode + fiber loop, not a
  live upstream ACLK feed).

## Testing

Simulation is the inner loop: cocotb 2.0 + Icarus, one `tb/<module>/` per module, each
emitting a matplotlib plot. `.\sim.ps1 list` lists the testbenches; `.\sim.ps1 run
-Module <tb>` runs one. The board-side Python has its own unit tests: `pytest deploy` on
the PC exercises the readout register map, the Redis sink/publisher, the stats
reconciliation, and the plotting.

## History (specs + plans)

Each feature went through brainstorm -> spec -> plan -> subagent-driven TDD: the TCLK
readout bring-up and event filter, the ACLK-Lite readout and signal generator, the
unified clk decoder, the GT/SFP ACLK readout, the White Rabbit timestamp, the Redis
publisher and convention alignment, and the single-board pipeline integration. The
process records themselves are not part of the tracked repo.
