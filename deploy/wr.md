# White Rabbit timebase bring-up (integrated pipeline bitstream)

The pipeline stamps every TCLK/ACLK event with `{sec[31:0], ns[31:0]}` (Unix UTC :
nanoseconds), disciplined by a WR node's 10 MHz + PPS. STRICT semantics: a
timestamp of 0 means "not WR-synced when stamped"; readers print it as UNSYNC.

## Wiring (Pmod 1)

| Pmod 1 pin | package pin | signal |
|---|---|---|
| 1 | H12 | tclk (existing input) |
| 2 | B10 | aclk_lite_out (existing output) |
| 3 | E10 | wr_clk10 (WR 10 MHz, 3.3V CMOS in) |
| 4 | E12 | wr_pps (WR PPS, 3.3V CMOS in) |

The WR source must drive push-pull 3.3V CMOS (the carrier's auto-direction level
translators misbehave with open-drain). PPS must be phase-aligned to the 10 MHz
(a real WR node, or the replica generator project, does this by construction).

## Load

    dtc -@ -O dtb -o aclk_pipeline.dtbo aclk_pipeline.dts
    sudo xmutil unloadapp
    sudo fpgautil -b uart_echo_bd_wrapper.bit.bin -o aclk_pipeline.dtbo

Three UIO nodes appear: tclk_readout @ 0x8000_0000, aclk_readout @ 0x8001_0000,
wr_timebase @ 0x8002_0000. Match /dev/uioN indices via
`grep . /sys/class/uio/uio*/name`.

## Sync (needs an NTP-disciplined system clock: chrony or systemd-timesyncd)

    sudo python3 wr_time.py /dev/uio6 status   # expect pps_alive=1 clk10_alive=1,
                                               # CELLS_LAST ~= 10000000, PPS_REJECT=0
    sudo python3 wr_time.py /dev/uio6 arm      # arms floor(now)+1 for the next PPS
    sudo python3 wr_time.py /dev/uio6 status   # expect locked_* = 1, |HW-system| < 0.5 s

`arm` reads the system clock to name the second, so it checks that clock first and
REFUSES to arm if it looks untrustworthy. That check is not cosmetic: this board has a
dead RTC, so every boot starts about 58 days out until chrony steps it, and arming
inside that window labels every later timestamp wrong without STATUS showing anything.
Wait for NTP rather than reaching for the override.

    sudo python3 wr_time.py /dev/uio6 arm --verify-after 90   # re-check 90 s later,
                                                              # catching a late NTP step
    sudo python3 wr_time.py /dev/uio6 arm --force             # arm despite a bad clock
    sudo python3 wr_time.py /dev/uio6 disarm    # force unlock (everything reads UNSYNC)
    sudo python3 wr_time.py /dev/uio6 clear     # clear the lost_lock sticky

## Unattended runs

The timebase is strict, so any WR blip unlocks it permanently and every event stamped
afterwards is UNSYNC-dropped. For anything longer than a supervised session, run the
guard, which polls STATUS and re-arms by itself:

    sudo python3 wr_time.py /dev/uio6 guard    # logs transitions to wr-guard.log

`run_pipeline.sh` already starts this in its own tmux window, so you only run it by
hand outside that launcher. To watch the PPS live instead (edge count, missing edges,
rejects, and the walk rate in ppm):

    sudo python3 wr_pps_live.py /dev/uio6      # one line per second

Copy that log back to the PC and plot it with `plot_pps_log.py`, which needs numpy and
matplotlib and so does not run on the board.

## Read events on the WR timeline

    sudo python3 tclk_read.py /dev/uio4 --wr    # TCLK events with their WR {sec, ns}

`tclk_read.py` supports `--wr`. `aclk_read.py` prints only a raw hardware tick
timestamp (it has no `--wr` flag); WR-timestamped ACLK events come from the capture
path (`redis_publish.py --src aclk`, see redis.md / capture.md).

## Gotchas

- STRICT: pulling either WR line unlocks everything and sets the lost_lock
  sticky; timestamps read UNSYNC until you `arm` again. `clear` resets the sticky.
- A GT relock (recovery FSM or a `--gtreset`) stops rx_usrclk2 and resets the
  ACLK replica: re-run `arm` after any GT recovery.
- CELLS_LAST far from 10,000,000 means a flaky 10 MHz or PPS line: fix the
  wiring before trusting nanoseconds.
- PPS_REJECT counts edges the PL glitch filter threw away for arriving too early.
  Zero is the healthy reading. Nonzero means the PPS line is glitching, and each
  rejected edge is a whole second that would otherwise have been added silently,
  with every health flag still green. Check it whenever a capture ends and after
  any suspected upstream event; scope the PPS pin if it is climbing.
