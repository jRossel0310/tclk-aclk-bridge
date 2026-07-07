# Redis event publishing (board-side)

Publishes WR-timestamped TCLK/ACLK readout events into local Redis Streams so
other processes on the KR260 can consume a durable, ordered feed. Publish side
only. UNSYNC events (ts==0, WR timebase not locked) are dropped, so arm the WR
timebase first (see wr.md).

## One-time setup on the board

    sudo apt update && sudo apt install -y redis-server
    sudo systemctl enable --now redis-server
    redis-cli ping                       # -> PONG
    pip install -r requirements-board.txt   # installs redis-py

## Run (one publisher per source; after the WR timebase is armed + locked)

    sudo python3 redis_publish.py /dev/uio4 --stream events:tclk --src tclk
    sudo python3 redis_publish.py /dev/uio5 --stream events:aclk --src aclk

Match the /dev/uioN indices to the readout names with:
    grep . /sys/class/uio/uio*/name

Ctrl-C stops a publisher (it flushes the queue and prints final stats). The 1 Hz
stats line reports drained / published / queued / queue_dropped / redis_dropped /
reconnects.

Options: --redis-host (default 127.0.0.1), --redis-port (6379), --maxlen (stream
cap, default 1000000), --queue-size (in-process queue, default 100000).

## Verify

    redis-cli XLEN events:tclk                       # climbs while publishing
    redis-cli XREVRANGE events:tclk + - COUNT 5      # newest 5 entries

Each entry carries: sec, ns, utc, event, data, is_tclk, has_data, src. Cross-check
against the console reader (they read the same FIFO, so the same events/timestamps
appear):
    sudo python3 tclk_read.py /dev/uio4 --wr

## Gotchas

- Nothing publishes until the WR timebase is armed and locked (UNSYNC events are
  dropped). If XLEN stays 0, run: sudo python3 wr_time.py /dev/uio6 status
- The publisher never blocks the hardware FIFO drain on a Redis stall: it drops the
  oldest queued entries (queue_dropped climbs) rather than stalling. A rising
  queue_dropped / redis_dropped means Redis is not keeping up.
- The `reconnects` stat counts Redis connect/publish FAILURES (not successful
  reconnections). If `published` stays 0 while `reconnects` climbs, Redis is not
  reachable: check `redis-cli ping` (is redis-server running?) and that redis-py is
  installed (`pip install -r requirements-board.txt`) -- a missing redis-py shows up
  as this same climbing-reconnects, published=0 pattern.
- Streams are capped at --maxlen (approximate); old entries are trimmed by Redis.
- redis-server binds localhost by default; keep it that way (no auth is configured).
