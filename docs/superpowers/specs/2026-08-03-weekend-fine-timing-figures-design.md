# Weekend-Run Fine-Timing Presentation Figures - Design

**Date:** 2026-08-03
**Status:** Approved
**Data:** `../weekend-20260731/` (9 x `flags-*.csv`, `counters.json`, `weekend-flags.log`, `wr-guard.log`)

## Purpose

The PIP-II poster is already printed. Since then the multiphase fine-timing TDC
was validated on the board and a 52 h weekend capture was taken. This work
produces a figure set for on-screen presenting (iPad) that explains the
four-phase sampling technique and reports what the weekend run measured.

These figures are additive: they do not replace anything in `poster/figures/`.

## The dataset

Parsed from the nine `flags-*.csv` files (columns `id,sec,ns,event,fine_phase,fine_valid`):

- **52.46 h continuous**, 2026-07-31 21:11:51 to 2026-08-03 01:39 UTC.
- **18,562,135 rows** kept after the two defect filters below. `counters.json` deltas over the same interval:
  `event_count` +18,562,421, `filtered_count` +135,959,068, so roughly 136 M
  further events were seen and discarded by the in-FPGA drop mask.
- `error_count` = 0 and `null_count` = 0 at both counter samples.
- `fine_valid` = 1 for every row.
- 41 distinct event codes; 3145 supercycles anchored on `$00`.
- `ns % 5 == 0` for every row, confirming the coarse stamp sits on the exact
  5 ns (200 MHz) grid and the two fine bits carry the only sub-tick information.

### Two data defects, handled explicitly

1. **Stale FIFO prefix.** The first 512 rows (exactly the readout FIFO depth;
   the earlier 737 k run has 512 too) carry timestamps from
   2026-07-31 16:35 UTC, 4.6 h before the run began: leftovers in the readout
   FIFO from a prior session. The loader drops every row before the first
   large forward time gap.
2. **Byte-corruption burst.** `flags-20260802-151159.csv` contains a localized
   run of corrupted bytes (~586 non-ASCII bytes in one region, consistent with a
   storage write fault rather than a decode fault). The loader validates each row
   numerically and in-range; 135 rows of 18.5 M are dropped. The count is
   reported, never silently swallowed.

### Precision trap

Interval statistics must be computed on time relative to the run start.
`float64` at absolute UNIX seconds (~1.785e9) has ~400 ns of resolution, which
swamps every effect in these figures. The loader returns `t_rel`, never an
absolute float second.

## Figures

House style throughout: the existing blue-and-white poster palette
(`C_TCLK #1b5a8f`, `C_ACLK #2b8cc4`, `C_WR #0f9c85`, `INK #1b1b1b`,
`MUTED #6f6f6f`), DejaVu Sans, `svg.fonttype: none`, PNG at 300 DPI plus SVG.
Type is sized for a tablet held at arm's length, not for a 36x48 print.

### A - "Four clocks, one edge" (technique explainer)

The only synthetic figure; drawn from the RTL, not from data.

- A TCLK line edge arriving at an arbitrary time inside one 5 ns period.
- The four 200 MHz clocks stacked at 0/90/180/270 degrees with a sampling arrow
  at each rising edge.
- The resulting five-sample window rendered as thermometer cells, ordered
  earliest to latest: previous period's phase-270 sample, then this period's
  0/90/180/270.
- The decoded 2-bit bin shown against a 1.25 ns ruler.
- A case strip below: the four arrival positions producing `10000`, `11000`,
  `11100`, `11110`, and a non-monotone glitch pattern producing `fine_valid = 0`.
- A callout for the fifth sample: without the previous period's phase-270 tap the
  boundary quarter aliases away and only three of the four bins are resolvable.

Source of truth: `rtl/aclk_lite/tclk_fine_tdc.sv`,
`rtl/aclk_lite/tclk_fine_decode.sv`, and the 0/90/180/270 MMCM request in
`vivado/build_aclk_pipeline.tcl` lines 164-175.

### B - Measured TDC transfer function

Code-density calibration per `deploy/fine_calibrate.py`: an asynchronous edge
lands in each bin with probability equal to that bin's fractional width, so the
`fine_phase` histogram over 18.5 M events recovers the bin widths.

- Upper panel: measured cumulative bin edges as a staircase against the ideal
  1.25 ns ramp.
- Lower panel: differential nonlinearity per bin as a stem/step residual, not
  a bar chart.
- Measured widths 1.209 / 1.198 / 1.271 / 1.322 ns, annotated against the
  poster's 736 k-event calibration (1.07 / 1.24 / 1.34 / 1.35 ns) to show the
  longer run is the better-conditioned calibration.

### C - Coarse versus refined residual density

`$8F` (1 Hz, GPS-locked, 188,850 events). Interval residual about the median
period, histogrammed at 0.25 ns.

- Coarse residuals form a comb of spikes on the 5 ns grid.
- Refined residuals (`fine_calibrate.refine`, `+` convention, no boundary wrap)
  fill the comb into a continuum.

Filled density plus outline. This shows the fine bits carry information; it does
not claim they reduce the interval RMS, because they do not (see D).

### D - The jitter ladder

Log-log scatter, one point per event code with at least 200 occurrences:
nominal period on x, robust interval-jitter RMS on y (residuals about the median
period, 90th-percentile core cut). Shaded horizontal bands mark the 5 ns coarse
quantization floor and the 1.25 ns fine bin, three to four decades below every
measured point.

The measured ladder: `$8F` 38 ns, `$02` 60 ns, the 15/20 Hz mains-locked family
13-19 us, the 1 s supercycle-locked family 258 us, 3 s 773 us, 10 s 2.6 ms, and
the 60 s supercycle codes 14 ms. GPS-locked and supercycle-locked codes separate
visually, and the machine-locked group shares a common **+121.5 ppm** rate offset.

That offset is referenced to `$8F`, the GPS 1 Hz marker, and NOT to the nominal
period. The board's own WR timebase free-runs at -3.49 ppm against GPS, so
referencing to nominal folds that oscillator's error into the answer and reports
+118 ppm instead. `$8F` is the only absolute reference in the chain.

Message: the receiver's timing floor is no longer the limiting term anywhere in
the system, and the fine bits are what put it there.

### E - Weekend supercycle heatmap

`poster/make_supercycle_heatmap.py` styling, recomputed on the weekend capture:
top-N event codes as rows, phase within the ~60.007 s supercycle on x,
per-code-normalized density as color, over 3145 supercycles.

### F - Schedule-stability raster

Phase within the supercycle on x, wall-clock hour across the 52 h on y, density
as color, for the deterministic codes. `$7A` occupies 18 of 600 phase bins,
`$EF` 25, `$B3` 54, so these draw straight vertical lines across two days. The
figure shows both the accelerator's determinism and the receiver's stability
over the full run.

### Rejected

- **Fine-phase walk heatmap.** Measured: the `fine_phase` distribution is
  uniform at 10-minute scale (peak bin 27-30 %) and only weakly concentrated at
  10 s. `$8F`'s own 38 ns delivery jitter scrambles the sampling phase across all
  four bins. The figure would be a flat noise field.
- **Run-continuity river.** Measured: all 41 codes present for the whole run,
  per-10-minute rate coefficient of variation 0.014. A set of flat lines. The
  result is retained as a stat strip on the presentation page instead.

## Deliverables

- `poster/weekend_data.py` - loader, `.npz` cache, stale-prefix and corrupt-row
  handling, relative-time conversion, calibration helpers.
- `poster/make_fine_tdc_figure.py` - figure A.
- `poster/make_fine_linearity_figure.py` - figures B and C.
- `poster/make_jitter_ladder_figure.py` - figure D.
- `poster/make_weekend_supercycle.py` - figures E and F.
- `poster/figures/` - PNG (300 DPI) and SVG for each.
- A published Artifact page holding all six figures, the run stat strip, and
  presenting notes.

## Testing

The generators are analysis scripts, not library code. Verification is:

- `poster/weekend_data.py` gets unit tests for the two defect paths
  (stale-prefix drop, corrupt-row rejection) and for relative-time precision.
- Every figure is rendered and visually inspected before it ships.
- Numbers quoted in captions are read from the data at render time, never
  hard-coded.
