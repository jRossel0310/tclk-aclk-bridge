# Redis Event Publishing Design

Date: 2026-07-07
Status: approved (brainstorm complete)
Scope: board-side Python only (`deploy/`). No RTL, no bitstream change.

## 1. Problem and goal

The pipeline's readout scripts (`deploy/tclk_read.py`, `deploy/aclkgt_read.py`) drain
decoded, WR-timestamped events from the UIO register blocks and print them to a
console. Nothing persists or exposes those events to other processes.

Goal: publish each timestamped event to a local Redis node as it is drained, so
downstream consumers on the board (loggers, dashboards, analysis) can read a durable,
ordered, replayable event feed. This design covers the publish side only.

## 2. Decisions made during brainstorm

| Question | Decision |
|---|---|
| Redis pattern | Streams (XADD): ordered, durable, replayable log per source |
| Redis host | On the KR260 (`redis-server`, 127.0.0.1:6379); standing it up is in scope |
| Sources | Both readouts, separate streams: `events:tclk` (uio4), `events:aclk` (uio5) |
| Entry schema | Full flat fields (sec, ns, utc, event, data, is_tclk, has_data, src) |
| Code shape | New publisher reusing a shared drain helper; existing print readers untouched |
| Redis-unavailable behavior | Non-blocking: never stall the UIO drain; reconnect; cap streams with MAXLEN |
| UNSYNC events (ts==0) | Dropped: only WR-synced events are published |
| Drain vs publish isolation | Background writer thread + bounded in-process queue |

## 3. Architecture

One **publisher process per source** (mirrors the two existing readers), each running a
two-thread model to isolate the hardware FIFO from Redis latency:

- **Drain thread** reuses `readout_common` (`open_dev`, the STATUS-poll / `read_event` /
  POP loop via a new shared `drain_events` helper, `wr_split`). It pulls events off the
  UIO FIFO as fast as it can, drops UNSYNC events (ts == 0) immediately, builds an event
  dict for the rest, and `put_nowait()`s it onto a bounded `queue.Queue`. On a full queue
  it drops the OLDEST entry (counting the drop) and enqueues the new one, so the drain
  never blocks.
- **Writer thread** pops event dicts off the queue in batches and pipelines `XADD` into
  that source's stream with `MAXLEN ~ <cap>`. On any Redis exception it logs, counts a
  redis-drop, and reconnects with backoff.

The bounded queue is the isolation boundary: a Redis stall fills the queue and the drain
side drops oldest, but the drain thread never stalls, so the hardware FIFO cannot
overflow because of Redis. Because UNSYNC events are dropped, every published entry has a
valid WR timestamp (sec/ns > 0).

Redis runs locally on the KR260 (`redis-server`, 127.0.0.1:6379); installing and enabling
it is part of the plan.

## 4. Components and files (all under `deploy/`)

- **`redis_sink.py`** (new): the Redis writer. Wraps `redis-py`, owns the connection +
  reconnect/backoff, the bounded queue, the background writer thread, pipelined `XADD`
  with `MAXLEN ~`, and the drop/reconnect counters. Single responsibility; unit-testable
  against a stub Redis with no board and no server. Public surface (consumed by
  `redis_publish.py`):
  - `RedisSink(host, port, stream, maxlen, queue_size)`
  - `.start()` / `.stop(timeout)` (starts/joins the writer thread; stop flushes best-effort)
  - `.submit(event_dict)` (called by the drain thread; non-blocking, drop-oldest on full)
  - `.stats()` -> dict: `published`, `queue_dropped`, `redis_dropped`, `reconnects`, `queued`
- **`redis_publish.py`** (new): entry point. Parses args, opens the UIO via
  `readout_common.open_dev`, constructs a `RedisSink`, runs `drain_events(io, on_event)`
  where `on_event` filters UNSYNC then `sink.submit(...)`, prints a 1 Hz stats line, and on
  Ctrl-C stops the drain, flushes the sink, prints final stats.
- **`test_redis_sink.py`** (new): PC-runnable unit tests, manual-runner style matching the
  existing `deploy/test_*.py`. Uses a stub Redis object that records `xadd`/`pipeline`
  calls. No `redis` server required.
- **`redis.md`** (new): board runbook (install/enable redis, install deps, launch both
  publishers, verify with `redis-cli`).

Shared drain helper added to **`readout_common.py`**:
- `drain_events(io, on_event, idle_cb=None, poll_s=0.001)`: the STATUS-poll /
  `read_event` / POP loop, calling `on_event(event_dict)` per drained event and `idle_cb()`
  roughly once per second while the FIFO is empty (for the stats line). Returns on
  `KeyboardInterrupt`. The event dict:
  ```python
  {"event": int, "flags": int, "data": int, "ts": int,
   "is_tclk": 0|1, "has_data": 0|1}
  ```
  The existing print readers keep their `stream_events` as-is; they are NOT refactored onto
  `drain_events` now (a conscious YAGNI to avoid disturbing working readers). This leaves
  two drain loops in the module; acceptable, noted here so it is a deliberate choice.

## 5. Stream entry schema

Redis auto-assigns each entry's `<ms>-<seq>` ID (arrival time); the WR event time lives in
fields. All values are strings (Redis stream convention); numeric fields are decimal.

```
XADD events:tclk MAXLEN ~ <maxlen> *
    sec       <u32>            # WR seconds (Unix UTC), always > 0 (UNSYNC dropped)
    ns        <u32>            # WR nanoseconds within the second
    utc       <ISO8601 Z>      # e.g. 2026-07-07T13:45:11.001747250Z  (from wr_utc)
    event     <int>            # event code
    data      <int>            # 64-bit payload, decimal
    is_tclk   <0|1>
    has_data  <0|1>
    src       tclk             # or "aclk" on the ACLK stream
```

Stream name and `src` come from CLI args, so the same code serves both sources.

## 6. CLI

```
redis_publish.py /dev/uioN --stream events:tclk --src tclk \
    [--redis-host 127.0.0.1] [--redis-port 6379] \
    [--maxlen 1000000] [--queue-size 100000]
```
Argument parsing uses `readout_common.parse_args` (the repo's existing tiny parser).

## 7. Error handling and resilience

- **Redis down at startup:** publisher starts, drain runs, writer retries with backoff;
  events queue then drop-oldest until Redis appears, then flow resumes. No crash.
- **Redis stall / disconnect mid-run:** writer catches the exception, counts a redis-drop,
  reconnects; the drain thread is unaffected.
- **Queue full:** drop oldest, increment `queue_dropped`; bounded memory.
- **UIO / AXI wedge:** the existing `readout_common` watchdog still fires and names the
  stuck register, exactly as in the readers.
- **Ctrl-C:** stop draining, best-effort flush of already-queued entries (short timeout),
  close Redis, print final stats.
- **Visibility:** a 1 Hz stats line reports `drained / published / queue_dropped /
  redis_dropped / reconnects / queued`, so no drop is silent.

## 8. Testing

- **PC unit tests** (`test_redis_sink.py`, no board, no server):
  - event dict -> XADD field mapping matches section 5 (field names, decimal values, `src`).
  - `MAXLEN ~ <maxlen>` is passed on every XADD.
  - queue-full drops the OLDEST and increments `queue_dropped`.
  - a raised Redis error triggers a counted redis-drop and a reconnect attempt.
  - clean `.stop()` flushes queued entries and joins the thread.
- **`drain_events` unit test** (added to `test_readout_common.py`): a scripted `FakeIO`
  (same pattern as the `stream_events` WR test) drives the poll loop; asserts `on_event`
  receives the decoded fields and that POP advances. UNSYNC filtering is verified in the
  publisher layer (the drain helper itself is source-agnostic and does not filter).
- **Board integration smoke** (documented in `redis.md`): with a locked WR timebase,
  `sudo systemctl start redis-server`, run one publisher, confirm `redis-cli XLEN
  events:tclk` climbs and `redis-cli XREVRANGE events:tclk + - COUNT 5` shows sec/ns/utc/
  event fields consistent with `tclk_read.py --wr`.

## 9. Deployment

- New board dependency: `redis-py`. Add `deploy/requirements-board.txt` (kept separate
  from the sim-only root `requirements.txt`) listing `redis`; `redis.md` installs it with
  `pip install -r requirements-board.txt`.
- `hw.ps1` deploy map for `aclk_pipeline` gains `redis_sink.py`, `redis_publish.py`,
  `requirements-board.txt`. While editing that map, also add the two files this session's
  WR bring-up found missing from it: `aclk_pipeline.dts` and `wr_time.py`.
- `redis.md`: `sudo apt install redis-server` + `sudo systemctl enable --now
  redis-server`, install deps, launch both publishers (one per UIO node), verify.

## 10. Out of scope (YAGNI)

- Consumer / analysis / dashboard applications; consumer groups.
- Redis auth / TLS / remote Redis (localhost only for now).
- Publishing UNSYNC events (dropped by decision).
- Refactoring the print readers onto `drain_events`.
- A merged single stream (kept separate per source).
