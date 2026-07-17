# Kickoff prompt for the plot GUI repo

Paste everything below the line into Claude Code in the NEW repo. It assumes
the new repo lives on this same PC (the prompt points at absolute paths in
this repo).

---

Build a desktop GUI for exploring Fermilab event-capture CSVs. The design
was already brainstormed and approved; do NOT re-open the design questions.
The approved spec is here, read it first and follow it:

    C:\Users\jacob\Fermilab\Summer-2026\kria-2-hardware\docs\superpowers\specs\2026-07-17-plot-gui-design.md

Context you need:

- A Kria KR260 board captures TCLK/ACLK timing events and archives them to
  CSVs named `events-<src>-YYYYMMDD.csv` with columns `id, sec, ns, event,
  data`. `id` is a Redis stream id (dedup key), `sec`/`ns` are a White
  Rabbit timestamp, `event` is the event code (displayed as $XX hex).
  Sources are `tclk` and `aclk`. Files can hold millions of rows.
- The proven analysis math lives in this file. Read it and PORT the
  functions `load_events`, `cycles_from_anchors`, `assign_offsets`, and the
  last-N-cycles limiting behavior into the new project (adapt, do not
  reimplement from scratch, and do not import across repos):

    C:\Users\jacob\Fermilab\Summer-2026\kria-2-hardware\deploy\supercycle_plot.py

What to build (details in the spec):

- PySide6 + pyqtgraph desktop app, deps: PySide6, pyqtgraph, numpy, pandas
  (CSV parsing), pytest.
- Layout: `app.py` main window (Open CSVs... multi-select picker, global
  source dropdown tclk/aclk, status bar with counts and time span) over a
  `core/` package (data_store.py, folding.py, codes.py, no Qt imports) and
  a `tabs/` package with a TAB_REGISTRY so future graph tabs are one module
  plus one registry line.
- Tab 1 "Folded histogram": plot on top (~75% of the tab), params below:
  fold-event dropdown pre-filled with codes present in the data (shown as
  `$00 (count)`), a comma-separated-hex targets box where each code is
  overlaid as a step/outline histogram in its own color with a legend, a
  last-N-fold-windows spinbox (0 = all), a bins spinbox (default 600), and
  an explicit Apply button. Stats line: windows folded, median window
  length, events per target.
- Tab 2 "Raster": same geometry. X = time since the most recent fold event;
  Y = elapsed time since the first anchor in hours, oldest at top (true
  wall-clock position, so capture gaps show as blank bands). Params:
  fold-event dropdown, a checklist of codes to populate (dots, one color
  per code), the same last-N-windows control, and a max-points cap
  (default 500k) that subsamples uniformly and visibly reports "showing X
  of Y". Use ScatterPlotItem with pxMode=True and no pen, one item per
  checked code.
- Fold semantics: the anchor is any event code; consecutive anchor events
  define windows; windows whose length deviates from the median by more
  than tol (default 1%) are rejected (a missed anchor would fold two
  windows together). Surface kept/rejected counts in the UI.
- Errors: invalid hex turns the field red with a hint; a fold code with no
  events (or < 2 anchors) shows a message listing the codes that ARE
  present with their counts.
- Tests: pytest for core/ (folding, code parsing, store dedup/merge), plus
  a synthetic CSV generator (known anchor period, known target offsets,
  some jitter, a deliberate missed anchor) used by the tests and for
  running the GUI without real capture data.

Style constraints: never use em dashes anywhere (code, comments, docs, UI
text). Event codes render as $XX uppercase hex.

Verify before calling it done: generate a synthetic CSV, launch the app,
load it, and confirm on tab 1 that the known target offsets appear as peaks
in the right places, and on tab 2 that the raster columns sit at those
offsets with the missed-anchor window rejected. Then run the pytest suite.

Real data for a manual check (optional, if present on this PC): any
`events-tclk-*.csv` copied from the board; fold on $00, targets 1E,0C
should reproduce the shapes that
`deploy/supercycle_plot.py --target 1E --ref 0C,BA` produces in the old
repo.
