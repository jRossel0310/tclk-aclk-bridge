# TCLK Configurable Event Filter — Design

- **Date:** 2026-06-17
- **Status:** Approved (brainstorming)
- **Branch:** `tclk-event-filter` (off `main` @ `4dbb12b`, the working-readout milestone)

## Problem / motivation

The TCLK link broadcasts ~800 events/sec, dominated by `0x07` (720 Hz machine
clock). These high-rate "clock" codes (`0x07` 720 Hz, `0x0F` 15 Hz, `0xBA` 20 Hz,
`0x8F` 1 Hz, ...) are health/heartbeat markers, not the "real" events (resets,
beam events) we want to publish to Redis eventually.

Buffering every clock tick:
- wastes the small 64-deep FIFO and AXI bandwidth,
- risks FIFO overflow under reader jitter (a >89 ms PS hiccup overflows it),
- floods the downstream Redis stream with noise.

(Note: the overflow seen with the current debug reader is *mostly* the per-event
`print` over SSH, not the AXI reads — but filtering is still the right call for a
clean Redis stream and a safety margin.)

**Goal:** suppress a **runtime-configurable** set of event codes at the hardware
FIFO input, while still **counting** them so the clock's presence/rate stays
observable. Only the "real" events get buffered for the PS → Redis path.

## Approach

**HW configurable drop-mask + count.** This generalizes the existing `DROP_NULL`
(count-but-drop) pattern from "`0xFF` only" to "any PS-chosen set of codes."

Alternatives considered and rejected:
- **HW fixed drop** — simplest, but changing which codes are dropped needs a
  Vivado rebuild. Too inflexible for research bring-up.
- **SW-only filter + deeper FIFO** — maximally flexible, but the FIFO and AXI
  still carry the full 800/sec, and the FIFO depth is a fixed HW choice.

## Architecture / components

### Drop mask (256-bit)
- `logic [255:0] drop_mask` in `aclk_readout_axi`, one bit per event code
  `0x00`–`0xFF`.
- **Reset = all zeros = drop nothing = today's exact behavior** (safe default;
  the feature is inert until the PS configures it).
- Written in the `s_axi_aclk` (AXI) domain by the write FSM.

### `FILTER_CFG` register @ `0xD0` (write-only)
- Write-data format: `bits[7:0] = event code`, `bit[8] = action` (1 = drop,
  0 = keep). Upper bits unused.
- On write: `drop_mask[wdata[7:0]] <= wdata[8]`.
- Examples: write `0x107` → start dropping `0x07`; write `0x007` → keep it again.
- Write-only — the PS knows its own config, so no mask readback (YAGNI).
- **Replaces** the temporary `CONST` register currently at `0xD0`.

### Filter logic (`rx_clk` domain, at the FIFO input)
- The decoded event arrives in `rx_clk` with its 8-bit code.
- `drop_this = drop_mask_rxsync[event_code]`.
- Push to FIFO iff `aclk_valid && !core_is_null && !drop_this`.
- If `aclk_valid && drop_this` (and not a null): bump `FILTERED_COUNT`, **do not
  push**. Mirrors/extends the existing `DROP_NULL` path.

### Mask CDC (AXI → rx)
- `drop_mask` is quasi-static config (set once at startup, changes rarely).
- Synchronize the 256-bit mask into `rx_clk` (2-FF per bit / `ASYNC_REG`, with a
  set_false_path in the XDC). A transient during a mid-run config change at worst
  mis-filters a single event — harmless for a drop filter.

### Counters
- `EVENT_COUNT` (`0x70`, existing): events **pushed** to the FIFO (kept) — becomes
  the "real events" rate.
- `FILTERED_COUNT` (`0xE0`, **new**, `cdc_gray_count` like `NULL_COUNT`): events
  **dropped by the mask**. With `0x07` dropped, its rate ≈ 720 Hz = the
  "clock alive @ rate" check.
- `NULL_COUNT` (`0x80`, existing): `0xFF` nulls dropped by `DROP_NULL`
  (unchanged; TCLK sets `DROP_NULL=0`, so it stays 0).
- `ERROR_COUNT` (`0x90`, existing): parity errors (unchanged).
- One **aggregate** `FILTERED_COUNT` (not per-code) — sufficient for "is the clock
  alive." Per-code counting is a future option if ever needed.

### FIFO depth
- **Unchanged** (`ADDR_WIDTH=6`, 64 deep). The filter removes the firehose, so the
  FIFO sees only the slow real events. One-line bump to 256 (`ADDR_WIDTH=8`) is
  available if we ever run *unfiltered* at full rate.

## Register map (final, 16-byte spacing, `rsel = araddr[7:4]`)

| Offset | Name           | R/W | Description                                   |
|--------|----------------|-----|-----------------------------------------------|
| `0x00` | STATUS         | R   | bit0 empty, bit1 overflow                     |
| `0x10` | EVENT          | R   | `{flags, code}` of FIFO head                  |
| `0x20` | DATA_HI        | R   | DATA[63:32]                                   |
| `0x30` | DATA_LO        | R   | DATA[31:0]                                    |
| `0x40` | TS_HI          | R   | TIMESTAMP[63:32]                              |
| `0x50` | TS_LO          | R   | TIMESTAMP[31:0]                               |
| `0x60` | POP            | W   | pop head, advance FIFO                         |
| `0x70` | EVENT_COUNT    | R   | events kept (pushed)                          |
| `0x80` | NULL_COUNT     | R   | `0xFF` nulls dropped                           |
| `0x90` | ERROR_COUNT    | R   | parity errors                                 |
| `0xA0` | DEBUG          | R   | `{sig_err, level, tclk_edges}`                |
| `0xB0` | HEARTBEAT      | R   | free-running clk_40m counter                  |
| `0xC0` | LOCK           | R   | bit0 MMCM locked                              |
| `0xD0` | **FILTER_CFG** | W   | `{action, code}` → set/clear a `drop_mask` bit |
| `0xE0` | **FILTERED_COUNT** | R | events dropped by the mask                  |
| `0xF0` | (spare)        |     |                                               |

## PS / software (`deploy/tclk_read.py`)

- Add `configure_drops(codes)` — writes `FILTER_CFG` once per code.
- Add CLI flag `--drop 07,0F,BA,8F` (comma-separated hex codes) to set the
  suppressed set at startup; default empty (keep all = current behavior).
- Show `FILTERED_COUNT` in the `[stats]` line (so the clock rate is visible).
- Future Redis bridge drains the now-lean FIFO and publishes kept events.

## Testing

- **New cocotb test** (`tb/`): program the mask to drop code A and keep code B;
  drive A and B; assert A is **absent** from the FIFO and `FILTERED_COUNT`
  incremented, while B **lands** in the FIFO and `EVENT_COUNT` incremented. With
  `mask=0`, everything is kept (regression-guards current behavior).
- **Fix existing `tb/` sims**: update their AXI register offsets from 4-byte to
  16-byte spacing (clears the known follow-up noted in commit `4dbb12b`).

## Scope

**In scope:** `FILTER_CFG` + `FILTERED_COUNT` + `drop_mask` + filter logic; remove
the temp `CONST` register (its `0xD0` slot becomes `FILTER_CFG`); `tclk_read.py`
`--drop` + stats; cocotb filter test; fix existing sim offsets.

**Out of scope (separate cleanup):** stripping the scope-pin diagnostics
(`clk40_dbg`/`clk100_dbg`/`cdc_dbg`/`dbg_hb` + their BD ports and XDC pins). They
stay for now.

## Safety / rollback

- Default (`drop_mask = 0`) = the current verified behavior; the feature is inert
  until the PS configures drops.
- Revert point: `main` @ `4dbb12b`. All feature work on branch `tclk-event-filter`.
