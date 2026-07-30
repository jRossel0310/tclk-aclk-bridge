"""Faithfulness metrics for a captured TCLK/ACLK event CSV, checked against the
official Fermilab TCLK event definitions (resources/Tclk/*.pdf).

The capture is "faithful" if the bytes we wrote to disk are the bytes the
accelerator actually sent. We can't diff against a golden log, but the TCLK spec
pins down enough structure to check it several independent ways:

  1. DECODE INTEGRITY  - every event code must be a *defined* code. Codes that
     the spec marks Undefined / Decommissioned / Reserved / "$FE never
     generated" cannot legitimately appear; each one is a candidate bit error.
     For every bad code we test whether a single bit-flip lands on a common
     valid code (the signature of a 1-bit decode error).

  2. FIXED-RATE CLOCKS - the spec gives exact rates for the free-running events
     ($02 5 s, $8F 1 Hz GPS, $BA 20 Hz, $0C/$0F 15 Hz, $07 720 Hz). Measured
     rate vs spec tests calibration; missed-marker counting tests whether we
     dropped events (FIFO/reader loss) vs whole-machine-state gaps.

  3. GPS vs TCLK  - $8F is a GPS 1 Hz tick; White Rabbit is GPS-disciplined too,
     so $8F must measure ~1.000000 s to sub-ppm. $02 is TCLK/mains, ~7.9 ppm
     off. If $8F is tight and $02 is not, the offset is real machine physics,
     not a timestamp error -- i.e. the stamps are faithful.

  4. STREAM INTEGRITY  - timestamps must be monotonic, duplicate-free, and the
     Redis stream id (publish ms) must track the WR event time.

All timing math is in integer nanoseconds (sec*1e9 + ns); epoch seconds are
~1.78e9 so sec+ns/1e9 in float64 loses the ns digit.

    python tclk_faithfulness.py ../../events-tclk-20260716.csv
"""
import argparse
import csv
from collections import Counter

import numpy as np

NS = 1_000_000_000

# --- TCLK spec, transcribed from resources/Tclk/ (Information + Definitions) ---
# Codes with a live HCRM channel assignment: the machine can actually emit these.
DEFINED = {
    0x00, 0x02, 0x03, 0x04, 0x05, 0x07, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,
    0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B, 0x1C,
    0x1D, 0x1E, 0x1F,
    0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x2B, 0x2C,
    0x2D, 0x2E, 0x2F,
    0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D,
    0x3E, 0x3F,
    0x4C,
    0x50, 0x51, 0x52, 0x53, 0x57, 0x58,
    0x67,
    0x70, 0x72, 0x77, 0x79, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E, 0x7F,
    0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8C, 0x8D, 0x8E,
    0x8F,
    0x90, 0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0x9B, 0x9C,
    0x9D, 0x9E, 0x9F,
    0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAC, 0xAD, 0xAE,
    0xAF,
    0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB8, 0xB9, 0xBA, 0xBB, 0xBC, 0xBD,
    0xBE, 0xBF,
    0xDA, 0xDD, 0xDE,
    0xE0, 0xE1, 0xE2, 0xE3, 0xE4, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xED, 0xEF,
    0xF1, 0xF3, 0xF6, 0xF7, 0xFA,
}
ALLOWED = DEFINED | {0xFF}          # $FF: "No-Op, sometimes generated" - legal
NEVER = 0xFE                        # "No-Op, never generated" - always an error

# Free-running events with a spec'd rate (Hz). These are the faithfulness anchors.
KNOWN_RATE = {
    0x02: 0.2,      # Time-plot reset: every 50,000,000 cycles = 5 s, async to all
    0x8F: 1.0,      # GPS receiver 1 Hz tick
    0xBA: 20.0,     # 20 Hz, A phase of MAC-room AC line
    0x0C: 15.0,     # 15 Hz, GMPS BMIN delayed
    0x0F: 15.0,     # 15 Hz, A phase of MAC-room AC line
    0x07: 720.0,    # 720 Hz, poorly synced to AC line
}
CODE_NAME = {0x02: "timeplot-reset", 0x8F: "GPS-1Hz", 0xBA: "20Hz-mains",
             0x0C: "15Hz-GMPS", 0x0F: "15Hz-mains", 0x07: "720Hz", 0x00: "supercycle"}
COLLECT = set(KNOWN_RATE) | {0x00, 0x07}  # codes whose timestamps we keep for interval work


ALL_ONES = (1 << 64) - 1                     # 0xFFFF...FF corrupted-payload marker
BIN_NS = 60 * NS                             # timeline bin width for the figure


def scan(path):
    """One streaming pass: per-code counts, clock-code stamps, stream integrity.

    Also bins valid/invalid event counts per minute (binv/bini, keyed by absolute
    minute index) so a caller can draw the capture timeline without a second pass.
    """
    counts = Counter()
    stamps = {c: [] for c in COLLECT}
    binv, bini = Counter(), Counter()           # valid / invalid events per minute
    prev = None
    inversions = dups = 0
    max_gap = 0
    gap_at = 0
    lat = []                                    # id_ms - wr_ms, publish latency
    bad = dict(wide=0, low_valid=0, allones=0, first=0, last=0)  # invalid-frame profile
    with open(path, newline="") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) < 4:
                continue
            sec, ns, ev = int(row[1]), int(row[2]), int(row[3])
            t = sec * NS + ns
            counts[ev] += 1
            if ev in stamps:
                stamps[ev].append(t)
            if ev not in ALLOWED:                # profile the invalid frames
                bad["first"] = bad["first"] or t
                bad["last"] = t
                bini[t // BIN_NS] += 1
                if ev > 0xFF:
                    bad["wide"] += 1
                    if (ev & 0xFF) in ALLOWED:
                        bad["low_valid"] += 1
                if len(row) >= 5 and int(row[4]) == ALL_ONES:
                    bad["allones"] += 1
            else:
                binv[t // BIN_NS] += 1
            if prev is not None:
                if t < prev:
                    inversions += 1
                elif t == prev:
                    dups += 1
                elif t - prev > max_gap:          # largest all-event capture hole
                    max_gap, gap_at = t - prev, prev
            prev = t
            if row[0]:
                id_ms = int(row[0].split("-")[0])
                lat.append(id_ms - (sec * 1000 + ns // 1_000_000))
    for c in stamps:
        stamps[c] = np.array(stamps[c], dtype=np.int64)
    return (counts, stamps, inversions, dups, np.array(lat, dtype=np.int64),
            max_gap, gap_at, bad, binv, bini)


def bit_neighbors(code):
    """Valid codes reachable from `code` by flipping exactly one of 8 bits."""
    return [code ^ (1 << i) for i in range(8) if (code ^ (1 << i)) in DEFINED]


def clock_report(code, t, total_span_s):
    """Rate + drop metrics for one fixed-rate code, in integer-ns.

    Rate/offset come from the *median single-cycle interval* (instantaneous,
    gap-immune); completeness comes from counting missed markers, split into
    isolated drops (FIFO loss during live time) and big gaps (capture/state
    holes) so the two failure modes don't contaminate each other.
    """
    rate = KNOWN_RATE[code]
    nom = NS / rate                                  # nominal period, ns
    t = np.unique(t)
    if len(t) < 3:
        return None
    d = np.diff(t).astype(np.float64)
    cyc = np.rint(d / nom).astype(np.int64)
    cyc = np.maximum(cyc, 1)
    single = cyc == 1
    med = float(np.median(d[single])) if single.any() else nom
    meas_rate = NS / med                             # instantaneous, gap-immune
    total_time = (t[-1] - t[0]) / NS
    gap_time = float(d[cyc > 4].sum()) / NS          # time inside big gaps
    live_time = total_time - gap_time
    small = int(np.sum(cyc[cyc <= 4] - 1))           # isolated 1-3 marker drops
    biggap = int(np.sum(cyc > 4))                    # number of large gaps
    biggap_lost = int(np.sum(cyc[cyc > 4] - 1))      # markers lost inside them
    jit = float(np.std(d[single])) if single.any() else float("nan")
    return dict(code=code, rate=rate, meas_rate=meas_rate, n=len(t), nom=nom,
                ppm=(med / nom - 1) * 1e6, jit=jit, small=small, biggap=biggap,
                biggap_lost=biggap_lost, live_cover=live_time / total_time,
                drop_ppm=small / (len(t) - 1) * 1e6 if len(t) > 1 else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="+")
    a = ap.parse_args()

    for path in a.csv:
        name = path.replace("\\", "/").split("/")[-1]
        counts, stamps, inversions, dups, lat, max_gap, gap_at, bad, _, _ = scan(path)
        total = sum(counts.values())
        span = None
        t0 = stamps[0x02][0] if len(stamps[0x02]) else 0
        for c in (0xBA, 0x0F, 0x0C, 0x02):
            if len(stamps[c]) > 2:
                span = (stamps[c][-1] - stamps[c][0]) / NS
                break

        print("=" * 78)
        print(f"{name}   {total:,} events" + (f", {span/3600:.2f} h span" if span else ""))
        print("=" * 78)

        # --- 1. decode integrity ---------------------------------------------
        bad_codes = {c: n for c, n in counts.items() if c not in ALLOWED}
        bad_n = sum(bad_codes.values())
        never_n = counts.get(NEVER, 0)
        print(f"\n[1] DECODE INTEGRITY")
        print(f"    defined-code events : {total - bad_n:,} / {total:,} "
              f"({100*(total-bad_n)/total:.6f}%)")
        print(f"    invalid-code events : {bad_n:,}  "
              f"({bad_n/total*1e6:.3f} ppm)   distinct bad codes: {len(bad_codes)}")
        print(f"    $FE 'never generated': {never_n}")
        if bad_codes:
            wide8 = sum(n for c, n in bad_codes.items() if c > 0xFF)
            narrow = bad_n - wide8
            print(f"    framing corruption (>8-bit, $FF00|code high-byte leak): "
                  f"{wide8:,}  [{bad['low_valid']:,} have a valid low byte]")
            print(f"    all-ones ($FFFF..) data payload on bad frames: {bad['allones']:,}")
            print(f"    8-bit undefined codes (single-bit-error candidates): {narrow:,}")
            if bad['first']:
                print(f"    invalid-frame time window: t0+{(bad['first']-t0)/NS/3600:.2f} h "
                      f"to t0+{(bad['last']-t0)/NS/3600:.2f} h")
            worst = sorted(bad_codes.items(), key=lambda kv: -kv[1])[:8]
            print(f"    {'code':>8}{'count':>10}{'ppm':>10}  low byte  1-bit-flip -> valid")
            for c, n in worst:
                lo = c & 0xFF
                nb = bit_neighbors(c) if c <= 0xFF else bit_neighbors(lo)
                tag = f"${lo:02X}{' ok' if lo in ALLOWED else ''}" if c > 0xFF else ""
                nbs = ", ".join(f"${x:02X}" for x in nb) if nb else "-"
                print(f"    ${c:04X}{n:>10}{n/total*1e6:>10.3f}  {tag:>8}  {nbs}")

        # --- 2 + 3. fixed-rate clocks + GPS/TCLK -----------------------------
        print(f"\n[2] FIXED-RATE CLOCK FAITHFULNESS  (spec vs measured)")
        print(f"    {'code':>14}{'spec':>8}{'measured':>11}{'offset':>10}"
              f"{'drops':>9}{'biggaps':>9}{'live':>8}{'jitter':>10}")
        print(f"    {'':>14}{'(Hz)':>8}{'(Hz)':>11}{'(ppm)':>10}"
              f"{'(ppm)':>9}{'(#)':>9}{'(%)':>8}{'(ns)':>10}")
        rows = {}
        for c in (0x02, 0x8F, 0xBA, 0x0C, 0x0F, 0x07):
            rep = clock_report(c, stamps.get(c, np.array([], np.int64)), span or 1)
            if rep is None:
                print(f"    {CODE_NAME[c]:>14}{KNOWN_RATE[c]:>8.3g}"
                      f"{'--':>11}{'--':>10}{'--':>9}{'--':>9}{'--':>8}"
                      f"{'--':>10}   (obs count {counts.get(c, 0)})")
                continue
            rows[c] = rep
            print(f"    {CODE_NAME[c]:>14}{rep['rate']:>8.3g}{rep['meas_rate']:>11.5f}"
                  f"{rep['ppm']:>10.3f}{rep['drop_ppm']:>9.1f}{rep['biggap']:>9}"
                  f"{100*rep['live_cover']:>8.1f}{rep['jit']:>10.0f}")

        if 0x8F in rows and 0x02 in rows:
            gps, tclk = rows[0x8F]['ppm'], rows[0x02]['ppm']
            print(f"\n[3] GPS vs TCLK separation")
            print(f"    $8F GPS 1 Hz measured offset : {gps:+.3f} ppm  "
                  f"(tests White-Rabbit vs GPS -- both GPS-disciplined)")
            print(f"    $02 TCLK 5 s measured offset  : {tclk:+.3f} ppm  "
                  f"(tests TCLK/mains vs GPS)")
            print(f"    -> TCLK-vs-GPS physical offset: {tclk - gps:+.3f} ppm; "
                  f"timestamp scale error |$8F| = {abs(gps):.3f} ppm")

        # --- 4. stream integrity + capture continuity ------------------------
        print(f"\n[4] STREAM INTEGRITY")
        print(f"    non-monotonic stamps : {inversions}")
        print(f"    duplicate stamps     : {dups}")
        if len(lat):
            p = np.percentile(lat, [50, 99])
            print(f"    publish latency id-WR: median {p[0]:.0f} ms, 99% {p[1]:.0f} ms")
        if span:
            gs = max_gap / NS
            frac = gs / span * 100
            flag = "  <-- capture hole" if gs > 5 else ""
            print(f"    largest capture gap  : {gs:.1f} s ({frac:.1f}% of span) "
                  f"at t0+{(gap_at - (stamps[0x02][0] if len(stamps[0x02]) else gap_at))/NS/3600:.2f} h{flag}")
        print()


if __name__ == "__main__":
    main()
