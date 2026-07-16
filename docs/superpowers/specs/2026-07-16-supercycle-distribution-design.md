# Supercycle event-distribution analysis: stream archiver + folded-raster plot

**Date:** 2026-07-16
**Status:** approved approach (approach 1 of 3); spec for implementation

## Problem

TCLK plays a timeline per supercycle (anchored by event `$00`). The slot an event
occupies can move between cycles (timeline branching / machine state), so the offset of
a given code from its cycle's `$00`, folded across many cycles, has a distribution:
narrow spikes for fixed slots, multimodal clusters when slots reorder, genuine spread
for beam-conditioned events, and combs for the periodic codes (15/20 Hz). We want to
choose a target code and see that distribution against the periodic combs as reference,
both for analysis now (pre-WR-stand) and from the upcoming multi-day stand run.

Constraints discovered during brainstorming:
- The Redis stream retains only ~1M entries (~2.8 h at 99 ev/s). Raw per-event rows are
  otherwise lost, so a persistent archive is required for multi-day analysis. The final
  ~2.8 h of the 2026-07-15/16 overnight run is currently retained and frozen; it is the
  quick-look dataset.
- The archiver must not affect publisher throughput: it must be a separate process that
  only talks to redis-server (never /dev/uio*), so worst-case backpressure lands in the
  publisher sink's bounded queue, not the hardware FIFO drain.

## Components

### 1. deploy/stream_archive.py (board side)

Follows the published Redis streams and appends rows to daily CSV files.

- CLI: `python3 stream_archive.py [--src tclk aclk] [--namespace KR260]
  [--redis-host 127.0.0.1] [--redis-port 6379] [--poll 5] [--outdir .]
  [--once -o FILE]`
- Follow mode (default): every `--poll` seconds, per source, `XRANGE KR260:<src>
  (last_id +` in COUNT-10000 batches until drained; append rows to
  `events-<src>-YYYYMMDD.csv` (UTC wall date; new file gets a header); persist
  `last_id` per source to `archive-state.json` after each batch.
- Row schema: `id,sec,ns,event,data` (values exactly as published; `event` is the
  decimal string from the stream).
- `--once -o FILE`: ignore state, dump the full retained stream to FILE, exit.
  Requires exactly one `--src` (error otherwise). This is the quick-look path for data
  already in Redis.
- Restart behavior: resume from `archive-state.json`; if the state file is missing,
  start from the beginning of retention (duplicates possible only in that case; the
  plotter dedupes by stream id, so this is safe).
- Failure isolation: any Redis error logs one line, sleeps, retries; never exits on
  error in follow mode. Clean Ctrl-C exits 0 (fits the launcher restart-loop contract:
  nonzero exit = crash = restart).
- Cost: ~260 MB/day/source plain CSV at 99 ev/s (~2.1 GB/day at the full 819 ev/s
  line); acceptable for multi-day runs on the SD card. No auto-deletion; rotation is
  daily files, cleanup is manual.

### 2. run_pipeline.sh hook

A fourth tmux window `archive`, gated by `ARCHIVE="${ARCHIVE-1}"` (set ARCHIVE="" to
disable), running the archiver under `nice` in the same `until`-restart wrapper as the
publishers.

### 3. deploy/supercycle_plot.py (PC side)

Renders the folded raster + marginal-shape figure for one target code.

- CLI: `python supercycle_plot.py events-tclk-*.csv --target 1E [--ref 0C,BA]
  [--anchor 00] [--tol 0.01] [--topn-report 5] [-o supercycle_1E.png]
  [--theme default|poster]`
- Pipeline (pure helpers, unit-testable):
  1. Load CSV(s), dedupe by stream id, t = sec + ns*1e-9, sort by t.
  2. Anchors = times of `--anchor` events. Cycles = consecutive-anchor windows.
  3. Cycle-length filter: median length L; reject cycles with |len - L| > tol*L
     (a missed anchor folds two cycles together and would corrupt offsets). Report
     kept/rejected counts and L to stdout.
  4. Assign every event its cycle index and offset (seconds from that cycle's anchor)
     via searchsorted.
- Figure (matplotlib, gridspec 2 rows sharing x):
  - Top (~25%): marginal histogram of target offsets (the "shape"), reference-code
    histogram faint behind it. Log-y toggleable.
  - Bottom: raster: y = cycle index (time order), x = offset; reference events as faint
    gray dots (the comb and its per-tooth jitter are real data, not a drawn grid);
    target events as colored dots on top.
  - Header states target, refs, n cycles, n target events, median cycle length.
  - `--theme poster` applies the blue-and-white poster styling.
- Robustness: missing target/anchor code exits with the list of available codes and
  counts; fewer than 5 usable cycles exits with a clear message.
- Stdout report: cycle length mean/sigma, target occurrences per cycle (min/median/max),
  top-N mode positions of the target distribution.

## Data flow

publisher -> Redis stream (retention ~1M) -> stream_archive.py -> daily CSVs on SD
-> scp to PC -> supercycle_plot.py -> PNG/SVG (+ stdout stats).

Quick look now: `stream_archive.py --once -o overnight-tail-tclk.csv` on the board
(the retained ~2.8 h of the overnight run), scp, plot.

## Testing

- test_supercycle_plot.py: synthetic events (known cycle length, one comb ref, a
  bimodal target, one deliberately missed anchor) exercising segmentation, outlier
  rejection, offset assignment, and dedupe. Pure functions, no matplotlib needed.
- test_stream_archive.py: batching/resume logic against a stub Redis client (same
  pattern as test_redis_sink's FakeRedis): follow-mode batches, state persistence,
  --once dump, duplicate-safety on missing state.
- Hardware validation: run the archiver alongside the publishers for ~10 min and
  confirm stats_report still shows missed@pub 0 and missed@HW ~0 (throughput
  unaffected), and that the CSV row count matches the stream growth.

## Out of scope (explicit)

- No change to the publisher, sink, RTL, or stream schema.
- No Redis retention change (MAXLEN stays ~1M; the archive is the long-term record).
- Phase-folding of comb codes (offset mod period) is a possible later addition to the
  plotter; v1 shows combs as-is.
