# Unattended TCLK/ACLK capture + error check (KR260)

Runs both publishers unattended in a tmux session, snapshots every counter to an on-disk
JSONL log, and reconciles the log into an events-published / missed / failed-CRC report.
Builds on the Redis publisher (see redis.md); the JSONL log is the durable record, so it
survives a redis restart or reboot (Redis persistence stays off).

## 1. Bring-up (once, per the pasted checklist)

    cd aclk_pipeline
    # load the bitstream + overlay (see redis.md / the project runbook), then:
    grep . /sys/class/uio/uio*/name          # note tclk_readout / aclk_readout / wr_timebase indices
    timedatectl                              # System clock synchronized: yes
    sudo python3 wr_time.py /dev/uio6 arm
    sudo python3 wr_time.py /dev/uio6 status # want locked_tclk=1 locked_aclk=1 locked_mon=1
    redis-cli ping                           # PONG

## 2. Launch the capture (survives SSH disconnect)

    sudo ./run_pipeline.sh                   # defaults: uio4 tclk, uio5 aclk, uio6 wr
    # or pass indices:  sudo ./run_pipeline.sh /dev/uio4 /dev/uio5 /dev/uio6

Pre-flight refuses to launch unless Redis answers PONG and the WR timebase is fully
locked (an unlocked timebase stamps every event UNSYNC and they would all be dropped).
Override with FORCE=1 only if you deliberately want to capture while unlocked.

Spot-check while it runs:

    sudo tmux attach -t kr260                # Ctrl-b d to detach
    redis-cli XLEN KR260:tclk                # climbs
    tail -f stats-tclk.jsonl                 # one JSON line per snapshot (~60 s)

## 3. Stop cleanly (writes a final post-flush snapshot)

    sudo tmux send-keys -t kr260:tclk C-c
    sudo tmux send-keys -t kr260:aclk C-c
    sudo tmux kill-session -t kr260

If a publisher dies uncleanly, the report still works off the last periodic snapshot; you
just lose up to the last interval (~60 s) of counts.

## 4. Error check (on the board)

    sudo python3 stats_report.py stats-tclk.jsonl stats-aclk.jsonl

Per source it prints decoded (good events the PL enqueued), published, failed CRCs,
nulls/filtered, missed at the hardware (FIFO overflow) and at the publisher (queue+redis
drops), reconnects, WR-lock health, and an overflow cross-check. `decoded`, `failed CRC`,
`nulls`, and `filtered` are baseline-to-last deltas; the software counters are cumulative
totals from the last snapshot.

## 5. Plots (on the PC)

    scp ubuntu@<board>:~/aclk_pipeline/stats-*.jsonl .
    python plot_stats.py stats-tclk.jsonl stats-aclk.jsonl   # -> plot-tclk.png, plot-aclk.png

## Options

`redis_publish.py` gains `--statlog <path>` (default `stats-<src>.jsonl`) and
`--snapshot-interval <sec>` (default 60). Everything else is unchanged from redis.md.

## How "missed" is measured

`EVENT_COUNT` (0x70) counts events presented to the FIFO, including ones later lost to a
full FIFO, so `missed @ HW = decodedDelta - drained - unsync` recovers overflow loss as a
number even though the hardware exposes overflow only as a sticky bit (STATUS bit1). The
report cross-checks the two: if it computes loss but the overflow bit was never set (or
vice versa) it prints a WARN. Tolerance is one FIFO depth (64) of residual at the final
snapshot; a clean Ctrl-C stop drains first, so the equality is tight.
