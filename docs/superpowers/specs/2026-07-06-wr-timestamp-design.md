# White Rabbit Disciplined Timestamp (sec:ns) Design

Date: 2026-07-06
Status: approved (brainstorm complete)
Scope: `aclk_pipeline_bd_top` (the single-board TCLK -> ACLK -> ACLK-Lite pipeline) only.
Other bitstream tops keep the existing tick timebase and can adopt this module later.

## 1. Problem and goal

Today the pipeline stamps every TCLK and ACLK event with a free-running 64-bit tick
counter (`global_timebase` in `pl_clk0`, distributed to both event domains via
gray-code CDC). Ticks are relative and drift with the local oscillator; they cannot
be compared across runs or boards.

Goal: replace the tick timestamp with absolute wall-clock time, disciplined by a
White Rabbit (WR) node:

- A 10 MHz reference clock and a PPS (pulse per second) from the WR node arrive on
  two new Pmod 1 pins.
- NTP on the PS Linux side provides the coarse absolute time (which second it is);
  the PPS and 10 MHz provide the precise sub-second alignment.
- The packed 64-bit timestamp becomes `{sec[31:0], ns[31:0]}`: upper 32 bits are
  Unix UTC seconds, lower 32 bits are nanoseconds within the second.

## 2. Decisions made during brainstorm

| Question | Decision |
|---|---|
| Which designs | Pipeline top only (`aclk_pipeline_bd_top`) |
| ns derivation | 10 MHz edge counting (100 ns cells) + local-clock interpolation between edges |
| Seconds source | Unix UTC; PS arms the next-PPS label via AXI, hardware loads it at the PPS edge |
| WR pins | Pmod 1 pin 3 = E10 = `wr_clk10` (10 MHz), pin 4 = E12 = `wr_pps` (PPS), both LVCMOS33 |
| No/lost WR behavior | STRICT: timestamp outputs force to 64'd0 unless fully locked; re-lock requires re-arm |
| Domain delivery | One logical timeline, physically replicated per event domain (no 64-bit CDC) |
| Register home | New small AXI-Lite slave `wr_timebase_axi` on a third bus `S_AXI3` (both existing maps are full) |
| PS software | Full tooling: arm/status helper + reader scripts decode sec:ns |

## 3. Architecture

### 3.1 One timeline, replicated per domain

New module `wr_timebase`, instantiated three times in `aclk_pipeline_bd_top`,
replacing `global_timebase` there:

- `clk_40m` instance: drives `ts_ext` of the TCLK readout (`tclk_readout_top`).
- `rx_usrclk2` instance: drives `ts_ext` of the ACLK readout (`aclk_gt_readout_top`).
- `s_axi_aclk` monitor instance: backs the status registers in `wr_timebase_axi`.

All instances watch the same two physical pins, so they carry the same timeline and
cannot drift apart. Because each timestamp is generated inside the domain that
stamps events, no 64-bit value ever crosses a clock domain (the existing gray-code
CDC cannot carry a sec:ns value anyway, since it does not increment by 1).

The readout cores are unchanged: their `USE_EXT_TS=1` / `ts_ext` path is exactly the
right hook, and their stubbed `pps` inputs stay tied low (PPS handling lives entirely
in `wr_timebase`). The packed event word `{FLAGS, TS[63:0], EVENT, DATA}` keeps its
shape; only the meaning of TS changes.

### 3.2 Inputs

`wr_clk10` and `wr_pps` are treated as asynchronous data inputs (like the TCLK pin
today): 2-FF synchronized into each consuming domain, then rising-edge detected.
Ordinary LVCMOS33 pins are sufficient; no clock-capable pin, MMCM, or new clock
domain is needed. 10 MHz has edges every 50 ns, well within the Pmod level
translators' bandwidth and comfortably sampled at 40 MHz and up.

### 3.3 Timestamp construction (inside each `wr_timebase` instance)

- Cell counter: counts `wr_clk10` rising edges since the last PPS. Each edge snaps
  `ns` to `cells * 100`. Accumulated local-oscillator error can never exceed one
  100 ns cell.
- Interpolator: between 10 MHz edges, a fixed-point accumulator adds the local clock
  period (parameter; 25 ns in `clk_40m`, 6.4 ns in `rx_usrclk2`, 10 ns in the
  100 MHz `s_axi_aclk` monitor) so `ns` advances in roughly one-clock steps instead
  of 100 ns jumps. The accumulator clears at every 10 MHz edge, so interpolation
  error never accumulates.
- PPS edge: `ns` clears to 0; `sec` loads the armed value (first PPS after arming)
  or increments by 1 (subsequent PPSes).
- Output: `ts[63:0] = {sec, ns}` plus a `locked` status output.

Clock-period parameterization must handle non-integer periods (6.4 ns) exactly, for
example by accumulating in 0.2 ns units; the exact fixed-point scheme is an
implementation-plan detail. The requirement is: no systematic truncation drift
within a cell, and exact re-snap at each edge.

### 3.4 Strict validity

`ts` is forced to 64'd0 unless the instance is fully locked. Locked requires all of:

1. A seconds value was armed by the PS and has been loaded at a PPS edge.
2. The 10 MHz activity watchdog is alive (an edge seen within the last few cells,
   timeout on the order of 400 ns).
3. The PPS watchdog is alive (a PPS edge within the last ~1.1 s).

Any violation drops the instance to unlocked: `ts` reads 0, the sticky `lost_lock`
status bit sets, and re-locking requires the PS to re-arm (this guarantees seconds
are re-verified against NTP after any gap). A zero timestamp is therefore
unambiguous: the event was stamped while not WR-synced. Watchdog thresholds are
parameters so simulation can shorten them.

### 3.5 Accuracy budget

- ns is WR-true to within one 100 ns cell bound (snap) plus one local clock of
  sampling quantization (25 ns at 40 MHz, 6.4 ns at 156.25 MHz).
- The TCLK and ACLK copies agree with each other to within about one local clock
  cycle, since both are slaved to the same physical edges every 100 ns.
- Absolute seconds are as correct as the arm-time NTP reading, and are verified by
  readback (section 5).

## 4. Seconds arming protocol (NTP -> PL)

The PS system clock is assumed NTP-disciplined (chrony/timesyncd); we only consume
it.

1. Helper reads the system time `t`. If the fractional second is outside a safe
   window (roughly 0.1 s to 0.9 s), it waits, to avoid racing the PPS boundary.
2. Helper writes `floor(t) + 1` (the Unix UTC label of the NEXT PPS) to `SEC_ARM`.
   The write arms the hardware.
3. At the next PPS rising edge, every `wr_timebase` instance loads the armed value,
   sets `ns = 0`, and goes locked. The armed value is single-use; seconds then
   self-increment at each PPS.
4. Helper reads back `SEC_NOW` and compares with the system clock to verify, and
   re-arms if they ever disagree (e.g., after a lost-lock event).

The armed value crosses into each event domain as a quasi-static value with a
toggle-handshake (the repo's standard pattern for multi-bit quasi-static CDC).

## 5. New AXI-Lite slave: `wr_timebase_axi` (bus S_AXI3)

Both existing register maps (TCLK readout on S_AXI, ACLK readout on S_AXI2) are
full: all sixteen 16-byte-aligned slots 0x00-0xF0 are assigned. The timebase is a
self-contained unit, so it gets its own small slave and its own UIO device. The
16-byte register stride convention is kept (the 16-byte AXI aliasing lesson).

| Offset | Name | Access | Contents |
|---|---|---|---|
| 0x00 | STATUS | RO | live: locked_tclk, locked_aclk, locked_mon, pps_activity, clk10_activity, arm_pending; sticky: lost_lock |
| 0x10 | SEC_ARM | RW | Unix seconds label for the next PPS; writing arms |
| 0x20 | SEC_NOW | RO | monitor instance's current seconds; reading atomically latches NS_NOW |
| 0x30 | NS_NOW | RO | ns latched at the SEC_NOW read |
| 0x40 | PPS_COUNT | RO | PPS edges seen since reset |
| 0x50 | CELLS_LAST | RO | 10 MHz cells in the last PPS interval (expect 10,000,000) |
| 0x60 | CTRL | WO | clear sticky bits / force disarm |

`locked_tclk` / `locked_aclk` are single-bit 2-FF syncs of each event-domain
replica's `locked` output. CELLS_LAST is the primary signal-quality diagnostic: a
value far from 10,000,000 means a flaky 10 MHz or PPS line.

## 6. Integration changes

- `rtl/wr_timebase.v` (or `.sv`): the per-domain timebase core described above.
- `rtl/wr_timebase_axi.sv`: the S_AXI3 register slave + monitor instance + arm CDC.
- `aclk_pipeline_bd_top`: two new input ports (`wr_clk10`, `wr_pps`), the S_AXI3
  port set with the X_INTERFACE attributes matching the existing two buses,
  `global_timebase` instance replaced by the `wr_timebase` instances. The
  `global_timebase` module itself stays in the repo (other tops still use the tick
  scheme).
- `constraints/kr260_aclk_pipeline.xdc`: `E10` = `wr_clk10`, `E12` = `wr_pps`,
  LVCMOS33, async inputs (no input-delay constraints; add false-path/max-delay
  exceptions consistent with how `tclk` is handled).
- `vivado/build_aclk_pipeline.tcl`: third AXI-Lite master port on the interconnect,
  address assignment for S_AXI3, wrapper wiring for the two new pins.

## 7. PS software (deploy/)

- New `deploy/wr_time.py`:
  - `status`: decode STATUS / PPS_COUNT / CELLS_LAST / SEC_NOW+NS_NOW, print lock
    state and the hardware-vs-system-clock delta.
  - `arm`: the protocol of section 4 (mid-second guard, write SEC_ARM, verify by
    readback, report).
- Reader scripts for the pipeline (`deploy/tclk_read.py`, `deploy/aclkgt_read.py`
  paths used by the pipeline runbook): decode TS as `{sec, ns}`, print
  human-readable UTC alongside raw values, print zero timestamps as `UNSYNC`.

## 8. Testing (cocotb, with completion plots per repo convention)

- New `tb/wr_timebase/` unit suite: modeled PPS + 10 MHz stimulus; checks
  arm-and-load at PPS, ns snapping at every cell, interpolation monotonicity and
  bounds, strict behavior (ts == 0 before arm; stopping 10 MHz collapses ts to 0
  and sets the sticky; re-arm recovers), seconds increment across several PPSes,
  and both integer (25 ns) and fractional (6.4 ns) period parameterizations.
  Cells-per-second and watchdog thresholds are parameters so most tests run a
  "short second", with a couple of full-length spot checks.
- New `tb/wr_timebase_axi/` suite: arm write path, atomic SEC_NOW/NS_NOW latch,
  sticky set/clear, CELLS_LAST.
- Updated `tb/aclk_pipeline_chain/` integration suite: WR stimulus added; assert
  TCLK and ACLK events on the shared timeline agree within tolerance, and events
  stamped while unsynced carry ts == 0.

## 9. Out of scope

- Other bitstream tops (standalone TCLK, unified clk decoder, aclkgt selftest)
  keep the tick timebase for now.
- Leap-second handling beyond whatever NTP/the re-arm verify loop provides.
- Sub-cell (better than ~10 ns) phase calibration such as MMCM locking to the WR
  10 MHz; revisit only if the interpolated scheme proves insufficient on hardware.
- Holdover/flywheel operation when WR disappears (explicitly rejected in favor of
  strict zeroing).
