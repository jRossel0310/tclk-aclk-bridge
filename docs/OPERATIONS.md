# KR260 pipeline operations runbook

One page for the person running this system. Architecture background:
docs/PROJECT.md. Register-level details:
docs/generated/tclk-aclk-pipeline-hardware-interface-guide.pdf.

## 1. What you are operating

The KR260 runs a single bitstream (`aclk_pipeline`) that closes the whole
timing-link loop on one board: real Fermilab TCLK (3.3V biphase-mark) comes in
on a Pmod pin, a White Rabbit node's 10 MHz and PPS come in on two more Pmod
pins, and every decoded TCLK event is White-Rabbit-timestamped with an absolute
`{sec, ns}` UTC time and handed to the PS. The same event stream is re-encoded
as gigabit ACLK, sent out the SFP+ transceiver, looped back into the same board
over a physical fiber, decoded again against the same WR timeline, mirrored out
as ACLK-Lite (Manchester) on Pmod pin B10 as a scope probe, and published to the
PS on a second readout. On the board, one Python publisher per readout drains the
event FIFO and writes each event into local Redis Streams under the `{KR260}:`
namespace (RedisAdapter Protocol v1.0 key schema, base key braced for Redis
Cluster hash tagging).

## 2. One-time setup

### Accounts, network, Kerberos

The lab board is `aclk-timestamper.fnal.gov`. It authenticates with
GSSAPI/Kerberos, and Git Bash's `ssh`/`scp` cannot see the MIT Kerberos ticket,
so they fail to authenticate. Use PuTTY's `pscp` for file transfer (typically
`C:\Program Files\PuTTY\pscp.exe`; add PuTTY to PATH to call it bare). Getting
started with Kerberos on Windows is covered in
docs/Kerberos_Tutorial_V0_3.pdf.

Prerequisites: be on the lab network or VPN (to reach both the board and the
KDC), and hold a live ticket. Check and renew it:

```powershell
klist            # look for a krbtgt/FNAL.GOV entry that has not expired
kinit jrossel    # renew when it has expired (an expired ticket is the usual
                 # cause of GSSAPI / permission-denied failures)
```

Copy files with `pscp` (the `-scp` flag forces the SCP protocol so behavior is
predictable):

```powershell
# laptop -> board
pscp -scp "C:\path\to\localfile" ubuntu@aclk-timestamper.fnal.gov:/home/ubuntu/

# board -> laptop
pscp -scp ubuntu@aclk-timestamper.fnal.gov:/home/ubuntu/somefile.log "C:\Users\jacob\Downloads\"

# whole directory
pscp -scp -r "C:\path\to\folder" ubuntu@aclk-timestamper.fnal.gov:/home/ubuntu/
```

GUI alternative: WinSCP also supports GSSAPI/Kerberos (enable GSSAPI in its SSH
settings) and is what Fermilab's docs recommend for drag-and-drop transfers.

### Board prerequisites

The publishers write to a local Redis server. Redis >= 7.0 is no longer
required: the publisher sends explicit, complete `<ms>-<ns_within_ms>` stream
IDs (never a server-assigned `-*` sequence), and that syntax is accepted on
Redis 6 as well as Redis 7. Install Redis and the KR260 settings once, on the
board:

```bash
sudo apt update && sudo apt install -y redis-server python3-redis
redis-cli INFO server | grep redis_version    # informational only, any version works

# apply the KR260 Redis tuning (ephemeral streams, persistence off), then restart
cat redis-kr260.conf | sudo tee -a /etc/redis/redis.conf
sudo systemctl enable --now redis-server
sudo systemctl restart redis-server
redis-cli ping                                # -> PONG
sudo python3 -c "import redis; print(redis.__version__)"   # redis-py visible to root
```

Install the board-side Python dependencies:

```bash
sudo pip3 install -r requirements-board.txt
```

The `arm` step needs an NTP-disciplined system clock so it labels the correct
second. Confirm one of `chrony` or `systemd-timesyncd` is running and that
`timedatectl` reports `System clock synchronized: yes`.

## 3. Build the bitstream (PC, Vivado 2024.2)

The build wrapper now defaults to the pipeline design, so a bare build is all
you need:

```powershell
.\hw.ps1 build
```

This runs Vivado, packages the bitstream with bootgen, and hashes it in one
command. It prints `BIT`, `BIN`, `MD5`, and `SHA256`, and writes
`build-manifest.json`. Artifacts land repo-local under
`build\kria\aclk_pipeline\aclk_pipeline.runs\impl_1\`, and the loadable file is
`uart_echo_bd_wrapper.bit.bin`.

Path note: Vivado's IP Integrator (block design) breaks when the project path
contains spaces, and this repo lives under a "Summer 2026" directory. The build
task works around it by running Vivado from a space-free parent. If you still hit
a space-path error, relocate the build output to a space-free directory with
`-BuildRoot` (it is exported to the build TCL as the `KRIA_BUILD_DIR`
environment variable):

```powershell
.\hw.ps1 build -BuildRoot C:\kria-build
```

Copy the build to the board. The helper collects the `.bit.bin` plus every
board-side script and config the pipeline needs and copies them with `scp`:

```powershell
.\hw.ps1 deploy -Name aclk_pipeline -DeployHost ubuntu@aclk-timestamper.fnal.gov
```

It copies: `uart_echo_bd_wrapper.bit.bin`, `tclk_read.py`, `aclk_read.py`,
`wr_time.py`, `tclk_filter.py`, `readout_common.py`, `redis_sink.py`,
`redis_publish.py`, `stats_log.py`, `stats_report.py`, `stream_archive.py`,
`run_pipeline.sh`, `requirements-board.txt`, `redis-kr260.conf`,
`aclk_pipeline.dts`, and `capture.md`, all to `~` on the board.

Note: `hw.ps1 deploy` uses plain `scp`, which will **not** authenticate to
`aclk-timestamper.fnal.gov` (the GSSAPI board). For the lab board, copy those
files with `pscp` (Section 2) instead. The deploy helper works for a board
reachable by ordinary SSH.

Verify the load matches your build: compare the board-side `md5sum` of the
`.bit.bin` against the `MD5` line from `hw.ps1 build` (also in
`build-manifest.json`). A mismatch means a stale copy on the board.

## 4. Load on the board

The UIO + device-tree-overlay path is required: the overlay creates the
`/dev/uioN` nodes the readers mmap and releases PL reset. Compile the overlay
from `aclk_pipeline.dts`, unload any current app, then program the PL with the
overlay attached:

```bash
md5sum ~/uart_echo_bd_wrapper.bit.bin     # must equal the PC-side MD5
dtc -@ -O dtb -o aclk_pipeline.dtbo aclk_pipeline.dts
sudo xmutil unloadapp
sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o aclk_pipeline.dtbo
```

- The `-o <overlay>.dtbo` form is what creates the UIO devices and releases
  reset. Do NOT use `-f Full`: it programs the PL but creates no UIO device and
  does not release reset, so every AXI access bus-errors.
- A cosmetic `OF: overlay: WARNING: memory leak will occur ...` on load is
  harmless.
- `shell.json` (lives in `deploy/` in the repo, not part of the deploy set copied
  to the board) is the xmutil accelerator-slot descriptor; the fpgautil path
  above does not need it.

Three UIO nodes appear: `tclk_readout` @ 0x8000_0000, `aclk_readout` @
0x8001_0000, and `wr_timebase` @ 0x8002_0000. Map their `/dev/uioN` indices
(they are NOT guaranteed to be a fixed number) with:

```bash
grep . /sys/class/uio/uio*/name           # lists tclk_readout / aclk_readout / wr_timebase
```

Throughout the rest of this runbook the examples use `/dev/uio4` (tclk),
`/dev/uio5` (aclk), `/dev/uio6` (wr). Substitute the indices this command
reports on your board.

## 5. Wiring

All I/O is on PMOD1, LVCMOS33. Wire by **package pin** (the connector-position
numbers are ambiguous across sources); confirm against the carrier silkscreen.

| Package pin | PMOD1 pos | Signal | Direction | Purpose |
|-------------|-----------|--------|-----------|---------|
| H12 | 1 | `tclk` | in | TCLK biphase-mark input, ~10 MHz, 3.3V push-pull |
| B10 | 2 | `aclk_lite_out` | out | ACLK-Lite Manchester mirror (scope probe) |
| E10 | 3 | `wr_clk10` | in | White Rabbit 10 MHz reference, 3.3V CMOS |
| E12 | 4 | `wr_pps` | in | White Rabbit PPS, 3.3V CMOS |

The TCLK front end and the WR source must drive **push-pull 3.3V CMOS**. The
carrier's auto-direction level translators misbehave with open-drain sources.
Tie signal grounds to a Pmod GND pin. The PPS must be phase-aligned to the
10 MHz (a real WR node, or the replica generator, does this by construction).

The SFP+ path is a physical fiber loopback: patch the SFP+ TX port back into the
same module's RX port with a fiber jumper.

**WR PPS width requirement:** the PPS pulse must be **at least ~100 ns wide**. A
real WR node's native 10 ns PPS is invisible to the 40 MHz sampler in the PL, so
the timebase never arms. Widen the pulse at the source (or with a pulse
stretcher) before expecting a lock.

## 6. Run a capture

The whole capture runs unattended in a detached `tmux` session that survives an
SSH disconnect. Nothing publishes until the WR timebase is armed and locked, so
arm it first.

### Bring-up (once per load)

Work from the directory holding the deployed scripts (for example
`~/aclk_pipeline`; move the deployed files there if you keep them organized that
way):

```bash
grep . /sys/class/uio/uio*/name          # note the tclk / aclk / wr indices
timedatectl                              # System clock synchronized: yes
sudo python3 wr_time.py /dev/uio6 status # expect pps_alive=1 clk10_alive=1, CELLS_LAST ~= 10000000
sudo python3 wr_time.py /dev/uio6 arm    # arms floor(now)+1 for the next PPS
sudo python3 wr_time.py /dev/uio6 status # want locked_tclk=1 locked_aclk=1 locked_mon=1, |HW-system| < 0.5 s
redis-cli ping                           # PONG
```

### Launch (survives SSH disconnect)

```bash
sudo ./run_pipeline.sh                   # defaults: uio4 tclk, uio5 aclk, uio6 wr
# or pass indices:  sudo ./run_pipeline.sh /dev/uio4 /dev/uio5 /dev/uio6
```

Pre-flight refuses to launch unless Redis answers `PONG` and the WR timebase is
fully locked (an unlocked timebase stamps every event UNSYNC and they would all
be dropped, wasting the run). Override with `FORCE=1` only if you deliberately
want to capture while unlocked.

The launcher opens four `tmux` windows: `tclk` and `aclk` publishers, a `wr`
window running `wr_time.py guard` (which auto-re-arms the strict timebase after
any WR blip so a glitch costs seconds, not the run), and an `archive` window
running `stream_archive.py` (daily CSVs of every published event). Two
environment variables tune the launch:

- `DROP` (default `07`): PL drop-mask event codes, hex, comma-separated. The
  default drops $07, the 720 Hz flood. `DROP=""` keeps every code. The mask
  register persists across launches (it clears only on PL reload).
- `ARCHIVE` (default `1`): also run `stream_archive.py`. It writes about
  260 MB/day/source and is needed for supercycle analysis of runs longer than
  the ~2.8 h Redis stream retention. Set `ARCHIVE=""` to disable.

Spot-check while it runs:

```bash
sudo tmux attach -t kr260                # Ctrl-b d to detach
redis-cli XLEN '{KR260}:tclk'            # climbs
tail -f stats-tclk.jsonl                 # one JSON line per snapshot (~60 s)
```

### Stop cleanly (writes a final post-flush snapshot)

```bash
sudo tmux send-keys -t kr260:tclk C-c    # Ctrl-C makes each publisher write its FINAL snapshot
sudo tmux send-keys -t kr260:aclk C-c
sudo tmux kill-session -t kr260
```

If a publisher dies uncleanly the report still works off the last periodic
snapshot; you lose up to the last interval (~60 s) of counts.

## 7. Monitor and verify

### Error check (on the board)

```bash
sudo python3 stats_report.py stats-tclk.jsonl stats-aclk.jsonl
```

Per source it prints `decoded` (good events the PL enqueued), `published`,
failed CRCs, nulls/filtered, `missed` at the hardware (FIFO overflow) and at the
publisher (queue + redis drops), reconnects, WR-lock health, and an overflow
cross-check. It also prints an `undelivered` count (events still queued at stop,
for example if Redis was down) and a `ledger check` line: `decoded` should equal
`published + missed + queued + unsync`. A clean run reconciles with `missed`
near zero. If you re-run into the same log the snapshots append; the report
detects the restart and reconciles only the most recent run, so delete or rename
the log between runs to keep them separate.

### Redis liveness and stream checks

```bash
redis-cli XLEN '{KR260}:tclk'                     # climbs while publishing
redis-cli XREVRANGE '{KR260}:tclk' + - COUNT 3    # newest 3 events (event-time ordered)
redis-cli GET '{KR260}:status'                    # 1 while a publisher is alive (sticky, see below)
redis-cli TTL '{KR260}:watchdog'                  # counts down from ~30 while alive
```

`{KR260}:watchdog` is the **authoritative** liveness signal: it is a TTL key
refreshed every ~10 s that expires within ~30 s if a publisher dies.
`{KR260}:status` is sticky (set to 1 on connect, never cleared on stop), so do not
trust it alone. If `XLEN` stays 0, the WR timebase is almost certainly not
locked (all events are UNSYNC and dropped): re-check
`sudo python3 wr_time.py /dev/uio6 status`.

The publisher's own 1 Hz stats line reports
`drained / published / queued / queue_dropped / redis_dropped / reconnects`. A
rising `queue_dropped` or `redis_dropped` means Redis is not keeping up; if
`published` stays 0 while `reconnects` climbs, Redis is unreachable or redis-py
is not visible under `sudo` (see Section 9).

## 8. Get the data out

### Redis key schema (base key `{KR260}`)

The publisher writes three things per event, matching the Fermilab
RedisAdapter Protocol v1.0 convention:

- `XADD {KR260}:<src>` : the time-ordered event feed (`{KR260}:tclk`,
  `{KR260}:aclk`). The **entry ID is the event's RA_Time** (nanoseconds since
  the Unix epoch) encoded as `<ms>-<ns_within_ms>`, guarded so a duplicate or
  backward RA_Time is bumped to the previous ID + 1 ns rather than making
  `XADD` error. Fields: a mandatory `_` (the little-endian `<IIIHB>`
  RedisAdapter primary payload: sec, ns, data, event, flags) plus the readable
  string extras `sec, ns, event, data, is_tclk, has_data, src`. There is no
  per-entry `utc` field here; derive it from `sec`/`ns` (building it per event
  measurably cost sink throughput).
- `HSET {KR260}:event:<src>:0x<CODE>` : a per-event-code index holding that code's
  latest `{sec, ns, utc, data}`.
- `HINCRBY {KR260}:event:<src>:0x<CODE> count` : a running per-code count.

Inspect a single code's latest value and count directly:

```bash
redis-cli HGETALL '{KR260}:event:tclk:0x1D'   # latest event for that code + count
```

Streams are in-memory only (persistence is off) and capped, so they hold roughly
the last ~2.8 h. For anything longer, use the on-disk archive.

### Pull the archived CSVs and plot (on the PC)

With `ARCHIVE=1` the capture writes daily CSVs on the board named
`events-<src>-YYYYMMDD.csv` (schema `id,sec,ns,event,data`). Pull the CSVs and
the stats logs to the PC (`pscp` on the lab network, `scp` otherwise):

```powershell
pscp -scp "ubuntu@aclk-timestamper.fnal.gov:/home/ubuntu/aclk_pipeline/events-*.csv" .
pscp -scp "ubuntu@aclk-timestamper.fnal.gov:/home/ubuntu/aclk_pipeline/stats-*.jsonl" .
```

Plot the capture health (event rate, CRC-error rate, cumulative missed,
WR-lock/overflow) from the stats logs:

```powershell
python plot_stats.py stats-tclk.jsonl stats-aclk.jsonl   # -> plot-tclk.png, plot-aclk.png
```

Fold a single TCLK event code onto the supercycle. `supercycle_plot.py` anchors
every event to the preceding $00 (the supercycle reset), folds all supercycles
onto one time axis, and renders a distribution histogram and a per-cycle raster
as SVGs:

```powershell
python supercycle_plot.py events-tclk-*.csv --target 1E --ref 0C,BA
python supercycle_plot.py tail.csv --target 1F --theme poster -o bes.svg
```

## 9. Known failure modes

- **WR PPS narrower than ~100 ns.** A pulse below ~100 ns is invisible to the
  40 MHz sampler, so the timebase never arms and every event stays UNSYNC. Fix
  it at the source or stretch the pulse; do not expect a lock with a raw 10 ns
  WR PPS.
- **Never write PL registers via a Python mmap slice assignment.** A slice write
  such as `m[o:o+4] = ...` can compile to a glibc `memcpy` that issues two AXI
  stores, and a double store to the `POP` register double-pops the event FIFO,
  silently discarding the next buffered event. Always use the single-store
  `pulse()` / write helpers in `readout_common.py`; keep any sensitive register
  off slice-assignment.
- **Reader FIFO overflow.** Symptom: `stats_report` shows `missed > 0` and the
  `STATUS` overflow bit set. Cause is a stalled reader, not the hardware. The
  `run_pipeline.sh` `until` restart loops are the mitigation. Restart the
  publisher window (or relaunch the session); do not power-cycle the board
  first.
- **Redis unreachable or not running.** If `published` stays 0 with `reconnects`
  climbing (backoff, not a busy spin), check `redis-cli ping` and that
  redis-py is installed and visible to root. Redis >= 7.0 is no longer
  required: the publisher's explicit `<ms>-<ns_within_ms>` stream IDs are
  accepted on Redis 6 too.
- **AXI reads that alias every 16 bytes.** The readout registers are spaced 16
  bytes apart on purpose: on this LPD path any offset that is not 16-byte
  aligned reads back 0 (the historical "register reads 0" trap). Keep any new
  register on that 16-byte grid.
- **Timebase unlocks on any WR dropout.** The strict WR timebase unlocks
  permanently on any PPS or 10 MHz blip (and on a GT relock) and sets a sticky
  lost-lock flag; every event then stamps UNSYNC. The `wr_time.py guard` window
  in `run_pipeline.sh` re-arms it automatically. A capture taken while unlocked
  is invalid, so always check `stats_report` (and the WR-lock health line)
  before trusting the data.

## 10. When something else breaks

- **WR timebase health:** `sudo python3 wr_time.py /dev/uio6 status` is the first
  stop. `pps_alive` / `clk10_alive` = 0 means a dead WR line;
  `CELLS_LAST` far from 10,000,000 means a flaky 10 MHz or PPS (fix the wiring
  before trusting nanoseconds); `lost_lock=1` means a reference dropped since the
  last arm (re-`arm`, or `clear` to reset the sticky). Re-`arm` after any GT
  recovery, which unlocks the ACLK replica.
- **Watch the raw event stream:** the console readers read the same FIFOs as the
  publishers (do NOT run both on the same UIO node at once, both pop the FIFO).
  `sudo python3 -u tclk_read.py /dev/uio4 --wr` prints each TCLK event with its
  WR timestamp. `sudo python3 -u aclk_read.py /dev/uio5` prints each ACLK-Lite
  event with a raw hardware tick timestamp only; `aclk_read.py` has no `--wr`
  flag, so passing one is silently ignored. For WR-timestamped ACLK events, use
  the normal capture path (`redis_publish.py`, Section 8) instead. Either way a
  `[stats]` line each second shows line activity so you can tell "no signal on
  the pin" from "signal present, decoder not locking".
- **Regression after RTL edits:** re-run the cocotb testbench suite before
  rebuilding hardware. `.\sim.ps1 list` shows the testbenches (the pipeline chain
  lives in `tb/aclk_pipeline_chain`), and `.\sim.ps1 run -Module <tb>` runs one
  (Icarus by default).
- **Software regression:** the board-side Python has unit tests. Run
  `pytest deploy` on the PC to exercise the readout register map, the Redis
  sink/publisher, the stats reconciliation, and the plotting.
- **Register-level detail:** the full register map (both readouts, the WR monitor
  slave, the GT-health DEBUG word, the `GT_CTRL` bits) is in
  docs/generated/tclk-aclk-pipeline-hardware-interface-guide.pdf. Section 10 of
  that guide has a full status/error/recovery table.
