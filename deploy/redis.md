# Redis event publishing (board-side, KR260 convention)

> For leaving the board capturing unattended for hours/days and then reconciling
> events-published / missed / failed-CRC statistics, see **capture.md** (tmux launcher +
> on-disk stats log + `stats_report.py`).

Publishes WR-timestamped TCLK/ACLK readout events into local Redis following the
**Fermilab RedisAdapter Protocol Specification v1.0** (repo `fermi-ad/redis-adapter`,
`docs/redis-adapter-implementation-spec.md`), so a generic RedisAdapter consumer reads
our primary data with no producer-specific code. Publish side only. UNSYNC events (ts==0,
WR timebase not locked) are dropped, so arm the WR timebase first (see wr.md).

Key schema is RedisAdapter `{baseKey}:subKey` with the base key braced for Redis Cluster
hash tagging (default base `KR260`). The publisher writes:
- per event: `XADD {KR260}:<src>` where the entry ID is the event's **RA_Time**
  (nanoseconds since the Unix epoch) encoded as `<ms>-<ns_within_ms>` =
  `<floor(RA_Time/1e6)>-<RA_Time mod 1e6>`. Fields:
  - `_` : the mandatory RedisAdapter primary payload, a little-endian packed struct
    `<IIIHB>` = sec (u32), ns (u32), data (u32), event (u16), flags (u8; bit0 has_data,
    bit1 is_tclk). This is the producer/consumer device contract; a generic RA consumer
    reads only `_`.
  - readable extras (ignored by generic RA consumers, used by our archiver/redis-cli):
    `sec, ns, event, data, is_tclk, has_data, src`. There is no per-entry `utc` (derive
    it from sec/ns).
- per event code, per writer batch (<= ~1 s): `HSET {KR260}:event:<src>:0x<CODE>` = that
  code's latest event (`sec, ns, utc, data`) and `HINCRBY ... count <n-in-batch>` (a
  per-code lookup index; counts stay exact, the hash updates per batch not per event).
It also maintains `{KR260}:status` (=1 while alive) and `{KR260}:watchdog` (a TTL key,
refreshed every ~10 s, expiring in 30 s) for liveness.

Stream IDs are explicit and complete (no server `-*` sequence), so this no longer
requires Redis >= 7.0; it works on Redis 6 too. A duplicate or backward RA_Time (same-ns
burst or a backward WR re-arm) is bumped to the previous ID + 1 ns so XADD's
strictly-increasing rule holds; the exact sec/ns always remain in `_` and the fields.

## One-time setup on the board

    sudo apt update && sudo apt install -y redis-server python3-redis
    # Redis >= 7.0 is no longer required: the publisher writes explicit, complete
    # <ms>-<ns_within_ms> stream IDs (never a server-assigned "-*" sequence), and that
    # syntax works on Redis 6 too. Ubuntu's default redis-server is fine as-is.
    redis-cli INFO server | grep redis_version    # informational only, any version works
    # apply the KR260 Redis settings (ephemeral streams, stream tuning), then restart:
    cat redis-kr260.conf | sudo tee -a /etc/redis/redis.conf
    sudo systemctl enable --now redis-server
    sudo systemctl restart redis-server
    redis-cli ping                       # -> PONG
    sudo python3 -c "import redis; print(redis.__version__)"   # redis-py visible to root

## Run (one publisher per source; after the WR timebase is armed + locked)

    sudo python3 redis_publish.py /dev/uio4 --src tclk
    sudo python3 redis_publish.py /dev/uio5 --src aclk

Match the /dev/uioN indices to the readout names with:
    grep . /sys/class/uio/uio*/name

To stream to a different Redis (e.g. a lab RedisAdapter server) instead of the board's
local one, point the publisher at it (no other change needed):

    sudo python3 redis_publish.py /dev/uio4 --src tclk \
        --redis-host redis.example.fnal.gov --redis-port 6379

Ctrl-C stops a publisher (it flushes the queue and prints final stats). The 1 Hz stats
line reports drained / published / queued / queue_dropped / redis_dropped / reconnects.

Options: --namespace (default KR260), --redis-host (127.0.0.1), --redis-port (6379),
--maxlen (stream cap, default 1000000), --queue-size (in-process queue, default 100000).

## Verify

    redis-cli XLEN '{KR260}:tclk'                     # climbs while publishing
    redis-cli XREVRANGE '{KR260}:tclk' + - COUNT 3    # newest 3 (event-time ordered)
    redis-cli HGETALL '{KR260}:event:tclk:0x1D'       # latest event for code 0x1D + count
    redis-cli GET '{KR260}:status'                    # 1 while a publisher is alive
    redis-cli TTL '{KR260}:watchdog'                  # counts down from ~30 while alive

Cross-check the stream against the console reader (they read the same FIFO, so the same
events appear; do NOT run both on the same /dev/uioN at once, they both POP the FIFO):
    sudo python3 tclk_read.py /dev/uio4 --wr

## Gotchas

- Nothing publishes until the WR timebase is armed and locked (UNSYNC events are
  dropped). If XLEN stays 0, run: sudo python3 wr_time.py /dev/uio6 status
- The publisher never blocks the hardware FIFO drain on a Redis stall: it drops the
  oldest queued records (queue_dropped climbs) rather than stalling. A rising
  queue_dropped / redis_dropped means Redis is not keeping up.
- The `reconnects` stat counts Redis connect/publish FAILURES (not successful
  reconnections). If `published` stays 0 while `reconnects` climbs, Redis is not
  reachable: check `redis-cli ping` (is redis-server running?) and that redis-py is
  installed and visible to root (the publisher runs under sudo): `sudo apt install
  python3-redis`, or `sudo pip3 install redis`. A user-only `pip install --user` is
  invisible to `sudo python3`. On a persistent failure the writer backs off ~0.5 s
  between retries (it does not busy-spin).
- Liveness: `{KR260}:watchdog` (a TTL key) is the authoritative signal, it expires within
  ~30 s if the publisher dies. `{KR260}:status` is sticky (set to 1 on connect, not reset
  on stop), so do not trust it alone.
- The event-time stream-ID guard is per publisher process. If you restart a publisher
  within ~1 s of a backward WR re-arm, Redis may reject the first events (their ID is
  below the stream's current top) and they are dropped until wall-clock time passes that
  top. Persistence is off, so restarting redis-server (which clears the stream) avoids
  this; only a publisher-only restart is exposed.
- Stream IDs are the event time, guarded to never go backward. A WR re-arm that jumps
  the clock back briefly clusters a few entries at the last ms instead of erroring.
- Streams are capped at --maxlen (approximate) and Redis persistence is off
  (redis-kr260.conf), so streams are in-memory and start empty on a redis restart.
- redis-server binds localhost by default; keep it that way (no auth is configured).
- Redis >= 7.0 is no longer required. The publisher always sends explicit, complete
  `<ms>-<ns_within_ms>` stream IDs, so XADD is accepted on Redis 6 as well as Redis 7.

## Self-test (no board, needs a redis-server binary)

Prove the publisher emits RedisAdapter v1.0-compliant entries end to end. It spawns a
private redis-server, runs the real producer path, and reads back with an RA-compliant
consumer (`ra_consumer.py`):

    python3 test_ra_roundtrip.py        # or: pytest deploy -q

It SKIPS if `redis-server` is not installed. `ra_consumer.py` doubles as reference code
for a lab-side RedisAdapter consumer.
