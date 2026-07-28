# TCLK Sub-Sample Timestamp Resolution via a Multiphase Edge-TDC - Design

**Date:** 2026-07-28
**Status:** Design approved, pending spec review

## Context and motivation

The shipped TCLK build (`26bef93`) decouples an 80 MHz biphase decoder from a
200 MHz timestamp clock, giving 5 ns stamp granularity but **~12.5 ns edge
localization** - the localization floor is set by the 80 MHz decode sampling
period, not the stamp clock. An earlier attempt to push the raw line sampler to
400 MHz for finer localization failed on the real 3.3 V TCLK line: the faster
sampler resolved real-line ringing as spurious edges and corrupted the biphase
decode (~60 % PERR; a 15 ns glitch-reject debounce did not help). See
`rtl/aclk_bridge/serdec4_9MHz.v` comments and the
`2026-07-17-tclk-clock-upgrade-aclk-hardening-design.md` risk section.

Separately, analysis of the day-long captures showed the *current* stamp is
taken at **byte-completion** (`aclk_valid = ~DAVn`, latched into the packed word
at the VALID cycle - `rtl/aclk_readout/aclk_readout_core.sv`). DAVn fires ~8
bit-cells (~800 ns) after the event begins on the wire, and its firing dithers
against the stamp clock; that dither is the likely source of the ~250 ns
discreteness seen in the (older, 40 MHz) captures.

Goal: get **below** the 12.5 ns floor without destabilizing the proven decode,
and while the board is inaccessible (servers down) so the work is sim-validated
only.

## Goals

- Timestamp TCLK events with **~1.25 ns edge localization** (~10x under the
  12.5 ns floor), by measuring the sub-sample arrival phase of a defined
  reference edge.
- Keep the existing receiver **bit-for-bit identical** - decode reliability is
  untouched.
- **Strictly additive / graceful fallback:** if the fine-timing bits are noise
  on the real line, the event and its 5 ns coarse stamp are exactly today's
  build. The feature can never make the capture worse.
- Fully sim-validated deliverable: a bitstream ready to flash, with the RTL
  proven in cocotb against the known ringing model.

## Non-goals

- Sub-nanosecond (IDELAY tapped-delay-line) resolution. Likely infeasible on the
  HD I/O bank the 3.3 V pin sits on, and below the physical meaningfulness of
  these events (mains events are us-jittery; even sharp $02/$8F are limited by
  the TCLK encoding). Explicitly out of scope.
- Changing the decoder, the biphase FSM, or the clock architecture of the
  shipped build.
- Board bring-up / real-line accuracy verification (deferred until servers
  return; see below).
- ACLK path changes. This design is TCLK-only (the 3.3 V biphase line).

## Constraints

1. **No board access.** Everything is sim-validated. The exact failure mode that
   limited the last attempt (real 3.3 V line ringing) cannot be reproduced
   faithfully in sim - so the design must degrade gracefully and the real-line
   fine accuracy is an accepted open item.
2. **HD I/O bank.** The 3.3 V TCLK pin is on a High-Density bank, which on Zynq
   US+ lacks the ISERDES/IDELAY primitives a classic TDC uses. The portable
   technique is **multiphase sampling with phase-shifted fabric clocks**, which
   works on any pin.
3. **Timing closure.** Every flip-flop still runs at <=200 MHz; the effective
   800 MHz is a phase relationship, not a real clock, so closure matches the
   shipped 200/400 envelope.

## Architecture

Three blocks. The first is the existing receiver, unchanged except for one new
output strobe; the other two are new.

```
                     +-------------------------------+
   TCLK pin --+-----▶| serdec + deserializer (80 MHz)|--▶ event byte + DAVn  (UNCHANGED)
              |      |  + new "reference-edge" pulse |--▶ ref_edge strobe
              |      +-------------------------------+
              |
              +-----▶+-------------------------------+
                     | multiphase edge-TDC (NEW)     |--▶ fine_phase[1:0], fine_valid
                     |  4 phase-shifted 200 MHz clks |
                     +-------------------------------+
                                     |
                     +---------------▼---------------+
                     | merge (NEW): on ref_edge/DAVn |--▶ packed word + fine bits in FLAGS
                     |  latch coarse TS + fine bits  |
                     +-------------------------------+
```

### Block 1 - Existing receiver + reference-edge strobe (minimal change)

`TCLK_RCV` (serdec4_9MHz + TCLK_DESERIALIZER2) is kept bit-identical. The only
addition: the deserializer raises a **one-cycle `ref_edge` strobe** at
**frame detection** (the carrier edge tied to `DAVn`). See *Reference edge
definition* for why this frame-completion edge is equivalent to a start-of-byte
edge for our purpose. No decode logic changes; `ref_edge` is a tap on the
existing detection condition.

### Block 2 - Multiphase edge-TDC (new)

An MMCM generates four 200 MHz clocks at 0/90/180/270 degrees. Four flip-flops
sample the raw line, one per phase; each tap passes through a 2-FF synchronizer
(the line is asynchronous) into the 0-degree domain, all with **equal pipeline
latency** so the four samples are temporally coherent (one period).

Four samples alone give only 3 interior crossing positions per 200 MHz period;
the 4th quarter `[phase3, next-period phase0)` aliases across the period boundary
(all-old this period, all-new the next) and would be lost. To recover it, the
decoder uses a **5-sample window**: the four current-period samples plus the
previous period's last-phase sample (`s270` delayed one `clk_p0` cycle). Ordered
earliest to latest these five samples span exactly one period across the
boundary, giving **4 interior positions = 4 clean 1.25 ns bins** ->
`fine_phase[1:0]` (bin 0-3), with no wraparound aliasing.

- A clean single crossing yields a monotone thermometer code -> `fine_valid=1`,
  `fine_phase` = leading-run length - 1 (0..3).
- A glitch / double edge yields a non-monotone code -> `fine_valid=0`.

The decode handles either edge polarity (biphase-mark alternates), reporting the
crossing sub-bin for a rising or falling reference edge alike.

MMCM output budget: the shipped build uses ~2 MMCM outputs (80 MHz serdec,
200 MHz timestamp). Four phase-shifted 200 MHz outputs fit within the MMCM's 7
CLKOUT outputs (one of the four can reuse the existing 200 MHz at 0 degrees, so
+3 new). Exact allocation is pinned in the implementation plan; if the budget is
tight, fall back to 2 phases / DDR (2.5 ns bins, still 5x).

### Block 3 - Merge / tagging (new)

At the `ref_edge` strobe (frame detection), latch **both** the coarse 200 MHz
timestamp **and** `fine_phase`/`fine_valid` for the event, and hold them until
`DAVn` for packing. Capturing the coarse stamp from this carrier-edge-derived
strobe, rather than from the `DAVn` output that is resynced from the recovered
SCLK onto `clk_40m`, is what removes the resync-beat dither seen in the captures
("increment C"); the fine phase then refines the same reference-edge time to
~1.25 ns ("increment A"). Because the fine bits and the coarse stamp travel
inside the FIFO word with their event, they cross the clock-domain boundary
together and need no separate CDC (the property the coarse timestamp already
relies on).

Localization chain: DAVn-resynced stamp (dither observed ~250 ns, to be
confirmed by the characterization task) -> ref-edge coarse stamp (increment C)
-> + fine phase (~1.25 ns, increment A).

## Reference edge definition

The reference edge is the **frame-completion carrier edge** - the transition tied
to the deserializer's frame detection (`DAVn`). TCLK's carrier toggles
continuously (biphase-mark) and the decode FSM only knows a frame exists at
completion (`TCLK_DESERIALIZER2.v`, detection on `data_reg[10:8]==110`), so a true
start-of-byte edge is not available without restructuring the proven FSM - which
is out of scope.

This does not matter for the goal. A TCLK frame is fixed length (start + 8 data +
parity = 10 cells), so `DAVn` fires a **constant** number of bit-cells after the
start edge. A constant offset **cancels in event-to-event spacing** - the only
thing measured ($02 period, jitter, drift). Tagging the same frame-completion
edge on every frame is therefore equivalent to tagging the start edge, and is
cleanly available as a tap on the existing detection condition.

## Build order (characterization, then two increments)

0. **Characterization** - in the existing `tclk_readout` testbench, reproduce and
   quantify the byte-completion timestamp jitter and confirm its source (expected:
   the recovered-SCLK-to-`clk_40m` resync beat, not byte-assembly latency). This
   validates the increment-C premise on measured ground before we build on it.
1. **Increment C** - add the `ref_edge` strobe (frame-detection tap) and capture
   the coarse timestamp at `ref_edge` (held until `DAVn`), off the fast clock
   rather than the resynced `DAVn`. Removes the resync-beat dither. Self-contained;
   the decode-preservation regression must pass here.
2. **Increment A** - add the multiphase edge-TDC and the `fine_phase`/
   `fine_valid` bits, refining the reference-edge time to ~1.25 ns.

Each increment is additive and independently sim-provable; A builds on C.

## Data path and packed word

The packed word is unchanged in width. `FLAGS[15:0]` currently uses only bit 0
(`has_data`) and bit 1 (`is_tclk`). Add:

- `FLAGS[3:2]` = `fine_phase` (sub-bin 0-3)
- `FLAGS[4]`   = `fine_valid`

PL emits the **raw** sub-bin, never a PL-computed corrected time.

## Software calibration

The four phase bins are not exactly 1.25 ns each (MMCM phase error, routing skew,
PVT), so the bin->time mapping is calibrated **in software**, not baked into the
bitstream:

- Use the code-density of a known-periodic marker to recover the effective bin
  edges. The `$02` (5 s) and `$8F` (1 Hz GPS) streams are ideal references and
  the existing analysis tools (`deploy/marker_timing.py`,
  `deploy/tclk_faithfulness.py`) already isolate them.
- Apply `refined_ts = coarse_ts - offset[fine_phase]` for events with
  `fine_valid = 1`; ignore fine bits otherwise.

Keeping calibration in software is what makes this a safe bolt-on: analysis can
drop the fine bits entirely and recover exactly today's behavior, and the
correction is re-calibratable without a rebuild.

## Graceful fallback

- Fine bits are consumed **only** at the reference edge, so ringing edges
  elsewhere never enter an event.
- Ringing *inside* the reference window is caught by the non-monotone thermometer
  code -> `fine_valid = 0`, and software drops the fine bits for that event.
- The event and the 5 ns coarse stamp are always produced by the untouched
  decode path. Worst case (fine bits useless on the real line) = the shipped
  build.

## Validation (sim-only)

Cocotb, extending the existing TCLK suites:

1. **Fine-timing sweep** - generate biphase TCLK with a known sub-sample edge
   offset stepped across a 12.5 ns window; assert `fine_phase` advances correctly
   through its bins and `fine_valid = 1` for clean edges.
2. **Decode-preservation regression** - the existing OSR=8 decode suite stays
   bit-identical: `EVENT_COUNT` exact, timestamps strictly increasing, PERR
   behavior unchanged. Proves the additions do not perturb decode.
3. **Ringing robustness** - inject the width-3 ringing model already in
   `tb/tclk_rcv` at the reference edge; assert decode is still correct **and**
   `fine_valid` drops rather than reporting a wrong bin.
4. **Calibration sanity** - a Python-side test that the code-density calibration
   recovers an injected offset from a periodic marker.

**Acceptance:** all decode regressions green + sweep correct + ringing triggers
fallback (not a silent wrong bin).

## Risks and mitigations

- **Real-line fine accuracy unverifiable in sim** (the 400 MHz-killer). Mitigated
  structurally: graceful fallback makes worst case = shipped build; documented as
  an open item for board bring-up.
- **MMCM output budget** for four phases. Mitigated: fits in 7 CLKOUTs; 2-phase
  DDR fallback (2.5 ns) if tight.
- **Metastability** sampling an async line across four phase domains. Mitigated:
  2-FF synchronizer per tap; thermometer decode after synchronization; the
  non-monotone-code check also flags marginal captures.
- **Timing closure** with the added sampler cloud. Mitigated: all FFs <=200 MHz;
  the fine logic is off the decode path, so it can be pipelined freely if WNS
  tightens.

## Board bring-up (deferred follow-up)

When servers return: flash the build, confirm decode + timebase lock unchanged
(ERROR_COUNT delta from baseline == 0), then capture and run the code-density
calibration on `$02`/`$8F` to (a) confirm `fine_valid` stays high on the live
line and (b) recover the bin calibration and the improved event-to-event jitter.
Exact check commands to be written with the implementation.

## Out of scope

- Sub-ns / IDELAY TDC; ACLK path; decoder or clock-architecture changes;
  board verification.
