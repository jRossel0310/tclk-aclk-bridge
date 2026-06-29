#!/usr/bin/env python3
"""Sweep the GT TX driver (txdiff / txpost / txpre) via GT_CTRL and score each setting
from the RX health counters, to hunt a TX eye the SFP optical self-loop can lock on.

WHAT THIS IS (and is not): the GT/SFP serial path lives entirely in the PL and runs at
62.5 MHz, so software cannot send/receive raw bits over the fiber. This script only does
what the PS CAN do over AXI: write the GT's runtime TX-driver knobs (GT_CTRL[23:9]) and
read the readout's health word (0xA0) + EVENT_COUNT (0x70). For each setting it pulses
the RX datapath re-init (GT_CTRL[8], which also clears the sticky bits and re-aligns),
dwells, then measures whether the RX byte-aligns / the decoder locks / events decode and
whether the 8b10b disparity-error counter is still moving.

Run on the board (at your best optical attenuation):
    sudo python3 -u aclkgt_sweep.py /dev/uio4            # sweep txdiff (7 points)
    sudo python3 -u aclkgt_sweep.py /dev/uio4 --post     # also sweep txpostcursor
    sudo python3 -u aclkgt_sweep.py /dev/uio4 --pre      # also sweep txprecursor
    sudo python3 -u aclkgt_sweep.py /dev/uio4 --base 0x01  # sweep with rxpolarity=1
    sudo python3 -u aclkgt_sweep.py /dev/uio4 --dwell 2.0  # longer dwell per setting

Reading the result: the WIN condition is aligned_frac > 0 (decoder locked) or evt_delta > 0
(events actually decoded), ideally with disperr_active == 0 (errors stopped). If NO setting
gets there, the TX drive is not the bottleneck and the problem is elsewhere in the optical
path (fiber/connector/SFP) or the RX side.
"""
import mmap, os, struct, sys, time

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# ---- args ----
_args = sys.argv[1:]
DEV = "/dev/uio4"
DWELL = 1.5
BASE = 0x00
SWEEP_POST = False
SWEEP_PRE = False
_i = 0
while _i < len(_args):
    a = _args[_i]
    if a == "--dwell" and _i + 1 < len(_args):
        DWELL = float(_args[_i + 1]); _i += 2
    elif a == "--base" and _i + 1 < len(_args):
        BASE = int(_args[_i + 1], 0) & 0xFFFFFFFF; _i += 2
    elif a == "--post":
        SWEEP_POST = True; _i += 1
    elif a == "--pre":
        SWEEP_PRE = True; _i += 1
    elif not a.startswith("--"):
        DEV = a; _i += 1
    else:
        _i += 1

# ---- register map (16-byte stride, matches aclk_readout_axi) ----
EVENT_COUNT, DEBUG, GT_CTRL = 0x70, 0xA0, 0xF0
OFF = 0 if "uio" in DEV else 0x8000_0000

fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OFF)

def rd(o):
    return struct.unpack("<I", m[o:o + 4])[0]

def wr(o, v):
    m[o:o + 4] = struct.pack("<I", v & 0xFFFFFFFF)

def apply_gt(txdiff, txpost, txpre):
    """Set the TX-driver fields + pulse RX datapath re-init (clears stickies, re-aligns).
    GT_CTRL: [13:9]=txdiff [18:14]=txpost [23:19]=txpre [8]=rx datapath re-init."""
    val = (BASE & ~0x100) | ((txdiff & 0x1F) << 9) | ((txpost & 0x1F) << 14) | ((txpre & 0x1F) << 19)
    wr(GT_CTRL, val | 0x100)   # assert re-init with the new config
    time.sleep(0.03)
    wr(GT_CTRL, val)           # release: RX re-inits + re-aligns under the new TX eye
    time.sleep(0.15)

def decode(d):
    return ((d >> 30) & 1, (d >> 31) & 1, (d >> 28) & 1, (d >> 14) & 0x3FFF)  # byteali, aligned, notintbl, disperr

def measure(dwell, samples=40):
    """Dwell and score: fraction of samples byte-aligned / decoder-locked, whether any
    invalid symbol seen, events decoded, and how many samples saw disperr still moving
    (0 => errors stopped = clean eye; immune to the 14-bit counter wrap)."""
    evt0 = rd(EVENT_COUNT)
    ba = al = 0
    ni = 0
    last_de = None
    de_active = 0
    for _ in range(samples):
        byteali, aligned, notintbl, disperr = decode(rd(DEBUG))
        ba += byteali; al += aligned; ni |= notintbl
        if last_de is not None and disperr != last_de:
            de_active += 1
        last_de = disperr
        time.sleep(dwell / samples)
    evt1 = rd(EVENT_COUNT)
    return {
        "byteali_frac": ba / samples,
        "aligned_frac": al / samples,
        "notintbl": ni,
        "evt_delta": (evt1 - evt0) & 0xFFFFFFFF,
        "disperr_active": de_active,
        "samples": samples,
    }

# ---- sweep grid ----
TXDIFF = [0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x1F]   # 0 would map to PL default 0x18, so omitted
TXPOST = [0x00, 0x08, 0x10, 0x18] if SWEEP_POST else [0x00]
TXPRE = [0x00, 0x08, 0x14] if SWEEP_PRE else [0x00]

combos = [(d, p, q) for d in TXDIFF for p in TXPOST for q in TXPRE]
print("# GT TX-driver sweep on %s | base=0x%X | dwell=%.1fs | %d combos" % (DEV, BASE, DWELL, len(combos)))
print("# WIN = aligned_frac>0 or evt_delta>0 (ideally disperr_active=0). disperr_active is #samples"
      " where the error counter moved (0 = errors stopped).")
print("%-7s %-7s %-7s | %-9s %-9s %-9s %-9s %-8s" % (
    "txdiff", "txpost", "txpre", "byteali", "aligned", "evt_d", "notintbl", "de_act"))

results = []
try:
    for (d, p, q) in combos:
        apply_gt(d, p, q)
        r = measure(DWELL)
        results.append(((d, p, q), r))
        flag = " <== LOCK" if (r["aligned_frac"] > 0 or r["evt_delta"] > 0) else (
               "  (byteali)" if r["byteali_frac"] > 0 else "")
        print("0x%02X    0x%02X    0x%02X    | %-9.2f %-9.2f %-9d %-9d %-8d%s" % (
            d, p, q, r["byteali_frac"], r["aligned_frac"], r["evt_delta"], r["notintbl"],
            r["disperr_active"], flag))
except KeyboardInterrupt:
    print("\n# interrupted")

# ---- rank + verdict ----
# best = most decoder-locked, then most byte-aligned, then most events, then fewest errors
def score(item):
    (_, r) = item
    return (r["aligned_frac"], r["evt_delta"], r["byteali_frac"], -r["disperr_active"])

if results:
    results.sort(key=score, reverse=True)
    (bd, bp, bq), br = results[0]
    print("")
    print("# BEST: txdiff=0x%02X txpost=0x%02X txpre=0x%02X -> byteali=%.2f aligned=%.2f evt_delta=%d disperr_active=%d" % (
        bd, bp, bq, br["byteali_frac"], br["aligned_frac"], br["evt_delta"], br["disperr_active"]))
    if br["aligned_frac"] > 0 or br["evt_delta"] > 0:
        print("# VERDICT: a TX setting OPENED the eye. Re-run aclkgt_read.py with --txdiff 0x%02X"
              " (and --txpost/--txpre) to confirm + stream events." % bd)
    elif br["byteali_frac"] > 0:
        print("# VERDICT: best case only flickers byte-align, never a stable decode. TX drive helps a"
              " little but is NOT the fix; the eye is marginal for another reason (optics/RX).")
    else:
        print("# VERDICT: NO TX setting byte-aligned. TX drive is not the bottleneck. Look elsewhere:"
              " fiber/connector/SFP module, or the RX side. (M0 internal loopback still proves the logic.)")

# restore a known-normal state
wr(GT_CTRL, BASE & ~0x100)
print("# GT_CTRL restored to 0x%X" % (BASE & ~0x100))
