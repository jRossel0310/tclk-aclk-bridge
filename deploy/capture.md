# Unattended TCLK/ACLK capture + error check (KR260)

Runs both publishers unattended in a tmux session, snapshots every counter to an on-disk
JSONL log, and reconciles the log into an events-published / missed / failed-CRC report.
Builds on the Redis publisher (see redis.md, in the repo's deploy/ directory on the PC);
the JSONL log is the durable record, so it survives a redis restart or reboot (Redis
persistence stays off).

## 1. Bring-up (once, per the pasted checklist)

    cd aclk_pipeline
    # load the bitstream + overlay (see redis.md / the project runbook, both in the
    # repo's deploy/ directory on the PC), then:
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
    redis-cli XLEN '{KR260}:tclk'            # climbs
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
totals from the last snapshot. The report also prints an "undelivered" count (events still
in the queue at stop, e.g. Redis down/backlog) and a "ledger check" line (decoded should
equal published + missed + queued + unsync). If you re-run the capture into the same
stats-*.jsonl, snapshots are appended; the report detects the restart and reconciles only
the most recent run (delete or rename the log between runs to keep them separate).

## 5. Plots (on the PC)

    scp ubuntu@<board>:~/aclk_pipeline/stats-*.jsonl .
    python plot_stats.py stats-tclk.jsonl stats-aclk.jsonl   # -> plot-tclk.png, plot-aclk.png

## Options

`redis_publish.py` gains `--statlog <path>` (default `stats-<src>.jsonl`) and
`--snapshot-interval <sec>` (default 60). Everything else is unchanged from redis.md
(deploy/redis.md in the repo on the PC; not copied to the board).

## How "missed" is measured

`EVENT_COUNT` (0x70) counts events presented to the FIFO, including ones later lost to a
full FIFO, so `missed @ HW = decodedDelta - drained - unsync` recovers overflow loss as a
number even though the hardware exposes overflow only as a sticky bit (STATUS bit1). The
report cross-checks the two: if it computes loss but the overflow bit was never set (or
vice versa) it prints a WARN. Tolerance is one FIFO depth (64) of residual at the final
snapshot, so a normal run stays in the clean band; a real in-window overflow sets the
sticky bit and prints the WARN instead.

## ACLK drain ceiling (rate-hardening check)

With the ACLK line (or the loopback generator) actively producing events:

    sudo python3 bench_drain.py /dev/uio5 --seconds 10

Report is `<events> in <s>s = <rate>/s overflow=<bool>`. Interpretation:
- overflow=False and rate >= your target sustained ACLK rate: the path keeps up.
- overflow=True: events are being dropped; the 2048-deep FIFO absorbed the burst
  but the sustained rate exceeds the drain ceiling. The lever is drain speed
  (software), not more FIFO depth.

## 5 ns TCLK build bring-up (operator)

The deployable 5 ns build (clk_40m=200 MHz, serdec OSR=40) is a NEW bitstream. To bring it up:

1. scp the new `uart_echo_bd_wrapper.bit.bin`; `md5sum` on the board must match the build manifest.
2. `sudo xmutil unloadapp` ; `sudo fpgautil -b ~/<bin> -o aclk_pipeline.dtbo`
3. Arm WR: `sudo python3 wr_time.py /dev/uio6 arm` ; confirm `locked_tclk=1` (the 200 MHz timebase constants were sim-proven, but confirm lock on the real 10 MHz + PPS).
4. Confirm live TCLK still decodes: `EVENT_COUNT` climbs, `ERROR_COUNT` stays flat, and spot-check event codes vs the 25 ns build. Watch the real-line ERROR_COUNT rate (and whether the sticky SIG_ERR bit ever latches) vs the old build: the serdec immediate-edge glitch window is ~5x narrower at 400 MHz (a documented bring-up risk), so a rise in errors on the live line would point there.
