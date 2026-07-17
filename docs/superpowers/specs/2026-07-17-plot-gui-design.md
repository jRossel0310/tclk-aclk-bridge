# Plot GUI design (new repo)

Date: 2026-07-17
Status: approved
Builds in: a NEW repo (not this one). This document is the validated design; the
kickoff prompt that carries it into the new repo is
`2026-07-17-plot-gui-kickoff-prompt.md` next to this file.

## Purpose

A desktop GUI for exploring the event CSVs the KR260 pipeline captures
(`events-<src>-YYYYMMDD.csv`, written by `deploy/stream_archive.py`). It
replaces one-shot CLI plotting (`deploy/supercycle_plot.py`) with an
interactive tool: load CSVs once, then turn knobs and re-plot instantly.
Two graph tabs to start, with an architecture that makes adding more tabs
cheap.

## Decisions log

- Stack: PySide6 + pyqtgraph (chosen for point-count performance; a browser
  stack would ship millions of points as JSON and fall over).
- Data in: multi-select file picker ("Open CSVs..."), no folder watching.
- Source handling: one source at a time via a global dropdown (tclk / aclk);
  no cross-source folding in v1.
- "Samples" control: last N fold windows (anchor-to-anchor), 0 = all.
- Tab 2 y-axis: elapsed time since the first anchor (true wall-clock
  position), NOT dense window index.
- Fold-engine code: ported from this repo's `deploy/supercycle_plot.py`
  (read and port, not reimplement from prose).
- Structure: core + tab plugins (approach B), not a one-file app, not a
  client/server split.

## Stack and repo layout

Python 3.11+. Dependencies: PySide6, pyqtgraph, numpy, pandas (CSV parsing
only; 5.5M-row files parse in seconds), pytest.

```
plot-gui/
  app.py              # main window: toolbar, source dropdown, status bar, tabs
  core/
    data_store.py     # loaded files, per-source arrays, per-code index
    folding.py        # anchors -> windows -> offsets (ported)
    codes.py          # "$1E"/"1E"/"0x1E" parsing, $XX formatting, palette
  tabs/
    __init__.py       # TAB_REGISTRY = [HistogramTab, RasterTab]
    histogram_tab.py
    raster_tab.py
  tests/              # engine tests, no Qt required
```

Main window: toolbar with "Open CSVs..." (multi-select), a source dropdown
populated from loaded data, a status bar with per-source event counts and
time span, then the tab bar.

## Data layer (core/data_store.py)

- Accepts any set of `events-*.csv` files (columns: id, sec, ns, event,
  data). Source is parsed from the filename `events-<src>-YYYYMMDD.csv`.
- Rows dedup by stream `id` across all loaded files, then stable time-sort
  (mirrors `load_events()` in supercycle_plot.py).
- Per source, keep `t` (float64 seconds, sec + ns*1e-9) and `event` (int64)
  arrays plus a `{code: sorted t}` index built once at load.
- All parameter changes and tab switches operate on the in-memory arrays;
  files are never re-read. Loading shows a progress dialog; loading more
  files merges into the store.

## Fold engine (core/folding.py)

Direct ports from `deploy/supercycle_plot.py`:

- `cycles_from_anchors(anchor_t, tol)`: consecutive-anchor windows, reject
  windows whose length deviates from the median by more than tol (default
  0.01). Guards against missed anchors folding two windows into one.
- `assign_offsets(t, starts, ends)`: searchsorted window assignment; returns
  (mask, window index, offset seconds).
- Last-N-windows limiter (the CLI's --cycles behavior).

Generalization: the anchor is ANY event code. Folding on $0C gives ~66 ms
windows, folding on $00 gives full supercycles; the same median rejection
applies either way. The engine reports kept/rejected counts and median
window length; both tabs display these so a bad anchor choice is visible.

## Tab 1: folded histogram

Plot fills the top ~75% of the tab; parameter panel underneath. Params:

- Fold event: editable dropdown pre-filled with codes present in the active
  source, rendered as `$00 (1234)` with occurrence counts.
- Targets: text box, comma-separated hex codes (e.g. `1E, 1F, BA`). Each
  code gets its own color from a fixed categorical palette and a legend
  entry. Drawn as outline/step histograms (not solid bars) so overlapping
  distributions stay readable.
- Samples: spinbox, last N fold windows (0 = all).
- Bins: spinbox, default 600.
- Apply button (explicit recompute, not per-keystroke).

Rendering cost is bins-bound, not event-bound: numpy computes each
histogram, pyqtgraph draws only `bins` points per target. Zoom/pan is
native. Stats line under the plot: windows folded, median window length,
event count per target.

## Tab 2: raster

Same geometry. X: time since the most recent fold event. Y: elapsed time
since the first anchor, in hours, oldest at top, so windows sit at their
true wall-clock position and capture gaps appear as blank bands. Params:

- Fold event: same dropdown as tab 1.
- Populate: checklist of codes present in the source (with counts); each
  checked code is drawn as dots in its own palette color.
- Samples: same last-N-windows control.
- Max points: cap (default 500k) with uniform subsampling above it and a
  visible "showing 500k of 3.2M" note. Decimation is never silent.

ScatterPlotItem in pixel mode (pxMode=True, no pen), one item per code.

## Extensibility contract

Each tab is a QWidget subclass with: a `title` attribute, a params panel it
builds itself, and an `update_plot()` slot. Tabs receive the shared
DataStore and a data-changed signal. `tabs/__init__.py` holds
`TAB_REGISTRY`; adding graph three is one new module plus one registry line.
The core package never imports Qt.

## Error handling

- Bad hex in a code field: field turns red with an inline hint.
- Fold code absent, or fewer than 2 anchors: message listing the codes that
  ARE present with counts (mirrors the CLI behavior).
- Fewer than 5 kept windows (the CLI's minimum): message suggesting a
  longer capture or a different anchor.

## Testing

- core/ gets pytest coverage ported alongside the functions (folding,
  code parsing, dedup/merge behavior of the store), plus a synthetic CSV
  generator so tests and GUI smoke-testing need no real capture data.
- No automated GUI tests in v1.

## Out of scope (v1)

- Cross-source folding (TCLK anchor with ACLK targets).
- Folder watching / live Redis tailing.
- Matplotlib-quality SVG export (pyqtgraph's built-in export dialog is
  available for free; poster-grade output stays in supercycle_plot.py).
