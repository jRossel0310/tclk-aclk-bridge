# Gigabit ACLK over GT/SFP: two-board readout to the PS

Date: 2026-06-23
Status: approved (design)

## Goal

Receive the **gigabit ACLK** serial timing link (8b/10b over an optical SFP, the
`ACLK_RCV` path) on the KR260 and deliver decoded events to the PS, mirroring the
shipped TCLK / ACLK-Lite readouts. Verified board-to-board with a **two-KR260
generator + receiver pair** (no Fermilab fiber required), after a single-board
**near-end loopback** milestone that proves the full PL chain with zero optics.

This is the gigabit-ACLK analog of the existing ACLK-Lite generator/receiver pair.
It is a *different physical layer* from everything shipped so far: ACLK-Lite / TCLK
arrive as a 10 MHz Manchester/biphase baseband on the H12 logic pin (recovered by
`serdec4_9MHz`); gigabit ACLK arrives through a **multi-gigabit GT transceiver**
(KR260 GTH + SFP). The logical event/data packet is identical on both interfaces.

## Background

The starter code (`rtl/Li_Files/`, byte-for-byte identical to `rtl/aclk_bridge/`,
Evan Milton's bring-up design) already contains both ends of the gigabit link:

- **`ACLK_RCV`** (`rtl/Li_Files/ACLK_REV.v`) — the receiver/decoder. Consumes a GT's
  `DATA_FROM_XCVR[15:0]` + `K_FROM_XCVR[1:0]` on the recovered RX clock; emits
  `ACLK_EVENT[15:0]` + `ACLK_DATA[63:0]` + `ACLK_VALID` + `ACLK_ERROR` +
  `RX_ALIGNED_OUT`. Internally: `GEARBOX_16_TO_96` (comma-aligned 16->96) +
  `crc8_calc` (CRC-8 poly `0x2F`) + a 5-deep align/dropout hysteresis. Decode gates
  on CRC==0. Sim-validated in `tb/aclk_rcv/` (alignment, multi-event order, bad-CRC).
- **`aclk_data_source`** (+ `gearbox_96_to_16`, `fake_data`, `lfsr80`, etc.) — the TX
  stimulus that builds 96-bit frames `{0xBC, EVENT[15:0], DATA[63:0], CRC8}` and
  serializes them to the GT's 16-bit + K interface.
- **`top_module.v`** — Evan's GT bring-up: instantiates a **GTY** GT Wizard with
  **near-end PMA loopback** (`loopback_in=3'b010`), 8b/10b on, comma detect/align on
  `0xBC` (K28.5), 16-bit user data @ a ~60 MHz user clock, 156.25 MHz refclk from an
  external 8A34001 jitter cleaner. This targets *Evan's bring-up board, not the
  KR260*, and the `gtwizard_ultrascale_0` IP is **not present** in this repo.

The shared readout already exists and is proven on hardware for TCLK and ACLK-Lite:

- **`aclk_readout_axi`** (`rtl/aclk_readout/aclk_readout_axi.sv`) — timestamping
  packer + dual-clock async FIFO + AXI4-Lite slave (LPD base `0x8000_0000`,
  16-byte-spaced register map, configurable null-drop + event drop-mask). It takes a
  generic `aclk_valid / aclk_event[15:0] / aclk_data[63:0] / flags[15:0] /
  aclk_error` event interface in a recovered-RX clock domain and crosses it to the
  AXI/PS domain. `ACLK_RCV`'s outputs map onto this almost 1:1.

So the decode brains and the readout/PS path are reuse; the genuinely new work is the
**GT transceiver front end for the KR260's GTH + SFP**, the thin tops that wire it to
`ACLK_RCV` / `aclk_data_source`, the GT-specific constraints, and the PS reader.

## Decisions (locked during brainstorming)

1. **Full board build** (not sim-only): the GT/SFP transceiver front end is in scope.
2. **Two-board generator + receiver** verification (the ACLK-Lite-pair analog), with a
   single-board **near-end loopback as Milestone 0** to de-risk the PL chain + GT
   config before any optics.
3. **GT reference clock is a research item**: the KR260 SFP GTH refclk source /
   frequency / pins are unknown and must be pinned from the carrier schematic /
   UG1091. The GT is designed refclk-parameterized until then.
4. **Match the real ACLK line rate exactly** so the same RX bitstream later works on a
   live Fermilab fiber unchanged. The exact rate is currently only inferable
   (~1.2 Gbps from Evan's 60 MHz x 16-bit x 8b10b) and must be confirmed.
5. **Mirror the existing pattern**: separate build targets per role, thin Verilog tops
   + BD cloned from `build_aclk.tcl`, the inherited decode/stimulus blocks and the
   shared `aclk_readout_axi` reused as-is.
6. **Naming**: `aclkgt` prefix (gigabit ACLK over GT/SFP) to avoid collision with the
   existing `aclk` / `aclkgen` builds (which are ACLK-Lite over the H12 pin).

## Architecture

Three staged build targets, each a thin Verilog top wrapped in a Vivado BD cloned
from `build_aclk.tcl` (Zynq PS + pl_clk0 + LPD GP2 @ `0x8000_0000` + SmartConnect +
MMCM + `dcm_locked` tied high).

```
Milestone 0 (build_aclkgt_loop, ONE board, no optics)
  aclk_data_source -> gearbox_96_to_16 -> GTH GT Wizard (loopback_in=3'b010)
       -> [internal near-end PMA loopback] -> GTH RX (16b + K @ rxusrclk2)
       -> ACLK_RCV -> adapter -> aclk_readout_axi -> PS (UIO)

Milestone 1 (build_aclkgt_rx, board A = receiver)
  SFP optical RX -> GTH GT Wizard (RX, loopback off) -> 16b + K @ rxusrclk2
       -> ACLK_RCV -> adapter -> aclk_readout_axi -> PS (UIO)

Milestone 2 (build_aclkgt_gen, board B = generator)
  aclk_data_source -> gearbox_96_to_16 -> GTH GT Wizard (TX) -> SFP optical TX
  ... fiber / copper SFP loopback to board A ...
```

Board-to-board success criterion: the event/data timeline injected at the generator
equals the events the receiver's PS reads out (id, 64-bit data, order, counts).

## Components

### New RTL (`rtl/aclk_gt/`)

- **`aclk_gt_rx_top.v`** — GTH GT Wizard (RX) + `ACLK_RCV` + adapter +
  `aclk_readout_axi`. Exposes the SFP RX pins, the GT refclk, the GT freerun/reset
  clock, `mmcm_locked`, the AXI4-Lite slave, and GT/link debug.
- **`aclk_gt_gen_top.v`** — `aclk_data_source` (+ `gearbox_96_to_16`) -> GTH GT Wizard
  (TX) -> SFP TX. Exposes the SFP TX pins, refclk, freerun clock, and debug.
- **`aclk_gt_loop_top.v`** — Milestone 0: `aclk_data_source` -> gearbox -> GTH GT
  Wizard with `loopback_in=3'b010` -> `ACLK_RCV` + adapter + `aclk_readout_axi`. RX
  pins tied off (internal loopback). Effectively gen + rx in one for single-board
  bring-up.
- **`rtl/aclk_gt/aclk_gt_*_bd_top.v`** — plain-Verilog BD wrappers with
  `X_INTERFACE` attributes for the inferred AXI4-Lite slave, mirroring
  `rtl/aclk_lite/tclk_readout_bd_top.v` / `aclk_readout_bd_top.v`.

### GT Wizard IP

A GTH UltraScale+ Transceiver Wizard core, generated in-build via tcl (like the other
BD IP). Configuration:
- GTH, KR260 SFP quad / lane (from research dependency #1).
- Line rate = parameter, target = real ACLK rate (research dependency #2).
- Reference clock frequency = parameter (research dependency #1).
- 8b/10b encode (TX) + decode (RX) enabled.
- Comma detect + plus/minus comma align on `0xBC` (K28.5).
- 16-bit user data width; 2-bit K per 16-bit word.
- TX + RX (loop/gen/rx select which paths the top uses); near-end PMA loopback
  available for Milestone 0.
- `IBUFDS_GTE4` for the differential refclk; `BUFG_GT` on the recovered RX out clock.

### Reused as-is

`ACLK_RCV` (+ `GEARBOX_16_TO_96`, `crc8_calc`), `aclk_data_source`
(+ `gearbox_96_to_16`, `fake_data`, `lfsr80`, `ack_stimulus_gen` and any stimulus
deps it pulls in), and the full `aclk_readout_axi` / `aclk_readout_core` /
`async_fifo` / `cdc_gray_count` / `synchronizer` stack.

### Adapter (`ACLK_RCV` -> readout)

```
aclk_valid = ACLK_VALID
aclk_event = ACLK_EVENT[15:0]
aclk_data  = ACLK_DATA[63:0]
flags      = 16'h0001        // has_data=1 (every gigabit packet carries 64b), is_tclk=0
aclk_error = ACLK_ERROR      // 1-cycle pulse per bad-CRC frame; NOT sticky, no edge-detect
DROP_NULL  = 1'b1            // drop + count 0xFF nulls (the ACLK convention)
pps        = optional (tied 0 until a White Rabbit PPS is wired)
```

`ACLK_VALID` / `ACLK_ERROR` are registered one-cycle pulses gated on CRC validity and
`rx_aligned`, so unlike the sticky TCLK `PERR` they feed the readout error counter
directly (no edge-detect / auto-clear needed).

DEBUG word (readout reg `0xA0`), RX-domain, synchronized: GT link health, e.g.
`{ rx_aligned, rx_byte_aligned, rx_comma_det, cdr_stable, ... , frame_count[...] }`,
analogous to the line-activity debug word on the TCLK/ACLK-Lite tops.

### Vivado builds (`vivado/`)

`build_aclkgt_loop.tcl`, `build_aclkgt_rx.tcl`, `build_aclkgt_gen.tcl`, each cloned
from `build_aclk.tcl` and adding the GT Wizard IP + `IBUFDS_GTE4` refclk path + the
corresponding `aclk_gt_*_top`. Overlay/bitstream naming reuses the established
`uart_echo_bd` convention.

### Constraints (`constraints/kr260_aclkgt.xdc`)

SFP TX/RX differential pins + GT refclk pins + GT clock constraints + async
`set_clock_groups` between the recovered-RX clock, the GT freerun clock, and pl_clk0
(same idiom as `kr260_aclk.xdc`). Seeded from the Aurora-on-KR260 reference
(GTH Quad X0Y1, refclk Y6/Y5, RX T2/T1, TX R4/R3) but **must be verified against the
KR260 carrier schematic + the selected SFP port** before trust.

### PS reader (`deploy/aclkgt_read.py`)

Mirrors `deploy/aclk_read.py`: drains the shared 16-byte register map; prints each
event's 16-bit id + 64-bit data; `TICK_NS` set for the recovered-RX user clock
(timestamp domain); reuses the drop-mask filter helper.

## Clocking and data flow

- **GT refclk** (frequency from research #1) from the carrier -> `IBUFDS_GTE4` ->
  GT Wizard.
- **Recovered RX user clock** (`rxusrclk2`, ~line_rate/20 ≈ 60 MHz at target rate) out
  of the GT -> clocks `ACLK_RCV` and the readout `rx_clk`. The async FIFO crosses this
  to the AXI domain.
- **GT freerun / reset clock** (`gtwiz_reset_clk_freerun`) from a pl_clk0-derived
  ~50-100 MHz (the BD MMCM).
- **AXI domain** = PS `s_axi_aclk` (pl_clk0).

Receiver data flow: SFP optical -> GTH (8b/10b decode, comma-align on `0xBC`) ->
16-bit + K @ rxusrclk2 -> `ACLK_RCV` (16->96 gearbox, CRC8 check, align hysteresis) ->
adapter -> timestamp + async FIFO + AXI-Lite -> PS reads `STATUS / EVENT / DATA_* /
TS_*`, writes `POP`.

## Error handling

- Bad CRC -> `ACLK_ERROR` pulse -> ERROR_COUNT++, never asserts `ACLK_VALID`, so bad
  frames never enter the FIFO.
- `0xFF` null packets -> dropped + counted (NULL_COUNT) via `DROP_NULL=1`.
- FIFO overflow -> sticky STATUS overflow bit.
- Loss of comma alignment -> `ACLK_RCV` drops `rx_aligned` after 5 bad frames; no
  events emitted while unaligned. GT link-health visible in the DEBUG word.

## Testing

The GT Wizard IP is a Vivado primitive and **does not simulate in Icarus**. Cocotb
therefore validates the RTL chain *feeding* `ACLK_RCV` (the 16-bit + K interface),
exactly as `tb/aclk_rcv/` does; the GT itself is verified on hardware at Milestone 0.

- **Unit:** reuse `tb/aclk_rcv/` (decode + alignment + bad-CRC, already green).
- **Full-chain sim `tb/aclkgt_readout/`:** drive `DATA_FROM_XCVR` / `K_FROM_XCVR` with
  the TX model (`tb/aclk_tx_model.py` + the 96->16 gearbox model used by
  `tb/aclk_rcv/`) -> `ACLK_RCV` -> adapter -> `aclk_readout_axi` -> AXI; read with
  `tb/axi_lite_bfm.py`. Check event/data/`flags(has_data=1)`/timestamps/counts, the
  null-drop path, and the bad-CRC error path. Emit the matplotlib plot (project
  convention). Keep the link fed with valid idle/null frames during readout (a frozen
  bus makes the still-aligned decoder assemble garbage, per the readout testbench
  note).
- **Generator-agreement sim `tb/aclkgt_gen_loop/`:** `aclk_data_source` +
  `gearbox_96_to_16` -> 16-bit model -> `GEARBOX_16_TO_96` -> `ACLK_RCV` -> decoded
  events == injected events. Proves the generator and receiver agree before hardware.
- **Hardware M0:** build `build_aclkgt_loop`, load on one board, read events over UIO,
  confirm decoded events match `aclk_data_source`'s injected timeline.
- **Hardware M1+M2:** flash `build_aclkgt_rx` on board A, `build_aclkgt_gen` on board
  B, connect the SFPs (fiber or copper SFP loopback), confirm board-to-board:
  injected events == received PS readout.

## Research dependencies (resolve first; they gate the GT config)

1. **KR260 SFP GTH refclk** — source, frequency, pin locations (carrier schematic /
   UG1091 / KR260 board files). GT line-rate/refclk parameters stay placeholder until
   pinned. Also confirm the SFP TX/RX GTH quad/lane + pin locations for the XDC.
2. **Real gigabit-ACLK line rate** — confirm the exact rate to honor decision #4
   (sources: Fermilab / Evan, or the original `gtwizard` `.xci` if recoverable).
3. **Physical hardware** — 2 KR260s + 2 SFP optical modules (matched wavelength) +
   fiber, or a copper/fiber SFP loopback, on hand for the two-board link.

## Risks

- **GT bring-up on optics is fiddly** (refclk quality, lane polarity/swap,
  equalization, comma alignment). Milestone 0 loopback isolates PL-chain correctness
  from optics so failures are attributable.
- **GT config only verified on hardware** (no Icarus model). Mitigate by maximizing
  the RTL-chain sim coverage feeding `ACLK_RCV`.
- **Refclk vs exact rate tension**: if the KR260 refclk cannot divide cleanly to the
  exact real ACLK rate, dependencies #1 and #2 collide. Resolution paths: program the
  onboard clock generator (if programmable) to the needed refclk, or run the two-board
  link at the closest clean rate and reconcile with the real ACLK rate before live
  fiber. The research step decides which.
- **16-byte AXI register spacing**: a known KR260 LPD quirk, already solved in
  `aclk_readout_axi` and reused unchanged.

## Out of scope

- CRC8 polynomial confirmation (already `0x2F` in `crc8_calc.v`; decode gates on
  CRC==0).
- The PS Redis bridge / event publisher (later phase).
- White Rabbit PPS wiring (`pps` tied/optional for now).
- Retiring or merging with the ACLK-Lite `aclk` / `aclkgen` builds.
- The optional jitter-cleaner (8A34001) path from Evan's bring-up board (the KR260
  carrier refclk replaces it).

## Assumptions to validate at bring-up

- The KR260 SFP GTH can be configured for the target ACLK line rate from its carrier
  refclk (research #1/#2). Until confirmed, the two-board link may run at a clean
  near-rate.
- `aclk_data_source`'s stimulus timeline is representative enough to exercise the
  receiver (event + 64-bit data + nulls + an injectable CRC error). Confirm its event
  generation during the generator-agreement sim.
- Two KR260 carriers present an identical SFP refclk, so the generator and receiver
  lock to a common rate without an external shared reference.
