# TCLK -> ACLK -> ACLK-Lite end-to-end pipeline (single-board loopback)

Date: 2026-06-29
Status: approved (design)

## Goal

Tie the project's existing decoders, encoders, and the SFP/GT path into one
single-board pipeline that:

1. Receives **TCLK** on the H12 logic pin, decodes it, timestamps every event, and
   publishes it to the PS over AXI-Lite/UIO (mirroring the shipped TCLK readout).
2. Re-encodes that same TCLK event stream as a **proper gigabit ACLK** signal and
   transmits it over the SFP optical link (GTH transceiver).
3. Receives the ACLK back into the **same board** over the SFP link (physical fiber
   loopback), decodes it, timestamps every event against a **shared timebase**, and
   publishes it to the PS over a **second** AXI-Lite/UIO node.
4. Re-encodes the decoded-back ACLK events as **ACLK-Lite** (Manchester baseband) and
   drives them out on a **Pmod 1** pin as a scope/probe point.

The deliverable is a single integrated bitstream plus the build/deploy glue to expose
two independent UIO readouts (`/dev/uio4` = TCLK-in, `/dev/uio5` = ACLK-back). Redis
publishing is explicitly **out of scope** here; both event streams are made available
over UIO exactly as the existing readouts are, and a Redis publisher will be a separate
Python tool developed later.

The scientific point of the loop is end-to-end measurability: with a shared timebase,
`ts(ACLK-back) - ts(TCLK-in)` for a given event is the true
H12 -> decode -> encode -> SFP -> decode round-trip latency, computed on the PS side.

## Background and reuse

Recon across the repo (TCLK decode, gigabit ACLK encode/decode, ACLK-Lite encode,
build/BD/UIO, PS-side, pinout) established that almost every block already exists and
is hardware-proven. This project is mostly integration plus one new encoder.

Reused unchanged:

- **`TCLK_RCV`** (`rtl/aclk_bridge/TCLK_RCV.v`, = `serdec4_9MHz` + `TCLK_DESERIALIZER2`):
  H12 biphase-mark -> `DATA[7:0]` + `DAVn` strobe in the `clk_40m` domain. This is the
  shipped TCLK decoder that already reads live Fermilab TCLK.
- **`ACLK_RCV`** (`rtl/aclk_bridge/ACLK_REV.v`): GT `DATA_FROM_XCVR[15:0]` + `K[1:0]`
  -> `ACLK_EVENT[15:0]` + `ACLK_DATA[63:0]` + `ACLK_VALID`/`ACLK_ERROR`/`RX_ALIGNED_OUT`.
  96-bit frame `{0xBC, EVENT, DATA, CRC8}`, CRC-8 poly `0x2F`, decode gated on CRC==0.
- **`aclk_readout_axi`** + **`aclk_readout_core`** (`rtl/aclk_readout/`): timestamping
  packer + dual-clock async FIFO + AXI4-Lite register block. 16-byte register spacing
  (LPD aliasing workaround). Register map: STATUS/EVENT/DATA_HI/DATA_LO/TS_HI/TS_LO/POP/
  EVENT_COUNT/NULL_COUNT/ERROR_COUNT/DEBUG/HEARTBEAT/LOCK/FILTER_CFG/FILTERED_COUNT.
- **`aclk_lite_encoder`** (`rtl/aclk_lite/aclk_lite_encoder.sv`): `event_id[15:0]`,
  `data[63:0]`, `frame_type[1:0]`, `start` -> real-framing Manchester `line` + `busy`.
  `frame_type=2` = full 12-byte packet (event 0-1, data 2-9, CRC 10, control 11).
- **`aclkgt_gt`** GT IP (`vivado/ip/gen_aclkgt_gt.tcl`, GTHE4 bank 224, 1.25 Gbps,
  156.25 MHz refclk, 8b10b, K28.5 comma, QPLL0, RX_EQ=LPM, RX elastic buffer enabled)
  plus the SFP wiring, `sfp_tx_disable=0`, and the self-healing RX recovery FSM from the
  endurance-proven `rtl/aclk_gt_selftest_bd_top.v`.
- **`aclk_gt_readout_top.sv`**: `ACLK_RCV` + adapter (`flags=0x0001`, `DROP_NULL=1`) +
  `aclk_readout_axi`. Already takes a `dec_rstn` decoder reset.
- Support: `crc8_calc`, `gearbox_96_to_16`, `GEARBOX_16_TO_96`, `cdc_gray_count`,
  `async_fifo`, `synchronizer`.

The authoritative reference for the new encoder is **`rtl/Li_Files/ACLK_DATA_SOURCE.v`**
(Evan Milton's TCLK->ACLK stimulus). It instantiates a `TCLK_RCV`, maintains a 256-entry
per-event-code count RAM, and for each decoded TCLK event emits an 80-bit payload
`{8'h00, event_code[7:0], 32'h0, count[31:0]}`, framed as `{0xBC, payload, CRC8}` with
`0xFF...FF` null frames filling the gaps, gearboxed 96->16 to the GT. `rtl/Li_Files/
top_module.v` shows Evan's own loop decoding it with `ACLK_RCV` and treating
`aclk_event[7:0]==0xFF` as null (`real_event_valid = aclk_valid && event[7:0]!=0xFF`).
This payload maps directly onto our `ACLK_RCV`: `EVENT={0x00,event_code}`,
`DATA={32'h0,count}`.

## Architecture

Single board, single bitstream. ASCII overview:

```
        pl_clk0 (100 MHz): AXI slaves + GLOBAL TIMEBASE (10 ns ticks)

 H12 --> TCLK_RCV --{DATA,DAVn}--+--> tclk_readout_top --> aclk_readout_axi #1 --> /dev/uio4
 (clk_40m/clk_80m)              |        (rx_clk=clk_40m, ts <- shared timebase)   (TCLK-in)
                                |
                                +--> aclk_tclk_encoder --> GT TX --> SFP --> [fiber loop]
                                     (tx_usrclk2 62.5M;        |                    |
                                      count RAM, null-fill,    |                    v
                                      CRC8, 96->16 gearbox)    |              SFP --> GT RX
                                                               |              (rx_usrclk2 62.5M)
                                                               |                    |
                                                               |                    v
                                                               |               ACLK_RCV
                                                               |          {EVENT,DATA,VALID}
                                  +----------------------------+--------------------+
                                  v                                                 v
                        aclk_gt_readout_top --> aclk_readout_axi #2          aclk_lite_bridge
                        (rx_clk=rx_usrclk2,        --> /dev/uio5             (real-events only,
                         ts <- shared timebase)    (ACLK-back)               rx_usrclk2->clk_80m)
                                                                                    |
                                                                                    v
                                                                            aclk_lite_encoder
                                                                            (frame_type=2 full)
                                                                                    |
                                                                                    v
                                                                       Pmod1 pin B10 (LVCMOS33)
```

## New / changed RTL

### `aclk_tclk_encoder.v` (new)

Simmable refactor of `ACLK_DATA_SOURCE.v`. Takes the **shared** TCLK decode stream as
input (no internal `TCLK_RCV`), so the H12 decoder is instantiated once.

```
module aclk_tclk_encoder (
    input  wire        clk_tx,       // tx_usrclk2 (~62.5 MHz)
    input  wire        rstn_tx,
    input  wire [7:0]  tclk_data,    // from shared TCLK_RCV (clk_40m domain)
    input  wire        tclk_davn,    // active-low strobe (clk_40m domain)
    output wire [15:0] data16,       // -> GT gtwiz_userdata_tx_in
    output wire [1:0]  k_out,        // -> GT txctrl2_in[1:0]
    output wire        marker        // 1 pulse per emitted frame (debug)
);
```

Internals (from Evan, made Icarus-simmable):
- DAVn-toggle CDC `clk_40m -> clk_tx` (capture `tclk_data` on the synchronized edge).
- 256 x 32-bit **inferred** dual-port count RAM (replaces `blk_mem_gen_0`); startup
  zeroing sweep walks addresses 0..0xFF writing 0 before counting begins.
- On a captured event: read `count[event]`, increment, write back; build
  `tclk_packet = {8'h00, event, 32'h0, count}`.
- 6-cycle frame cadence (`lfsr_adv` 0..5): at adv0 latch `aclk_packet = pending ?
  tclk_packet : 80'hFF...FF`; at adv1 run `CRC8_CALC` over `{aclk_packet, 8'h00}`.
- Assemble `{0xBC, aclk_packet, CRC8}`, `k = 12'b1000_0000_0000`, feed
  `gearbox_96_to_16` -> `data16` + `k_out`.
- Drop the `LFSR80` PRPG and `ERROR_INPUTn` bit-error injection (tie inactive).

One TCLK event produces exactly one non-null frame; null frames fill the rest. Nulls
are dropped at readout #2 (`DROP_NULL=1`). This is the endurance-proven free-running
cadence; an inter-frame gap is intentionally NOT added (kept identical to the proven
selftest stream).

### `global_timebase.v` (new) + `aclk_readout_core.sv` (small change)

One free-running 64-bit binary counter in `pl_clk0` (10 ns ticks), exposed as a gray
word. Each readout's event domain gets a 2-FF synchronizer + gray->binary, yielding the
same timebase value in both `clk_40m` and `rx_usrclk2`. `aclk_readout_core` gains a
`USE_EXT_TS` parameter and `ts_ext[63:0]` input; `ts = USE_EXT_TS ? ts_ext : internal`.
The `USE_EXT_TS` parameter and `ts_ext` port are threaded up through `aclk_readout_axi`
and the two readout tops (`tclk_readout_top`, `aclk_gt_readout_top`); both set
`USE_EXT_TS=1` and the integration top performs the per-domain sync + gray->binary before
feeding `ts_ext`. Existing builds default `USE_EXT_TS=0` and keep the internal counter
(backward compatible).

The gray counter / sync follows the existing `cdc_gray_count` pattern (single bit
changes per source tick, so the 64-bit gray word is CDC-safe). A few-cycle sync
uncertainty (tens of ns) is negligible against the microsecond-scale loop latency.

### `aclk_lite_bridge.v` (new)

Drives the ACLK-Lite encoder from the decoded-back ACLK events.

```
module aclk_lite_bridge (
    input  wire        rx_clk,           // rx_usrclk2 (~62.5 MHz)
    input  wire        rx_rstn,
    input  wire        aclk_valid,       // ACLK_RCV
    input  wire [15:0] aclk_event,
    input  wire [63:0] aclk_data,
    input  wire        enc_clk,          // clk_80m (encoder clock)
    input  wire        enc_rstn,
    output wire [15:0] enc_event_id,
    output wire [63:0] enc_data,
    output wire [1:0]  enc_frame_type,   // = 2 (full 12-byte)
    output wire        enc_start,
    input  wire        enc_busy,
    output wire [15:0] dropped_count     // events lost to FIFO-full (debug)
);
```

Filters real events (`aclk_valid && aclk_event[7:0]!=0xFF`), crosses them through a
small async FIFO (`rx_usrclk2 -> clk_80m`), and pulses `enc_start` with `frame_type=2`
when `!enc_busy`. FIFO-full drops + counts (TCLK events are far slower than the ~12 us
full-frame encode time, so drops should be zero in practice).

### `aclk_pipeline_bd_top.v` (new integrated top)

Single block-design top. External ports: `tclk` (H12), `gt_refclk_p/n`, `gt_rxp/n`,
`gt_txp/n`, `freerun_50`, `rstn`, `sfp_tx_disable`/`sfp_tx_fault`/`sfp_rx_los`/
`sfp_mod_abs`, `aclk_lite_out` (B10), debug pins, and **two AXI4-Lite slave interfaces**
`S_AXI0` (TCLK readout) and `S_AXI1` (ACLK readout) sharing `s_axi_aclk`/`s_axi_aresetn`
(both `pl_clk0`). Instantiates: one `TCLK_RCV`; `tclk_readout_top` -> `aclk_readout_axi`
#1; `aclk_tclk_encoder`; `aclkgt_gt` + recovery FSM + `sfp_tx_disable=0` (lifted from the
selftest top); `aclk_gt_readout_top` -> `aclk_readout_axi` #2; `aclk_lite_bridge` +
`aclk_lite_encoder`; `global_timebase`. Carries the GT-health DEBUG word (0xA0) into
readout #2 as in the selftest top.

## Shared timebase and latency

Both readouts expose the existing 64-bit `TS_HI`/`TS_LO`, now both counting `pl_clk0`
ticks. No new register is needed: the PS computes
`latency = ts(uio5 event) - ts(uio4 event)` for matched events. Events are paired by
event code plus arrival order; the per-event `count` carried in `DATA` (low 32 bits)
further disambiguates which TCLK occurrence a given ACLK-back frame corresponds to.

## Clock domains, CDC, reset

| Clock | Freq | Use |
|---|---|---|
| `pl_clk0` | 100 MHz | AXI SmartConnect + both AXI slaves + global timebase |
| `clk_80m` | 80 MHz | `serdec4_9MHz` oversample + ACLK-Lite encoder |
| `clk_40m` | 40 MHz | TCLK byte framer + readout #1 event domain |
| `gt_refclk` | 156.25 MHz | GT QPLL reference (free-running at power-up) |
| `tx_usrclk2` | 62.5 MHz | `aclk_tclk_encoder` framing -> GT TX |
| `rx_usrclk2` | 62.5 MHz | `ACLK_RCV` + readout #2 event domain |

CDCs (all gray-counter or async-FIFO, matching existing style):
- TCLK event `clk_40m -> tx_usrclk2` (encoder DAVn-toggle sync).
- Global timebase `pl_clk0 -> clk_40m` and `pl_clk0 -> rx_usrclk2` (gray word sync).
- ACLK-back `rx_usrclk2 -> clk_80m` (bridge async FIFO).
- Both readouts event-domain -> `pl_clk0` (existing FIFO + `cdc_gray_count`).

`clk_80m`/`clk_40m` come from one MMCM off `pl_clk0` (the proven `clk_wiz` topology,
`dcm_locked` tied high). The XDC extends `set_clock_groups -asynchronous` to cover every
async pair (union of the existing TCLK `async_ps_vs_rx` group and the GT
`async_pl_vs_gt` group). Resets are per-domain async-assert / sync-deassert as today.

## Build, address map, deploy, pins

- New `vivado/build_aclk_pipeline.tcl`, `design_name=uart_echo_bd` (overlay/UIO node
  identity preserved). SmartConnect `CONFIG.NUM_SI=1`, `CONFIG.NUM_MI=2`.
  `assign_bd_address -offset 0x8000_0000 -range 0x10000` for `S_AXI0` (TCLK) and
  `0x8001_0000` for `S_AXI1` (ACLK).
- Device-tree overlay with two `generic-uio` nodes (`...@80000000`, `...@80010000`);
  load with `fpgautil`. `/dev/uio4` = TCLK-in, `/dev/uio5` = ACLK-back (exact indices
  confirmed on the board).
- Deploy readers (unchanged, stdlib only): `tclk_read.py /dev/uio4`,
  `aclk_read.py /dev/uio5`. 16-byte register spacing unchanged.
- New constraints file `constraints/kr260_aclk_pipeline.xdc`:

| Signal | LOC | IOSTANDARD |
|---|---|---|
| `tclk` | H12 | LVCMOS33 (Pmod1 pin 1) |
| `gt_txp` / `gt_txn` | R4 / R3 | - (MGTHTXP2/N2_224) |
| `gt_rxp` / `gt_rxn` | T2 / T1 | - (MGTHRXP2/N2_224) |
| `gt_refclk_p` / `gt_refclk_n` | Y6 / Y5 | - (MGTREFCLK0_224, 156.25 MHz) |
| `sfp_tx_disable` | Y10 | LVCMOS33 SLEW SLOW DRIVE 8 (drive 0 = laser on) |
| `sfp_tx_fault` | A10 | LVCMOS33 (monitor) |
| `sfp_rx_los` | J12 | LVCMOS33 (monitor) |
| `sfp_mod_abs` | W10 | LVCMOS33 (monitor) |
| `aclk_lite_out` | B10 | LVCMOS33 (Pmod1 pin 5) |

SFP path is a **physical fiber loopback** on the one board (SFP TX port -> own SFP RX
port), reusing the endurance-validated GT configuration.

## Verification plan

### Phase A - simulation (Icarus + cocotb, TDD, sim-first)

- A1: `aclk_tclk_encoder` vs a golden model: feed a TCLK byte sequence, assert emitted
  frames are `{0xBC, {0x00,event}, {32'h0,count}, CRC8}` with correct per-event counts
  and `0xFF...FF` nulls between.
- A2: `aclk_tclk_encoder -> ACLK_RCV` agreement (pattern of `tb/aclkgt_gen_loop`):
  decoded `EVENT`/`DATA` match the fed events; `real_event_valid` excludes nulls.
- A3: `global_timebase` + CDC: monotonic, no gaps, same value observed in two
  destination domains within sync latency.
- A4: full chain: a TCLK biphase stimulus drives `TCLK_RCV`; assert readout #1 AXI
  decodes the TCLK events AND `encoder -> ACLK_RCV -> readout #2` AXI decodes the same
  events; assert both readouts' timestamps come from the shared timebase and are ordered
  (uio5 ts >= uio4 ts for a matched event).
- A5: `aclk_lite_bridge` + `aclk_lite_encoder -> clk_rcv` decode agreement (decoded-back
  ACLK event re-encoded as ACLK-Lite and recovered).

Per the project convention, cocotb tests emit matplotlib occupancy/throughput plots on
completion.

### Phase B - hardware (one integrated bitstream, incremental bring-up)

- B0: build time-clean (`All user specified timing constraints are met`); load; confirm
  two UIO nodes appear.
- B1: real TCLK on H12 -> `/dev/uio4` decodes events (the already-proven TCLK path).
- B2: fiber loopback -> `/dev/uio5` decodes the same events as `{0x00,event}` /
  `{32'h0,count}`; GT health green (reuse the aclkgt DEBUG word: `rx_los=0`,
  `rcv_aligned=1`, `disperr` not climbing, `recover` not climbing).
- B3: shared timestamps: `ts(uio5) - ts(uio4)` for matched events is stable and
  positive (the round-trip latency).
- B4: scope `aclk_lite_out` (B10) -> ACLK-Lite Manchester waveform of the decoded-back
  events; optionally loop B10 into a second board's `clk_rcv` to confirm decode.

## Out of scope

- **Redis publishing.** Events are exposed over UIO only; a Redis publisher is a later
  standalone Python tool.
- **Two-board operation and a live Fermilab ACLK fiber.** This design is single-board
  loopback; matching the real ACLK line rate is deferred (re-tune GT `TX/RX_LINE_RATE`
  only, nothing else changes).
- **DDM / SFP I2C telemetry** (would need a PL AXI-IIC core).

## Open items to confirm during planning

- The TCLK biphase stimulus for A4: reuse the existing TCLK-readout testbench stimulus,
  or the `FrameEncoder` + `BitEncoder` biphase encoder from `rtl/Li_Files/`.
- Whether `tclk_readout_top` cleanly accepts an external timestamp via the
  `aclk_readout_core` `USE_EXT_TS` path without further changes to its adapter.
- Final debug-pin assignments (heartbeats / `clk*_dbg`) on the remaining free Pmod1 pins.
