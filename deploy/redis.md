# Redis event publishing (board-side, KR260 convention)

Publishes WR-timestamped TCLK/ACLK readout events into local Redis under the `KR260:`
namespace, matching the Fermilab redis-clock-server conventions. Publish side only.
UNSYNC events (ts==0, WR timebase not locked) are dropped, so arm the WR timebase first
(see wr.md).

Per event the publisher writes:
- `XADD KR260:<src>` (the time-ordered event feed; entry ID is the event time in ms),
  fields `sec, ns, utc, event, data, is_tclk, has_data, src`.
- `HSET KR260:event:<src>:0x<CODE>` = that code's latest event (`sec, ns, utc, data`) and
  `HINCRBY ... count 1` (a per-code lookup index).
It also maintains `KR260:status` (=1 while alive) and `KR260:watchdog` (a TTL key,
refreshed every ~10 s, expiring in 30 s) for liveness.

## One-time setup on the board

    sudo apt update && sudo apt install -y redis-server python3-redis
    # Redis MUST be >= 7.0 (the <ms>-* event-time stream ID syntax is a Redis 7 feature;
    # Ubuntu's default redis 6.x rejects every XADD and publishes nothing). If your
    # redis-server is 6.x, install Redis 7 from packages.redis.io:
    #   curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg
    #   echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/redis.list
    #   sudo apt update && sudo apt install -y redis
    redis-cli INFO server | grep redis_version    # confirm >= 7.0
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

Ctrl-C stops a publisher (it flushes the queue and prints final stats). The 1 Hz stats
line reports drained / published / queued / queue_dropped / redis_dropped / reconnects.

Options: --namespace (default KR260), --redis-host (127.0.0.1), --redis-port (6379),
--maxlen (stream cap, default 1000000), --queue-size (in-process queue, default 100000).

## Verify

    redis-cli XLEN KR260:tclk                        # climbs while publishing
    redis-cli XREVRANGE KR260:tclk + - COUNT 3       # newest 3 (event-time ordered)
    redis-cli HGETALL KR260:event:tclk:0x1D          # latest event for code 0x1D + count
    redis-cli GET KR260:status                       # 1 while a publisher is alive
    redis-cli TTL KR260:watchdog                     # counts down from ~30 while alive

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
- Liveness: `KR260:watchdog` (a TTL key) is the authoritative signal, it expires within
  ~30 s if the publisher dies. `KR260:status` is sticky (set to 1 on connect, not reset
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
- Requires redis-server >= 7.0; on 6.x every XADD is rejected (invalid stream ID) and
  published stays 0 (with backoff, not a spin).
