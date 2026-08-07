# Cold start: board to lab Redis

Run in this order. Target is the lab Redis `10.200.12.100:6379`, base key `{TCLK}`.

## 0. Log in

    ssh ubuntu@aclk-timestamper.fnal.gov

Connect to outland first (controls network). Serial console fallback: 115200 8N1,
user `ubuntu`, password `kr260pwd`.

## 1. Clock

    chronyc waitsync 60 0.1
    timedatectl

The RTC is dead, so the clock boots ~58 days wrong until chrony steps it. Arming
before this mislabels every timestamp.

## 2. FPGA

    cd ~/aclk_pipeline
    sudo xmutil unloadapp
    sudo fpgautil -b uart_echo_bd_wrapper.bit.bin -o aclk_pipeline.dtbo
    grep . /sys/class/uio/uio*/name

Loads the bitstream. The grep shows which /dev/uioN each readout got: expect
tclk_readout=uio4, aclk_readout=uio5, wr_timebase=uio6. If they differ, pass them
to `run_pipeline.sh` in step 5 as `./run_pipeline.sh /dev/uioTCLK /dev/uioACLK /dev/uioWR`.

## 3. Timebase

    sudo python3 wr_time.py /dev/uio6 status
    sudo python3 wr_time.py /dev/uio6 arm
    sudo python3 wr_time.py /dev/uio6 status

Before arming: `pps_alive=1 clk10_alive=1`, `CELLS_LAST=10000000`, `PPS_REJECT=0`.
After: all `locked_*=1`, `HW - system clock` a few ms. Unlocked means every event
stamps UNSYNC and the publisher drops it.

## 4. Redis reachable

    redis-cli -h 10.200.12.100 ping

Never publish to the board's local Redis: it looks healthy and nobody downstream
sees anything.

## 5. Launch

    sudo env REDIS_HOST=10.200.12.100 BASE=TCLK ARCHIVE= ./run_pipeline.sh

Starts tmux session `kr260` (publisher + WR guard). `ARCHIVE=` skips the CSV
archiver, which writes ~370 MB/day masked or ~3.2 GB/day unmasked and has filled
the disk before. Without it the board writes ~1 MB/day (stats + logs), or ~7 MB/day
with the PPS monitor of step 7 also running. `sudo env VAR=`, never `export VAR=`; sudo drops exported vars and you
would publish locally. Check the host in the launch banner.

Add `DROP=` to publish `$07` too (~820 ev/s instead of ~98):

    sudo env REDIS_HOST=10.200.12.100 BASE=TCLK DROP= ARCHIVE= ./run_pipeline.sh

The mask lives in a PL register that survives launches and clears only on step 2.

## 6. Verify

    tail -3 stats-tclk.jsonl
    redis-cli -h 10.200.12.100 HTTL '{TCLK}:watchdog' FIELDS 1 kr260-tclk
    redis-cli -h 10.200.12.100 XREVRANGE '{TCLK}:STREAM' + - COUNT 1
    sudo python3 stats_report.py stats-tclk.jsonl

In `stats-tclk.jsonl`: `published` climbing is success, `drained` stuck means no
hardware events, `unsync` climbing means the timebase is unlocked. `HTTL` ~1 means
the publisher is alive. `XREVRANGE` gives the newest event, its ID is the event
time in ms. `stats_report.py` reconciles decoded vs drained vs published.

Do not judge by `XLEN`: the stream is trimmed, so it sits pinned at ~10,000 and
looks frozen even when everything works.

## 7. PPS monitor (optional)

    sudo tmux new-session -d -s ppsmon \
      "cd /home/ubuntu/aclk_pipeline && python3 wr_pps_live.py /dev/uio6 | tee -a pps-live.log"
    tail -3 ~/aclk_pipeline/pps-live.log

One line per second: edge count, missing edges, rejected glitches, HW-vs-NTP
offset, ppm. ~7 MB/day. Plot a copied log on a PC with `deploy/plot_pps_log.py`.

## 8. Stop

    sudo tmux send-keys -t kr260:tclk C-c
    sudo tmux kill-session -t kr260
    sudo tmux kill-session -t ppsmon

Ctrl-C first so the publisher writes its final snapshot.

## 9. Disk

    df -h /
    rm ~/aclk_pipeline/events-tclk-YYYYMMDD.csv

---

## Bench hardware

PMOD1, LVCMOS33, wire by package pin. Connector position numbers are ambiguous
across sources.

| Pin | Signal | Notes |
|---|---|---|
| H12 | TCLK in | biphase-mark, ~10 MHz, 3.3 V push-pull |
| B10 | ACLK-Lite out | Manchester mirror, scope probe only |
| E10 | WR 10 MHz in | through the conditioning network |
| E12 | WR PPS in | with the damping resistor |

Two hand-built circuits sit between the WR node and the board, currently in a
green 3D-printed box that should be replaced with something sturdier. The
timebase does not lock without them, so check them first after any rewiring.

**10 MHz conditioner.** The WR node puts out a sine centered on zero, and the
negative half will damage the Pmod input. A series capacitor removes it; two
resistors, one to ground and one to 3.3 V from the Pmod, bias what is left into
something the pin reads as a TTL square wave.

**PPS damping resistor.** The source expects 50 ohm and the Pmod input is high
impedance, so the pulse reflected off the unterminated end and came back as extra
edges. 180 ohm from the cable's center conductor to ground at the board end damps
that while leaving enough amplitude to cross the logic threshold. Symptom when
missing: `PPS_REJECT` climbing.
