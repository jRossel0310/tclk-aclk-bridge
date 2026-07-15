# Unattended TCLK/ACLK Capture with Stats Reconciliation Design

Date: 2026-07-15
Status: approved (brainstorm complete)
Scope: board-side Python (`deploy/`) plus one PC-side plot script and a tmux launcher.
Additive changes to the existing publisher. No RTL, no bitstream change, no Redis
schema change.

## 1. Problem and goal

The KR260 pipeline bitstream is loaded and the WR timebase brings up cleanly. The event
publisher (`deploy/redis_publish.py` + `deploy/redis_sink.py`) already streams
WR-timestamped TCLK and ACLK events into local Redis under the `KR260:` namespace. What
is missing is the ability to **run the capture unattended for ~a day and then come back
and audit it**: how many events were published, how many were missed, how many failed
CRC, and so on.

The ground truth for "missed" and "failed CRC" lives in the PL readout registers, and
the publisher never reads them. The publisher only tracks its own software counters
(drained / published / queue_dropped / redis_dropped / reconnects) in process memory,
printed to stdout, with nothing durable. So today, after a day-long run, the
missed/CRC statistics cannot be reconstructed.

Goal: capture a durable time-series of every relevant counter during an unattended run,
and provide a report tool that reconciles hardware truth against what was published, so
"events published / events missed / failed CRCs" come out as hard numbers.

## 2. Decisions made during brainstorm

| Question | Decision |
|---|---|
| Stats granularity | Time-series snapshots AND a final summary |
| Run mechanism (survive SSH disconnect) | tmux/screen session (no systemd, no auto-restart) |
| Stats storage / durability | On-disk JSONL log only (survives reboot / redis flush) |
| Report output | Text reconciliation table on the board; plots run on the PC from the copied log |
| Who reads the hardware counters | The publisher self-samples (Approach A); one process owns each `/dev/uioN` |
| Report tool device access | None: the report is a pure JSONL reader, so it never races the live publisher |

## 3. Architecture (Approach A)

Only one process may own a given `/dev/uioN` (two would race on the `POP` write). The
publisher already holds the mmap and drains the FIFO, so it is the natural place to
sample the read-only counters. The chain:

```
PL readout regs ──(RO reads, no POP)──┐
FIFO ──drain──> publisher ────────────┼─> snapshot every N s ─> stats-<src>.jsonl (on disk)
                     │                                                    │
                     └─> Redis (KR260:*, unchanged) ...                   │
                                                                          ▼
                                        stats_report.py (board, pure log reader) -> text
                                        plot_stats.py   (PC, from copied log)   -> PNGs
```

The JSONL log is the single source of truth. The board report and the PC plots both read
it; nothing but the publisher touches the device.

## 4. Register semantics (authoritative, from `aclk_readout_axi.sv`)

Per readout register block (one per source), read-only unless noted. Reading any of
these has no side effect; only writing `POP` (0x60) advances the FIFO.

| Reg | Name | Meaning |
|---|---|---|
| 0x00 bit0 | STATUS.empty | FIFO empty |
| 0x00 bit1 | STATUS.overflow | **sticky**: an enqueued event was lost because the FIFO was full |
| 0x70 | EVENT_COUNT | good events **presented** to the FIFO (includes ones later lost to overflow) |
| 0x80 | NULL_COUNT | idle 0xFF packets dropped (ACLK only; 0 for TCLK) |
| 0x90 | ERROR_COUNT | bad-CRC / decode-error events (never enqueued) = **failed CRCs** |
| 0xE0 | FILTERED_COUNT | events suppressed by the drop-mask |
| 0xB0 | HEARTBEAT | free-running rx-clock counter (readback-liveness) |
| 0xC0 | LOCK.bit0 | MMCM locked |

Key property used by the reconciliation: `EVENT_COUNT` counts events **presented** to the
FIFO, incremented independently of `full`, so overflow-lost events are still counted in
`EVENT_COUNT` but never read out by the publisher. That makes overflow loss recoverable
as a number (section 6), even though the hardware exposes overflow only as a sticky bit,
not a counter.

## 5. Components and interface changes

### 5.1 `readout_common.py` (shared drain loop + snapshot helper)

- **Wall-clock tick in the drain loop.** `drain_events(io, on_event, idle_cb=None,
  poll_s=0.001)` gains an optional `tick_cb=None, tick_s=60.0`. `tick_cb()` is called at
  most once per `tick_s`, evaluated at the top of every loop iteration so it fires
  **whether the FIFO is busy or idle** (today's `idle_cb` only fires on an empty FIFO, so
  a sustained busy period would never snapshot). The existing `idle_cb` 1 Hz behavior is
  unchanged. Backward compatible: callers that pass no `tick_cb` are unaffected.
- **Snapshot helper.** A new pure function `read_hw_counters(io) -> dict` reads
  `STATUS` (for the overflow bit), `EVENT_COUNT`, `NULL_COUNT`, `ERROR_COUNT`,
  `FILTERED_COUNT`, `LOCK`, `HEARTBEAT` and returns
  `{event_count, null_count, error_count, filtered_count, overflow, lock, heartbeat}`.
  It reads registers only (no `POP`), so it is safe to call from the drain thread and is
  unit-testable against a `bytearray`-backed `RegIO`.

### 5.2 `redis_publish.py` (snapshot writer + closed accounting)

- **Count UNSYNC drops.** `on_event` currently ignores `ts==0` events entirely. Add an
  `unsync` counter incremented when `should_publish` is false, so the accounting closes
  (section 6). `drained` keeps its current meaning (non-UNSYNC events handed to the sink).
- **Snapshot writer.** A small `StatsLog` helper opens the JSONL file (append mode, line
  buffered) and exposes `snapshot(io, src, drained, unsync, sink_stats)` which builds one
  record (section 5.3) and writes it as a single JSON line. Called:
  - once at **startup** (baseline, before the drain loop, so hardware counters are
    anchored to publisher start),
  - every `--snapshot-interval` seconds via `tick_cb` during the run,
  - once at **shutdown** in the `finally` block, after `sink.stop()` flushes.
- **New CLI flags** (added to `value_flags`): `--statlog <path>` (default
  `./stats-<src>.jsonl`) and `--snapshot-interval <sec>` (default 60). All existing flags
  and Redis behavior are unchanged.
- Writing under `sudo` creates a root-owned log file; acceptable (documented).

### 5.3 Stats log format (JSONL, one line per snapshot per source)

Raw counters and timestamps only; rates are derived by the report, not stored. `mono` is
`time.monotonic()` (gap-free duration even if wall-clock steps); `utc` is human-readable
wall time. Note: `Date.now()`-style wall time is fine here (real board, not a workflow
script).

```json
{"utc":"2026-07-15T14:03:00Z","mono":1234.5,"src":"tclk",
 "hw":{"event_count":91234,"null_count":0,"error_count":7,"filtered_count":0,
       "overflow":0,"lock":1,"heartbeat":88123456},
 "sw":{"drained":91230,"unsync":3,"published":91230,"queued":0,
       "queue_dropped":0,"redis_dropped":0,"reconnects":0}}
```

### 5.4 `run_pipeline.sh` (tmux launcher)

A small helper that starts a detached tmux session `kr260` with one window per source,
each running a publisher with `--statlog` set:

```
tmux new-session -d -s kr260 -n tclk \
  'sudo python3 redis_publish.py /dev/uio4 --src tclk --statlog stats-tclk.jsonl'
tmux new-window  -t kr260   -n aclk \
  'sudo python3 redis_publish.py /dev/uio5 --src aclk --statlog stats-aclk.jsonl'
```

It assumes the hardware bring-up is already done (bitstream + overlay loaded, WR armed
and locked, `redis-cli ping` returns PONG). It prints a reminder and refuses to launch if
`KR260:status` cannot be reached or the WR timebase is not locked (a cheap pre-flight so a
day-long run does not silently publish nothing). The `/dev/uioN` indices are matched from
`grep . /sys/class/uio/uio*/name` (documented; the script takes them as optional args so a
different enumeration order is handled). Re-attach with `tmux attach -t kr260`; stop with
`tmux send-keys ... C-c` per window (so each publisher writes its final snapshot) then
`tmux kill-session -t kr260`.

### 5.5 `stats_report.py` (board report, pure JSONL reader)

Reads one or more `stats-<src>.jsonl` files (no device access). For each source it takes
the baseline (first) and last snapshot and prints a reconciliation table (section 6). It
never opens `/dev/uioN`, so it is safe to run while the publishers are still live. Output
is plain text to the terminal; no matplotlib dependency on the board.

### 5.6 `plot_stats.py` (PC-side plotter)

Runs on the PC from the copied JSONL log(s). Uses matplotlib (already present on the PC,
per the project's cocotb-plot convention) to save time-series PNGs: event rate,
CRC-error rate, cumulative missed / drops, and WR-lock over the run. It derives rates from
consecutive-snapshot deltas divided by the `mono` delta. Not run on the board.

## 6. Reconciliation math (what `stats_report.py` prints, per source)

Let F = baseline snapshot, L = last snapshot. Hardware counters are absolute since the
last PL reset, so window values are deltas F to L; software counters are cumulative since
publisher start (so F's software counters are ~0 and L's are the totals).

| Metric | Formula | Meaning |
|---|---|---|
| Duration | `utc_L − utc_F` (and `mono_L − mono_F`) | run length |
| Decoded (good) | `EVENT_COUNT_L − EVENT_COUNT_F` | good events the PL enqueued |
| Published | `published_L` | delivered to Redis |
| **Failed CRCs** | `ERROR_COUNT_L − ERROR_COUNT_F` | bad-CRC / decode errors |
| Nulls | `NULL_COUNT_L − NULL_COUNT_F` | idle 0xFF drops (ACLK) |
| Filtered | `FILTERED_COUNT_L − FILTERED_COUNT_F` | drop-mask suppressions |
| **Missed at HW** | `DecodedΔ − drained_L − unsync_L` | FIFO-overflow loss |
| Missed at publisher | `queue_dropped_L + redis_dropped_L` | Redis could not keep up |
| Reconnects | `reconnects_L` | Redis connect/write failures |
| WR health | any snapshot with `lock==0`, or `unsync_L > 0` | timebase gaps |
| Rates | `Decoded / duration`, `FailedCRC / Decoded` | events/s, error % |

**Cross-check (correctness invariant).** The sticky `overflow` bit and the computed
`Missed at HW` must agree in direction: if `overflow == 0` then `Missed at HW` is ~0
(bounded by FIFO residual at the last snapshot); if `Missed at HW > 0` then some snapshot
has `overflow == 1`. The report prints a WARN line if they disagree, which flags either a
counter-read anomaly or an off-by-residual at the final snapshot. On a clean Ctrl-C stop
the final snapshot is taken after the queue is drained, so FIFO residual is ~0 and the
equality is tight.

## 7. Testing (PC, existing stub/manual-runner style)

- `test_readout_common.py` (extended): `read_hw_counters` against a `bytearray`-backed
  `RegIO` returns the expected dict, including the `STATUS` overflow bit decode. The new
  `tick_cb`/`tick_s` path fires the callback on a busy (never-empty) fake FIFO, proving a
  sustained-busy run still snapshots.
- `test_stats_report.py` (new): given a synthetic JSONL log (baseline + a few snapshots),
  the reconciliation matches hand-computed deltas, the overflow / Missed-at-HW cross-check
  fires correctly for both the agreeing and the disagreeing case, and multi-source input
  produces one table per source.
- `test_redis_publish.py` (extended): the `StatsLog` snapshot record has the exact
  `hw`/`sw` shape from section 5.3; `unsync` increments on `ts==0`; record building and
  `should_publish` behavior are unchanged. The lazy-`redis`-import guard still holds
  (importing the modules needs no `redis`, no matplotlib).
- Existing `test_redis_sink.py`, `test_wr_time.py`, `test_tclk_filter.py` stay green
  (changes are additive).
- Board integration (documented in `redis.md`, manual): after a short live run, the JSONL
  log grows one line per interval, `stats_report.py` prints a sane table, and its
  `Published` matches `redis-cli XLEN KR260:<src>` within the last-interval window.

## 8. Deployment

- New files: `deploy/run_pipeline.sh`, `deploy/stats_report.py`, `deploy/plot_stats.py`,
  and their tests.
- Modified: `deploy/readout_common.py`, `deploy/redis_publish.py`.
- `deploy/redis.md` (or a new `deploy/capture.md`) documents the unattended run:
  bring-up checklist -> `run_pipeline.sh` -> `tmux attach` to spot-check ->
  `stats_report.py` on return -> copy the JSONL to the PC -> `plot_stats.py`. The stop
  sequence (Ctrl-C each window so a final snapshot is written, then kill the session) is
  spelled out.
- `hw.ps1` `aclk_pipeline` deploy map gains the new board-side files so they ship with the
  overlay bundle.
- Branch: a new feature branch off `main`.

## 9. Out of scope (YAGNI)

- systemd services / auto-restart on crash / start-on-boot (tmux was chosen).
- On-board plotting / matplotlib on the KR260 (plots run on the PC).
- Turning Redis persistence on, or writing stats into Redis (on-disk JSONL is the durable
  record; Redis stays ephemeral as configured).
- Log rotation (a day at 60 s is ~1440 tiny lines per source; not needed).
- Automating the hardware bring-up (bitstream load + WR arm/lock stays the existing manual
  checklist; the launcher only pre-flight-checks it).
- Any change to the Redis key scheme, the RTL, or the bitstream.
```