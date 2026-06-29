#!/usr/bin/env python3
"""Long-run endurance monitor for the gigabit-ACLK GT optical link.

Samples the readout registers every --interval seconds, accumulates WRAP-CORRECTED stats
(the hardware counters wrap: EVENT_COUNT 32-bit, ERROR_COUNT 32-bit, disperr 14-bit,
recover 4-bit), prints a compact progress line every --report seconds, and on Ctrl-C
prints a full summary of the whole run.

    sudo python3 -u aclkgt_monitor.py /dev/uio4
    sudo python3 -u aclkgt_monitor.py /dev/uio4 --interval 1.0 --report 60 --log run.csv

Leave it running for an hour, come back, press Ctrl-C, read the summary. It only READS
registers (it does not pop events or reset the link), so it is safe to leave unattended.
Run aclkgt_read.py --gtctrl 0x00 first if you want to start from a fresh lock; the monitor
measures deltas from its own start, so the absolute counter values do not matter.

DEBUG word (0xA0): {rcv_aligned[31], byteali[30], rx_los[29], notintbl[28], disperr[27:14],
                    tx_fault[13], mod_abs[12], recover[11:8], commadet[7:0]}
"""
import mmap, os, struct, sys, time

# ---- args ----
_args = sys.argv[1:]
DEV = "/dev/uio4"
INTERVAL = 1.0      # seconds between samples
REPORT = 60.0       # seconds between live progress lines
LOGPATH = None
_i = 0
while _i < len(_args):
    a = _args[_i]
    if a == "--interval" and _i + 1 < len(_args):
        INTERVAL = float(_args[_i + 1]); _i += 2
    elif a == "--report" and _i + 1 < len(_args):
        REPORT = float(_args[_i + 1]); _i += 2
    elif a == "--log" and _i + 1 < len(_args):
        LOGPATH = _args[_i + 1]; _i += 2
    elif not a.startswith("--"):
        DEV = a; _i += 1
    else:
        _i += 1

STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = 0x00, 0x70, 0x80, 0x90, 0xA0
LOCK, FILTERED_COUNT = 0xC0, 0xE0
OFF = 0 if "uio" in DEV else 0x8000_0000

fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OFF)

def rd(o):
    return struct.unpack("<I", m[o:o + 4])[0]

def decode(d):
    return {
        "rcv_aligned": (d >> 31) & 1, "byteali": (d >> 30) & 1, "rx_los": (d >> 29) & 1,
        "notintbl": (d >> 28) & 1, "disperr": (d >> 14) & 0x3FFF, "tx_fault": (d >> 13) & 1,
        "mod_abs": (d >> 12) & 1, "recover": (d >> 8) & 0xF, "commadet": d & 0xFF,
    }

def fmt_dur(s):
    h = int(s // 3600); mn = int((s % 3600) // 60); sc = s % 60
    return "%dh %02dm %04.1fs" % (h, mn, sc)

def human(n):
    return "{:,}".format(int(n))

# ---- baseline ----
t0 = time.monotonic()
evt0 = rd(EVENT_COUNT); err0 = rd(ERROR_COUNT)
prev_evt = evt0; prev_err = err0
db0 = decode(rd(DEBUG)); prev_rec = db0["recover"]; prev_disperr = db0["disperr"]

tot_events = 0          # wrap-corrected decoded events over the run
tot_crc_err = 0         # wrap-corrected ERROR_COUNT delta (bad-CRC frames)
tot_recover = 0         # wrap-corrected recovery-FSM firings
samples = 0
aligned_hi = 0; byteali_hi = 0
disperr_moving = 0      # samples where the disparity counter advanced
notintbl_ever = 0; rxlos_ever = 0; txfault_ever = 0; modabs_ever = 0; overflow_ever = 0
min_rate = None; max_rate = 0.0
lock_losses = 0; prev_aligned = db0["rcv_aligned"]
cur_streak = 0.0; longest_streak = 0.0

logf = None
if LOGPATH:
    logf = open(LOGPATH, "w")
    logf.write("elapsed_s,evt_rate,aligned,byteali,recover_total,crc_total,disperr_moving,"
               "rx_los,tx_fault,mod_abs\n")

print("# monitoring %s every %.1fs (progress every %.0fs). Ctrl-C to stop + summarize."
      % (DEV, INTERVAL, REPORT))
print("# baseline: EVENT_COUNT=%s ERROR_COUNT=%s lock=%d" % (human(evt0), human(err0), rd(LOCK) & 1))
last_report = t0

try:
    while True:
        time.sleep(INTERVAL)
        now = time.monotonic()
        dt = now - (t0 if samples == 0 else last_sample_t)
        last_sample_t = now

        evt = rd(EVENT_COUNT); err = rd(ERROR_COUNT); db = decode(rd(DEBUG)); st = rd(STATUS)
        d_evt = (evt - prev_evt) & 0xFFFFFFFF
        d_err = (err - prev_err) & 0xFFFFFFFF
        d_rec = (db["recover"] - prev_rec) & 0xF
        prev_evt, prev_err, prev_rec = evt, err, db["recover"]

        tot_events += d_evt; tot_crc_err += d_err; tot_recover += d_rec
        samples += 1
        aligned_hi += db["rcv_aligned"]; byteali_hi += db["byteali"]
        dm = 1 if db["disperr"] != prev_disperr else 0
        disperr_moving += dm
        prev_disperr = db["disperr"]
        notintbl_ever |= db["notintbl"]; rxlos_ever |= db["rx_los"]
        txfault_ever |= db["tx_fault"]; modabs_ever |= db["mod_abs"]
        overflow_ever |= (st >> 1) & 1

        rate = d_evt / dt if dt > 0 else 0.0
        if min_rate is None or rate < min_rate:
            min_rate = rate
        if rate > max_rate:
            max_rate = rate

        if db["rcv_aligned"]:
            cur_streak += dt
            if cur_streak > longest_streak:
                longest_streak = cur_streak
        else:
            cur_streak = 0.0
        if prev_aligned == 1 and db["rcv_aligned"] == 0:
            lock_losses += 1
        prev_aligned = db["rcv_aligned"]

        if logf:
            logf.write("%.1f,%.0f,%d,%d,%d,%d,%d,%d,%d,%d\n" % (
                now - t0, rate, db["rcv_aligned"], db["byteali"], tot_recover, tot_crc_err,
                dm, db["rx_los"], db["tx_fault"], db["mod_abs"]))
            logf.flush()

        if now - last_report >= REPORT:
            el = now - t0
            print("[%s] events=%s avg=%s/s aligned=%.1f%% recover=%d crc_err=%s" % (
                fmt_dur(el), human(tot_events), human(tot_events / el if el > 0 else 0),
                100.0 * aligned_hi / samples, tot_recover, human(tot_crc_err)))
            last_report = now

except KeyboardInterrupt:
    pass

el = time.monotonic() - t0
if logf:
    logf.close()
print("")
print("=" * 60)
print("  ACLK GT optical-link endurance summary")
print("=" * 60)
print("  Duration:              %s  (%d samples @ %.1fs)" % (fmt_dur(el), samples, INTERVAL))
if samples == 0:
    print("  (no samples - stopped immediately)")
    sys.exit(0)
avg_rate = tot_events / el if el > 0 else 0
print("  --- Decode throughput ---")
print("  Total events decoded:  %s" % human(tot_events))
print("  Avg event rate:        %s /s" % human(avg_rate))
print("  Min / max sample rate: %s / %s /s" % (human(min_rate or 0), human(max_rate)))
print("  --- Link lock ---")
print("  rcv_aligned uptime:    %.3f %%  (%d / %d samples)" % (
    100.0 * aligned_hi / samples, aligned_hi, samples))
print("  byteali uptime:        %.3f %%" % (100.0 * byteali_hi / samples))
print("  Lock-loss events:      %d" % lock_losses)
print("  Longest locked streak: %s" % fmt_dur(longest_streak))
print("  --- Self-healing / errors ---")
print("  Recoveries (FSM):      %d" % tot_recover)
print("  CRC errors:            %s" % human(tot_crc_err))
print("  disperr active:        %.3f %% of samples" % (100.0 * disperr_moving / samples))
print("  notintbl seen:         %s" % ("YES" if notintbl_ever else "no"))
print("  FIFO overflow seen:    %s (expected: free-running gen floods the FIFO)" % (
    "yes" if overflow_ever else "no"))
print("  --- SFP sideband ---")
print("  rx_los asserted:       %s" % ("YES - lost light!" if rxlos_ever else "never"))
print("  tx_fault asserted:     %s" % ("YES" if txfault_ever else "never"))
print("  mod_abs asserted:      %s" % ("YES" if modabs_ever else "never"))
print("  --- Verdict ---")
up = 100.0 * aligned_hi / samples
if tot_events == 0:
    v = "LINK DOWN - no events decoded the entire run."
elif rxlos_ever:
    v = "LINK LOST LIGHT during the run (rx_los asserted) - check optics/laser."
elif up >= 99.0 and tot_recover <= 2:
    v = "LINK HEALTHY - decoded continuously with >=99%% lock and minimal recoveries."
elif up >= 95.0:
    v = ("LINK UP, EYE MARGINAL - %d self-heals over the run; the FSM is holding the link "
         "together. Consider an attenuator / cleaner optics to reduce recoveries." % tot_recover)
else:
    v = ("LINK UNSTABLE - only %.1f%% lock uptime, %d lock losses, %d recoveries. The eye is "
         "too marginal; the self-heal keeps it alive but it is not reliable." % (
             up, lock_losses, tot_recover))
print("  " + v)
print("=" * 60)
if LOGPATH:
    print("  per-sample CSV: %s" % LOGPATH)
