# Redis event publishing (board side, `{TCLK}` base key)

> For leaving the board capturing unattended for hours/days and then reconciling
> events-published / missed / failed-CRC statistics, see **capture.md** (tmux launcher +
> on-disk stats log + `stats_report.py`).

Publishes WR-timestamped TCLK readout events into the lab's **redis-clock-server**
deployment (`bidaqt-tclk`), following its handoff contract and the **Fermilab
RedisAdapter Protocol Specification v1.0** (repo `fermi-ad/redis-adapter`,
`docs/redis-adapter-implementation-spec.md`). That deployment reserves the `{TCLK}` base
key for this decoder and already serves it as EPICS PVs: its `redis-pvxs-ioc` publishes
`EPICS_REDIS_TCLK:RT:<HEX>`, `:RT:<HEX>_C` and `:RT:STREAM` over PVAccess, backed by our
keys. Publish side only. UNSYNC events (ts==0, WR timebase not locked) are dropped, so
arm the WR timebase first (see wr.md).

## Key space

Keys are `{<baseKey>}:<subKey>` with literal braces (Redis Cluster hash tagging). We
build the braces ourselves; we do not link the `redis-adapter` library. `<HEX>` is the
event code as exactly two uppercase zero-padded hex digits.

| Key | Type | `_` payload | Width | Meaning |
| --- | --- | --- | --- | --- |
| `{TCLK}:<HEX>` | stream | `int64` LE | 8 B | RA_Time of that occurrence, ns since the Unix epoch |
| `{TCLK}:<HEX>_C` | stream | `int64` LE | 8 B | occurrences of `<HEX>` since this publisher started |
| `{TCLK}:STREAM` | stream | `uint16` LE | 2 B | the event code; one entry per event, the combined feed |
| `{TCLK}:watchdog` | hash | (field value is text) | - | field `kr260-tclk` = build version, expires 1 s after we stop refreshing |

Three XADDs per event. Every entry carries exactly one field, `_`, holding raw
little-endian bytes: never JSON, never text. **The widths are load-bearing**: a consumer
reading the wrong width gets a garbage value, not an error.

- **Stream IDs are event time**, not delivery time: the event's RA_Time (ns since the
  epoch) encoded as `<ms>-<ns_within_ms>` = `<floor(RA_Time/1e6)>-<RA_Time mod 1e6>`.
  Entries correlate across streams by accelerator time.
- **IDs are strictly increasing.** On a same-ns tie or a backward WR re-arm the sink
  bumps the ID to the previous one + 1 ns (the deployment's documented `max(raw, last+1)`
  policy). The guard is **per key**, so the shared `STREAM` feed absorbs bumps its
  per-code streams never see. A `{TCLK}:<HEX>` entry's `_` payload always holds the true,
  unbumped RA_Time even when its ID was nudged.
- **Counters reset to zero when the publisher restarts.** That is the intended restart
  signal, not corruption. They live in the process, so an outage that drops events also
  skips those counts rather than inventing them.
- **Retention** is `MAXLEN ~ 10000` per stream (the deployment's number), about 100 s of
  the combined feed at 99 ev/s. Anything wanting more history must archive
  (`stream_archive.py`); see capture.md.
- **Outages drop events.** The publisher never buffers or replays across a Redis outage
  and never fabricates a timestamp to cover a gap.

There is no event **data word** in this key space and no ACLK source: the contract has
nowhere to put either. Both are deferred, so `run_pipeline.sh` launches the TCLK
publisher only. Pointing an ACLK publisher at `{TCLK}` would interleave ACLK codes into
the TCLK streams; it needs a base key of its own first.

## One-time setup on the board

    sudo apt update && sudo apt install -y redis-server
    sudo pip3 install -r requirements-board.txt     # redis-py >= 5.1, for HEXPIRE
    # Publishing to the LOCAL redis for a bench test also needs redis-server >= 7.4
    # (hash-field TTLs). The lab server runs redis:7-alpine, which has them.
    redis-cli INFO server | grep redis_version
    # local bench only: apply the KR260 Redis settings, then restart
    cat redis-kr260.conf | sudo tee -a /etc/redis/redis.conf
    sudo systemctl enable --now redis-server
    sudo systemctl restart redis-server
    redis-cli ping                       # -> PONG
    sudo python3 -c "import redis; print(redis.__version__)"   # >= 5.1, visible to root

## Run (after the WR timebase is armed + locked)

    sudo python3 redis_publish.py /dev/uio4 --src tclk

Match the `/dev/uioN` index to the readout name with:

    grep . /sys/class/uio/uio*/name

To publish to the lab deployment instead of the board's local Redis:

    sudo env REDIS_PASSWORD=... python3 redis_publish.py /dev/uio4 --src tclk \
        --redis-host bidaqt-tclk --redis-port 6379

Ctrl-C stops it (it flushes the queue and prints final stats). The 1 Hz stats line
reports drained / unsync / published / queued / queue_dropped / redis_dropped /
reconnects, plus `last_error` whenever Redis is unhealthy.

Options: `--base` (default `TCLK`, `--namespace` still accepted), `--redis-host`
(127.0.0.1), `--redis-port` (6379), `--maxlen` (10000), `--queue-size` (100000),
`--watchdog-field` (`kr260-tclk`), `--build-version` (the watchdog field's value),
`--drop` (PL drop-mask codes).

## Verify

    redis-cli XLEN '{TCLK}:STREAM'                     # climbs while publishing
    redis-cli XREVRANGE '{TCLK}:STREAM' + - COUNT 3    # newest 3 (event-time ordered)
    redis-cli XREVRANGE '{TCLK}:1D' + - COUNT 1        # latest time for code 0x1D
    redis-cli XREVRANGE '{TCLK}:1D_C' + - COUNT 1      # its occurrence count
    redis-cli HGETALL '{TCLK}:watchdog'                # our field = build version
    redis-cli HTTL '{TCLK}:watchdog' FIELDS 1 kr260-tclk   # counts down ~1 s while alive

The payloads are binary, so `redis-cli` prints them as escapes. `ra_consumer.py` decodes
them and doubles as reference code for a lab-side consumer:

    python3 -c "import redis, ra_consumer as ra; c=redis.Redis(); \
      print([ra.decode_event_id(f) for _,f in c.xrange(ra.stream_key('TCLK'))][-5:])"

Cross-check against the console reader (it reads the same FIFO, so do NOT run both on
the same `/dev/uioN` at once, they both POP the FIFO):

    sudo python3 tclk_read.py /dev/uio4 --wr

## Gotchas

- Nothing publishes until the WR timebase is armed and locked (UNSYNC events are
  dropped). If XLEN stays 0, run: `sudo python3 wr_time.py /dev/uio6 status`
- **redis-py < 5.1 has no HEXPIRE.** The watchdog field would never expire and liveness
  would read as alive forever, so the publisher refuses to fake it: `last_error` says so
  and `reconnects` climbs while `published` stays 0. Fix with
  `sudo pip3 install -U redis`.
- The publisher never blocks the hardware FIFO drain on a Redis stall: it drops the
  oldest queued records (queue_dropped climbs) rather than stalling. Rising
  queue_dropped / redis_dropped means Redis is not keeping up.
- Three XADDs per event, up from roughly one under the old scheme. redis-py command
  building was already the throughput cap on the board (~560 us per 3-command record,
  so order 1800 ev/s), against ~99 ev/s sustained. Headroom exists but is no longer
  large; watch `queued` during bursts.
- `reconnects` counts Redis connect/publish FAILURES (not successful reconnections).
  If `published` stays 0 while `reconnects` climbs, read `last_error`. Common causes:
  redis-server unreachable (`redis-cli ping`), redis-py not visible to root (the
  publisher runs under sudo, so `sudo pip3 install redis`, not `pip install --user`),
  or the HEXPIRE case above. On persistent failure the writer backs off ~0.5 s between
  retries (it does not busy-spin).
- Liveness is the watchdog field's TTL, not its presence: check `HTTL`, not `HGETALL`.
- Retention is only ~100 s. A restarted archiver that was down longer than that has a
  real hole; the stream cannot backfill it.
- Streams are capped with an approximate MAXLEN and the lab Redis runs without
  persistence, so a Redis restart clears every key and producers repopulate as events
  arrive.

## Self-test

`test_ra_roundtrip.py` runs the REAL producer path (`RecordBuilder` + `RedisSink`) and
reads it back with the independent `ra_consumer` decoders, checking keys, payload widths,
counter sequences, ID monotonicity and the watchdog TTL:

    python3 test_ra_roundtrip.py        # or: pytest deploy -q

The in-memory level always runs. Two further tests spin up a private `redis-server` to
prove real XADD accepts our explicit RA_Time IDs and real HEXPIRE gives the watchdog
field a TTL; they SKIP if `redis-server` is not on PATH (as on a Windows dev box), so run
`pytest deploy -q` on the board or a Linux host to exercise them.
