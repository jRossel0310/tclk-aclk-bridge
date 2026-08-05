# Streaming to the lab Redis over eth0 (fresh boot to publishing)

Target, per the `keyspacehandoff.md` deployment document: Redis on host **`bidaqt-tclk`,
`10.200.12.100`**, TCP **6379**, on the `accel-controls` network. Base key **`{TCLK}`**,
which that deployment reserves for this decoder and already has 513 EPICS PVs waiting on
it (`EPICS_REDIS_TCLK:RT:*`).

**As found on 2026-08-03: the board reaches it over `eth1`, already configured, no
network setup needed.** eth1 sits at `10.200.12.148/22`, which puts `10.200.12.100`
on-link (the /22 spans `10.200.12.0`-`10.200.15.255`), with the default route via
`10.200.12.1`. `eth0` shows `NO-CARRIER` and is unused; the `10.200.8.97` address from
an earlier plan is unreachable from this network. Steps 1 and 2 below exist to
re-establish that if the wiring ever changes, but on this board they are a five-second
confirmation, not work.

Everything through Step 5 is safe with no bitstream loaded, so prove the network and the
Redis first and only then bring up the hardware. Key schema: redis.md. Full pipeline
runbook: ../docs/OPERATIONS.md.

> **You are on the UART console, not SSH.** That removes the usual hazard here: a bad
> network change cannot lock you out. Reconfigure eth0 freely.

---

## 0. Copy the files off the USB stick

The stick has a folder `kr260-tclk-redis-<date>`. Mount it and copy:

```bash
lsblk                                   # find the stick, e.g. sda1
sudo mkdir -p /mnt/usb
sudo mount /dev/sda1 /mnt/usb
ls /mnt/usb/kr260-tclk-redis-*/

mkdir -p ~/aclk_pipeline
cp -r /mnt/usb/kr260-tclk-redis-*/aclk_pipeline/. ~/aclk_pipeline/
cp /mnt/usb/kr260-tclk-redis-*/uart_echo_bd_wrapper.bit.bin ~/
```

Verify nothing was corrupted in transit before you trust any of it:

```bash
cd /mnt/usb/kr260-tclk-redis-*/ && sha256sum -c MANIFEST.sha256; cd ~
sudo umount /mnt/usb
```

Every line must say `OK`. Then make the launcher executable (FAT32 loses the bit):

```bash
chmod +x ~/aclk_pipeline/run_pipeline.sh
```

## 1. Confirm the link is up

```bash
ip -br addr                                   # which interface has a 10.200.x.x address?
ip route
```

On this board that shows `eth1  UP  10.200.12.148/22` and a default via `10.200.12.1`,
which is all you need: skip to Step 3. `eth0` reads `DOWN / NO-CARRIER` and is not in
use.

If no interface has a `10.200.x.x` address, find which port has a link before touching
any config, since `NO-CARRIER` is a physical problem no netplan file can fix:

```bash
ip -br link
sudo ethtool eth0 | grep -E "Speed|Duplex|Link detected"
sudo dmesg -w                                 # then unplug/replug to identify the jack
```

The KR260 has four RJ45s and the silkscreen numbering does not reliably match the
kernel's `ethN`, so `dmesg -w` while replugging is the only certain identification. If a
switch port needs enabling, the admin will want the MAC from `ip -br link`.

## 2. Get an address on the accel-controls network

**Look before you write anything.** eth0 may already be configured from earlier work, in
which case the whole step is done and adding a file would override it:

```bash
ip -br addr show eth0                     # already has a 10.200.x.x address?
ls -la /etc/netplan/                      # what config already exists
grep -rn "eth0" /etc/netplan/ 2>/dev/null # and what it says about this port
```

If eth0 already holds a `10.200.x.x` address, skip straight to Step 3 and change
nothing. Netplan applies its files in lexical order, so a new `99-` file wins over
anything already there; do not add one unless eth0 is genuinely unconfigured.

**Otherwise try DHCP.** Most control networks hand out addresses, and this avoids
guessing a subnet you do not own:

```bash
sudo tee /etc/netplan/99-tclk-redis.yaml >/dev/null <<'EOF'
network:
  version: 2
  renderer: networkd
  ethernets:
    eth0:
      dhcp4: yes
      dhcp6: no
EOF
sudo chmod 600 /etc/netplan/99-tclk-redis.yaml
sudo netplan apply
ip -br addr show eth0
```

If that yields an address in `10.200.x.x`, skip to Step 3.

**If DHCP gives nothing** you will see either no address at all or a `169.254.x.x`
link-local, which means no DHCP server answered. Check you are not just being impatient:

```bash
ip -br addr show eth0
journalctl -u systemd-networkd -n 30 --no-pager   # DHCP offers, or silence
```

Silence here is the one case that needs the Redis admin, and you need three facts that
are NOT in the handoff document: the address to use, the prefix length, and whether a
gateway is required to reach `10.200.12.0` from wherever the board sits. Fill them in:

```bash
sudo tee /etc/netplan/99-tclk-redis.yaml >/dev/null <<'EOF'
network:
  version: 2
  renderer: networkd
  ethernets:
    eth0:
      dhcp4: no
      dhcp6: no
      addresses: [10.200.12.NNN/24]     # <-- address + prefix from the admin
      # Only if the board is NOT on the same subnet as 10.200.12.100. A default
      # route here would compete with any other interface's; prefer this scoped form:
      # routes:
      #   - to: 10.200.12.0/24
      #     via: 10.200.12.1
EOF
sudo chmod 600 /etc/netplan/99-tclk-redis.yaml
sudo netplan apply
ip -br addr show eth0
```

Do not invent an address. Two hosts answering to the same IP on a controls network is a
problem that outlives your run and is somebody else's outage.

## 3. Prove the path to the Redis host

```bash
ping -c 3 10.200.12.100
ip route get 10.200.12.100         # must leave via eth0
nc -vz 10.200.12.100 6379          # TCP 6379 open
getent hosts bidaqt-tclk           # does DNS resolve the name? (optional, IP works)
```

`ip route get` is the check that catches traffic leaving the wrong interface, which
looks identical to a firewall drop from the ping alone.

Then Redis itself:

```bash
redis-cli -h 10.200.12.100 ping                              # -> PONG
redis-cli -h 10.200.12.100 INFO server | grep redis_version  # want >= 7.4 (HEXPIRE)
```

See what is already there, so you know the neighbours before you write:

```bash
redis-cli -h 10.200.12.100 HGETALL '{TCLK-MULTICAST}:watchdog'   # their producer, alive?
redis-cli -h 10.200.12.100 EXISTS '{TCLK}:STREAM'                # expect 0: nobody home yet
```

If the server needs auth, export it once and everything below inherits it (keeps the
password out of `ps` and out of your shell history):

```bash
read -rs REDIS_PASSWORD && export REDIS_PASSWORD
export REDISCLI_AUTH="$REDIS_PASSWORD"
read -r REDIS_USERNAME && export REDIS_USERNAME   # only if they use ACL users
```

## 4. Install the Python dependency

Ubuntu ships `python3-redis` 4.3.4, which **predates HEXPIRE**. It must be upgraded or
the watchdog cannot work:

```bash
cd ~/aclk_pipeline
sudo pip3 install -U redis                       # if the board has PyPI access
sudo pip3 install --no-index --find-links ~/wheels --upgrade --no-deps redis   # offline
python3      -c "import redis; print(redis.__version__, hasattr(redis.Redis,'hexpire'))"
sudo python3 -c "import redis; print(redis.__version__, hasattr(redis.Redis,'hexpire'))"
```

Both must print a 5.1-or-later version and `True`. Check **both** contexts: the publisher
runs under sudo to mmap `/dev/uio*`, while the smoke test and archiver do not, and a
`pip install --user` is invisible to `sudo python3`. If they disagree you get a green
smoke test and a publisher that fails.

Two traps when installing offline:

- `--force-reinstall` also forces reinstalling dependencies, so it demands
  `async-timeout` (required only on Python < 3.11.3) from your local wheel directory and
  fails if it is not there. Use `--no-deps` instead.
- `--no-deps` is safe here: only `redis/asyncio/connection.py` imports `async_timeout`,
  `redis/__init__.py` does not pull in `redis.asyncio`, and nothing in this repo uses the
  async client. If `import redis` does complain, `sudo apt install -y python3-async-timeout`.

When staging wheels on a PC, resolve them for the **board's** interpreter, not the PC's,
or conditional dependencies get silently omitted:

```bash
pip download "redis>=5.1" async-timeout -d wheels/ \
    --python-version 3.10.12 --only-binary=:all:
```

## 5. Smoke-test the write path (still no hardware)

```bash
cd ~/aclk_pipeline
python3 redis_smoketest.py --redis-host 10.200.12.100 --base KR260-SMOKETEST
```

This runs the real producer path against their server under a throwaway base key, reads
it back with the independent decoders, checks the watchdog field really received a TTL,
then deletes exactly the keys it created. Expect
`PASS: 10.200.12.100:6379 is ready for redis_publish.py`.

It refuses `TCLK` and `TCLK-MULTICAST` without `--allow-production`, because it deletes
what it writes and that server is shared.

Reading a `FAIL`:

| Message | Meaning |
| --- | --- |
| `cannot reach redis` | Step 3 lied, or auth is required and not exported |
| `this redis-py has no HEXPIRE` | client too old: `sudo pip3 install -U redis` |
| `watchdog field has no TTL` / `last_error` mentions HEXPIRE | the **server** is older than 7.4; the admin has to decide how liveness works |
| `STREAM read back ...` | payload width or key mismatch, stop and report it |

Also run the unit suite here, because two of its tests only execute where a real
`redis-server` binary exists and they are the ones that prove HEXPIRE end to end:

```bash
cd ~/aclk_pipeline && python3 -m pytest . -q     # expect 128 passed, 0 skipped
```

Two skips instead of zero means no local `redis-server` binary; that is fine for
publishing to the lab, it just means those two never ran anywhere.

## 6. Bring up the hardware

Detail in ../docs/OPERATIONS.md sections 4 and 6.

```bash
cd ~
md5sum uart_echo_bd_wrapper.bit.bin      # compare against MANIFEST.sha256's sibling MD5
dtc -@ -O dtb -o aclk_pipeline.dtbo ~/aclk_pipeline/aclk_pipeline.dts
sudo xmutil unloadapp
sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o aclk_pipeline.dtbo
grep . /sys/class/uio/uio*/name          # note the tclk_readout / wr_timebase indices
```

Use the `-o <overlay>.dtbo` form. `-f Full` programs the PL but creates no UIO node and
does not release reset, so every AXI access bus-errors.

### Wait for NTP before arming: this board has a dead RTC

`timedatectl` reports `RTC time: Thu 1970-01-01`, so **every boot starts with the system
clock about 58 days wrong** until chrony reaches the network and steps it:

```
Aug 3 01:46:01  System clock was stepped by 5047494.677722 seconds
Aug 3 12:31:19  System clock was stepped by 5086002.156533 seconds
```

`wr_time.py arm` reads that clock to label the second, so arming inside that window
labels the timebase weeks off, and it is invisible afterwards because `status` compares
HW against the same wrong clock and both agree. Block until the clock is real:

```bash
chronyc waitsync 60 0.1        # wait up to ~10 min for the offset to fall under 0.1 s
timedatectl                    # confirm: System clock synchronized: yes
chronyc tracking               # confirm a real Reference ID, not 00000000
```

`arm` now refuses outright if those are not satisfied, so a forgotten wait gives a clear
refusal instead of hours of silently wrong timestamps. `--force` overrides deliberately.

Arm the timebase (substitute your wr index):

```bash
sudo python3 ~/aclk_pipeline/wr_time.py /dev/uio6 status   # want pps_alive=1 clk10_alive=1
sudo python3 ~/aclk_pipeline/wr_time.py /dev/uio6 arm --verify-after 90
sudo python3 ~/aclk_pipeline/wr_time.py /dev/uio6 status   # want locked_tclk=1 locked_aclk=1
```

`--verify-after 90` re-reads HW against the system clock 90 s later. That catches an NTP
correction landing just after the arm, which is exactly the failure that put 4 s into the
2026-08-03 capture while looking clean at the time.

Two lines in `status` matter most:

- `HW - system clock = ...` should be well under 1 s. Close to a whole number of seconds
  means the arm labelled the wrong second: re-arm.
- `PPS_REJECT=` counts spurious PPS edges the PL discarded. Nonzero means the WR PPS line
  is glitching, and each one would previously have added a whole second silently.

Nothing publishes until this locks: unlocked stamps every event UNSYNC and the publisher
drops it by design.

## 7. First publish, in the foreground

Watch the first minute by hand before committing to the unattended launcher:

```bash
cd ~/aclk_pipeline
sudo python3 redis_publish.py /dev/uio4 --src tclk \
    --base TCLK --redis-host 10.200.12.100 --drop 07
```

Look for `published` climbing, `queued` near zero, and `queue_dropped` /
`redis_dropped` / `last_error` all quiet. Ctrl-C to stop.

Then the unattended run:

```bash
sudo env REDIS_HOST=10.200.12.100 BASE=TCLK bash run_pipeline.sh
```

`sudo env VAR=...`, never `export VAR=...; sudo ...`: sudo resets the environment, so
the exported form is silently ignored and you would publish to the board's local Redis
while believing it went to the lab. The launch banner prints the target actually used.
Check it. With auth, add `REDIS_PASSWORD="$REDIS_PASSWORD"` to the same `env` list.

## 8. Confirm it is live

```bash
redis-cli -h 10.200.12.100 XLEN '{TCLK}:STREAM'                        # climbs
redis-cli -h 10.200.12.100 HTTL '{TCLK}:watchdog' FIELDS 1 kr260-tclk  # ~1, counting down
```

Payloads are binary, so decode rather than reading escapes:

```bash
cd ~/aclk_pipeline && python3 -c "
import redis, ra_consumer as ra
c = redis.Redis(host='10.200.12.100')
print('codes:', [ra.decode_event_id(f) for _, f in c.xrange(ra.stream_key('TCLK'))][-10:])
print('0x1D t:', ra.decode_int64(c.xrevrange(ra.ts_key('TCLK', 0x1D), count=1)[0][1]))
"
```

The admin's side of the check is that `EPICS_REDIS_TCLK:RT:STREAM` starts updating. If
our keys fill but his PVs stay dead, the problem is on the IOC side, not ours.

## What persists across a reboot

| Survives | Repeats every boot |
| --- | --- |
| the netplan file and the eth0 address | the bitstream load (Step 6) |
| the installed redis-py | the WR arm (Step 6) |
| the files in `~/aclk_pipeline` | starting the publisher (Step 7) |

After the first reboot, confirm the network came back before anything else:

```bash
ip -br addr show eth0 && ping -c 1 10.200.12.100
```

## Known caveat: stamp-clock frequency offset

The WR PPS source is free-running, so it sits at some offset from GPS (see
gps_calibrate.py, which measures it). The PL counts PPS edges, so a HW second is a PPS
period by construction and that offset accumulates against real time. The offset
belongs to the source's current boot rather than to the installation, so measure it
per run instead of assuming a constant. Published timestamps are the raw hardware
stamps, uncorrected.

`gps_calibrate.py` measures the offset but is a *duration* correction for offline CSV
analysis; it cannot fix an absolute epoch and is deliberately not in the publish path.
Tell the admin the number before a long run, because it is his consumers that will see
our RT timestamps walk away from the multicast ones. The real fix is in hardware: the
PPS source is a free-running oscillator, not slaved to a grandmaster.
