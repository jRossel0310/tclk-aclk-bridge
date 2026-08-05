# Grandmaster console runbook

Purpose: confirm at the source what `docs/wr-reference-findings.md` established from
downstream: that the grandmaster is free-running, that it rebooted at
2026-08-03 01:36:36 UTC, and whether its time is set by a hardcoded `setsec` at
boot. Everything here is read-only.

## Before you go

Finding it: follow the fibre out of WR-LEN `_1597`'s wr0 / SFP1, which is its slave
port. That link's `Cable rtt delay` of 1,338,201 ps puts the grandmaster about 137 m
of fibre away, so expect another room or another rack. If you land on a further
intermediate node instead, read `crtt:` from its own `stat` and keep going. Metres =
crtt_ps / 9800, one way, at roughly 4.9 ns per metre in fibre.

Then, before you walk over:

- Bring a UTC clock good to the second (phone on time.gov, or a second SSH window
  on the board running `date -u`).
- Turn console logging on before you connect, not after. PuTTY: Session > Logging >
  "All session output". screen: `screen -L -Logfile gm-console.log ...`. The command
  lines under "Getting a console" below do this for you.
- Serial settings are 115200 8N1, and WR-LEN-class nodes present a CP2102 USB bridge.
  Prove your cable and driver on a unit you can already reach first. Finding out at
  the rack wastes the whole trip.
- Note the wall-clock UTC time when you start, and photograph the unit's front and
  rear (the SMA connectors, and whether anything at all is cabled to CLK IN / PPS IN /
  10MHz REF), the labels, the serial number, and the rack. The photos are for the
  owner hunt. Photograph any GPS antenna lead or roof feed in the rack too, and any
  label on the incoming fibre; both are shortcuts to the owner.

## Getting a console

WR-LEN-class nodes expose their LM32 shell on the front mini-USB (B) connector marked
`USB UART`, behind a Silicon Labs CP2102 bridge (`VID_10C4`, `PID_EA60`). 115200 8N1,
no flow control. A WR switch may instead present a DB9 or RJ45 console, usually at the
same baud rate.

### Windows laptop (PuTTY 0.84 installed)

The CP210x Universal driver was installed on this laptop on 2026-08-04, so it should
just enumerate. Plug in, then find the port:

```powershell
Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match '\(COM\d+\)' } |
    Select-Object Name, Status
```

Take the one named `Silicon Labs CP210x USB to UART Bridge (COMn)`. On this laptop
`COM5` and `COM6` are Bluetooth virtual ports and are never the right answer.

Connect with session logging already on, which satisfies the logging requirement
above without touching the GUI:

```powershell
& 'C:\Program Files\PuTTY\putty.exe' -serial COM7 -sercfg 115200,8,n,1,N `
    -sessionlog "$env:USERPROFILE\Downloads\gm-console.log"
```

Substitute the real port number. Press Enter once the window opens. Confirm the log
file is actually growing before you rely on it.

If no CP210x port appears, the driver did not load:

```powershell
Get-CimInstance Win32_PnPEntity | Where-Object { $_.DeviceID -match 'VID_10C4' } |
    Select-Object Name, ConfigManagerErrorCode
```

`ConfigManagerErrorCode 28` means no driver. Reinstall from an elevated prompt with
`pnputil /add-driver silabser.inf /install` (the Silicon Labs Universal package ships
no installer .exe, only the INF).

### From a Linux host (the KR260, or a Linux laptop)

`cp210x` is in-kernel, so there is nothing to install:

```bash
sudo dmesg | tail -5          # "cp210x converter now attached to ttyUSB0"
ls /dev/ttyUSB*
sudo screen -L -Logfile gm-console.log /dev/ttyUSB0 115200
```

Exit screen with Ctrl-A, then K, then y. If another adapter is already attached, the
node may be `ttyUSB1`; `dmesg` names it. Note that `minicom` is not installed on the
board and the board cannot reach `ports.ubuntu.com`, so do not plan on installing
anything there.

### If pressing Enter gets you nothing

- Wrong port, or a charge-only mini-USB cable. Swap the cable first: it is the most
  common cause and costs nothing to rule out.
- A stale session still owns the port: `screen -ls` then `pkill screen`, or close the
  other PuTTY window.
- Do not go hunting for other baud rates before trying a known-good cable.

## Safety: the whole timing chain hangs off this unit

Type query commands only. Specifically, never type any of these, not even to see what
happens:

- `mode <anything>` (bare `mode` is safe; with an argument it reconfigures and
  stops PTP, dropping every downstream node)
- `time setsec ...` / `setsec` / anything that sets time
- `init erase`, `init add`, `init boot`
- `sfp erase/add/match`, any calibration command
- On a Linux (switch) console: nothing under `/etc/init.d/`, no reboots

`gui` is safe (Esc exits). If a command prompts for confirmation, answer no.

## Step 0: identify what you are talking to

Hit Enter. Two possibilities:

- `wrc#` or similar bare prompt: WRPC-class node (WR-LEN, SPEC, CUTE). Go to A.
- A Linux login or shell prompt: WR switch (WRS). Go to B.

## A. WRPC-class node (wrc# prompt)

Run, in order, capturing everything:

1. `ver` : firmware, hardware, serial. Identifies the unit for the owner hunt.
2. `mode` : the reading the whole trip is for. Expected values and their meanings:
   - `master` : free-running on its local oscillator. Confirms the finding
     outright: it disciplines nothing and nothing disciplines it.
   - `gm` : expects an external 10 MHz + PPS on the SMAs. Cross-check with your
     photos. `gm` with empty SMAs is a misconfiguration and confirms the finding
     just as well as `master` does, since it calls itself a grandmaster while
     free-running. `gm` with cabled SMAs is the surprising outcome and does not fit
     the downstream evidence, because a real GPS reference would have made the
     frequency correct. Do not force it into the story. Record everything, trace the
     cables to their source, and bring it back for a re-think.
   - `slave_wr0` / `slave_wr1` : this is not the grandmaster, just another link in
     the chain. Both units already checked reported `slave_wr0`, so this outcome is
     likely. Note which port is the slave, read `crtt:` from `stat` for the distance
     to the next hop, and follow that fibre upstream.
3. `time` , with the timing protocol below. Raw `sec:` value is the datum.
4. `init show` : the stored boot script. Looking for a `setsec` line, suspected
   value `1778025600`. Also note any `mode` line in it.
5. `stat` : link/servo state, one snapshot. Also carries `crtt:` (distance to the
   upstream node) and `temp:` / `temp-FPGA:`.
6. There is no separate `temp` command on this firmware: the board and FPGA
   temperatures are already in the `stat` line above. Record both, they matter for
   the crystal-retrace story.
7. `time` a SECOND time, at least 60 s after the first reading. Two spaced readings
   prove the clock is advancing at roughly 1 s/s, which rules out a stopped or
   externally-stepped clock. One reading cannot tell you that.

### The `time` reading protocol (the decisive number)

1. Type `time` but do not press Enter yet.
2. Watch your UTC clock; press Enter exactly as the seconds roll to a value you
   chose in advance (e.g. :00).
3. Write down both: the wall UTC second and the console's `sec:` value.

Interpretation, where `lag = wall_utc_epoch - sec_value`:

- lag = 7,695,396 s (= 89 d 1:36:36), within a few seconds:
  no reboot since 08-03 01:36:36Z. The frequency walk seen since then is the
  crystal retracing after that single reboot. Theory fully confirmed.
- lag noticeably smaller than 7,695,396 but still large (days to months):
  it rebooted again after 08-03. Boot instant = sec_value's date read as
  "time since 2026-05-06 00:00:00Z" added to that epoch. Every frequency number
  measured before that instant is stale.
- lag ~ 0 (console date is correct):
  someone fixed or replaced the time source since 2026-08-04 16:00Z. Find who;
  they are the owner you have been looking for.
- Any other constant: record it; `init show` will explain the epoch.

## B. WR switch (Linux console)

Same goals, different commands:

1. `uptime` : answers the reboot question directly. Expect a boot near
   2026-08-03 01:36Z if the theory holds.
2. `date -u` and the timing protocol above: is its time real or epoch-reset?
3. `wr_mon` : per-port sync state; which port is master, whether any port is a
   slave to something further upstream.
4. `hwinfo` or `/proc/cpuinfo` + labels: identity for the owner hunt.
5. `dmesg | head -30` : early boot messages, corroborates boot time.
6. Look but do not touch: `ls /wr/etc/` and `cat` any init/config file there;
   the setsec-equivalent would live in its startup config.

## What to bring home (the complete list)

1. `mode` (or wr_mon master/slave layout)
2. The paired reading: wall UTC second + `sec:` value (or `date -u` + `uptime`)
3. `init show` output (or startup config contents)
4. Photos: SMAs cabled or empty, labels, serial, rack location
5. `ver` / `hwinfo` identity
6. The console log file

If you turn up a human who owns the box, the one question worth asking is what
happened to this unit at 2026-08-03 01:36:36 UTC (20:36 CDT, Sunday evening). That is
the reboot instant derived downstream, and a yes confirms the whole chain of reasoning
from the other end. The follow-up is whether it has ever had a GPS or GPSDO reference
on its CLK IN / PPS I/O, and if not, whether it could.

Paste all of it back into the analysis session; the arithmetic takes minutes and
updates `docs/wr-reference-findings.md` from "confirmed from downstream" to
"confirmed at the source".

## After the visit: re-check the board

Merely reading the console changes nothing, but a trip to the rack often coincides
with someone reseating a fibre or power-cycling something. Any upstream event unlocks
the KR260's strict timebase, and it stays unlocked until re-armed, so every event
stamped in the meantime is UNSYNC-dropped.

```bash
cd ~/aclk_pipeline
sudo python3 wr_time.py /dev/uio6 status
```

Run it from `~/aclk_pipeline/`, not `~/wr_time.py`: the copy in the home directory is
a stale pre-glitch-filter version and prints no `PPS_REJECT` line. You want all three
lock bits at 1, `CELLS_LAST` at exactly 10000000, and `PPS_REJECT` unchanged from
before the trip. Re-arm only if a lock bit actually dropped.

Two things to check if the PL was reloaded while you were out (`PPS_COUNT` back near
zero gives it away):

- the `$07` drop mask is cleared and must be re-applied, or the capture rate goes
  from ~98 ev/s to ~820 ev/s
- the sticky FIFO overflow bit is cleared, so it is briefly a usable diagnostic again

## If the unit is reachable but you cannot stay

If you get thirty seconds at the console and nothing more, spend them on `mode` and
then the timed `time` reading. Those two settle discipline status and reboot history
respectively, and everything else in this runbook is elaboration on them.
