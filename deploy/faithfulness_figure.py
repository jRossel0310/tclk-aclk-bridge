"""One-figure summary of the CSV faithfulness analysis (deploy/tclk_faithfulness.py).

Three panels:
  A  faithfulness scorecard  - live capture coverage + invalid-frame rate per file
  B  ACLK-0716 capture timeline - valid event rate vs time, the 5 h hole, and the
     post-resync $FF00|code corruption burst overlaid
  C  timestamp scale - GPS $8F reveals our stamp clock is ~7.5 ppm slow; the real
     TCLK-vs-GPS offset (residual) is tiny

    python faithfulness_figure.py ../../events-tclk-20260716.csv \
        ../../events-tclk-20260717.csv ../../events-aclk-20260716.csv \
        ../../events-aclk-20260717.csv -o faithfulness.png
"""
import argparse

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from tclk_faithfulness import scan, clock_report, NS, ALLOWED

INK, MUTED, SURF = "#1b1b1b", "#6f6f6f", "#ffffff"
AZURE, WARM, GREEN = "#2b8cc4", "#c4562b", "#2ba86f"
plt.rcParams.update({"font.family": "DejaVu Sans", "svg.fonttype": "none"})


def short(name):
    src = "TCLK" if "tclk" in name else "ACLK"
    day = "0716" if "20260716" in name else "0717"
    return f"{src} {day}"


def summarize(path):
    counts, stamps, inv, dups, lat, max_gap, gap_at, bad, binv, bini = scan(path)
    total = sum(counts.values())
    bad_n = sum(n for c, n in counts.items() if c not in ALLOWED)
    r02 = clock_report(0x02, stamps[0x02], 1)
    r8f = clock_report(0x8F, stamps[0x8F], 1)
    r0f = clock_report(0x0F, stamps[0x0F], 1)
    return dict(
        name=short(path.replace("\\", "/").split("/")[-1]),
        total=total, bad_n=bad_n, bad_ppm=bad_n / total * 1e6,
        live=100 * (r0f["live_cover"] if r0f else 1.0),
        gps_ppm=r8f["ppm"] if r8f else np.nan,
        tclk_ppm=r02["ppm"] if r02 else np.nan,
        resid=(r02["ppm"] - r8f["ppm"]) if (r02 and r8f) else np.nan,
        binv=binv, bini=bini)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="+")
    ap.add_argument("-o", "--out", default="faithfulness.png")
    a = ap.parse_args()

    res = [summarize(p) for p in a.csv]

    fig = plt.figure(figsize=(16, 8.4), dpi=150, facecolor=SURF)
    gs = fig.add_gridspec(2, 2, width_ratios=[1, 1.35], height_ratios=[1, 1],
                          hspace=0.42, wspace=0.26)
    axA = fig.add_subplot(gs[0, 0])
    axC = fig.add_subplot(gs[1, 0])
    axB = fig.add_subplot(gs[:, 1])

    # --- A: faithfulness scorecard ---------------------------------------
    y = np.arange(len(res))[::-1]
    for yi, r in zip(y, res):
        clean = r["bad_ppm"] < 1 and r["live"] > 99
        axA.barh(yi, r["live"], color=GREEN if clean else WARM, alpha=0.85, height=0.6)
        tag = "clean" if clean else (
            f"{r['bad_ppm']:.0f} ppm bad" if r["bad_ppm"] >= 1 else f"{r['live']:.0f}% live")
        axA.text(r["live"] + 1.5, yi, tag, va="center", ha="left",
                 fontsize=11, color=INK if clean else WARM, fontweight="bold")
    axA.set_yticks(y)
    axA.set_yticklabels([r["name"] for r in res], fontsize=11)
    axA.set_xlim(0, 118)
    axA.set_xticks([0, 25, 50, 75, 100])
    axA.set_xlabel("live capture coverage  (% of span)", fontsize=12, color=MUTED)
    axA.set_title("A   Faithfulness scorecard", loc="left", fontsize=14,
                  fontweight="bold", color=INK)
    axA.axvline(100, color=MUTED, lw=0.6, ls=":")

    # --- C: timestamp scale, GPS vs TCLK ---------------------------------
    x = np.arange(len(res))
    w = 0.38
    axC.bar(x - w / 2, [abs(r["gps_ppm"]) for r in res], w, color=AZURE,
            label=r"stamp-clock error  |\$8F GPS|")
    axC.bar(x + w / 2, [abs(r["resid"]) for r in res], w, color=WARM,
            label="real TCLK vs GPS  |residual|")
    for xi, r in zip(x, res):
        axC.text(xi + w / 2, abs(r["resid"]) + 0.15, f"{abs(r['resid']):.2f}",
                 ha="center", fontsize=8.5, color=WARM)
    axC.set_xticks(x)
    axC.set_xticklabels([r["name"] for r in res], fontsize=10)
    axC.set_ylabel("frequency offset  (ppm)", fontsize=12, color=MUTED)
    axC.set_title(r"C   Timestamp scale  (GPS \$8F vs TCLK \$02)",
                  loc="left", fontsize=14, fontweight="bold", color=INK)
    axC.legend(fontsize=9.5, frameon=False, loc="upper center")
    axC.set_ylim(0, 9.3)

    # --- B: ACLK-0716 capture timeline -----------------------------------
    tl = max(res, key=lambda r: r["bad_n"])          # the file with the defects
    binv, bini = tl["binv"], tl["bini"]
    bmin = min(binv)
    bmax = max(max(binv), max(bini) if bini else bmin)
    xs = np.arange(bmin, bmax + 1)
    hrs = (xs - bmin) / 60.0
    vrate = np.array([binv.get(b, 0) / 60.0 for b in xs])
    irate = np.array([bini.get(b, 0) for b in xs])

    axB.fill_between(hrs, vrate, color=AZURE, alpha=0.85, lw=0, label="valid event rate")
    axB.set_xlabel("elapsed time  (hours)", fontsize=12, color=MUTED)
    axB.set_ylabel("valid event rate  (Hz)", fontsize=12, color=AZURE)
    axB.set_title(f"B   {tl['name']} capture timeline", loc="left", fontsize=14,
                  fontweight="bold", color=INK)
    axB.set_ylim(0, max(vrate) * 1.25)
    axB.tick_params(axis="y", labelcolor=AZURE)

    axR = axB.twinx()
    axR.bar(hrs, irate, width=(hrs[1] - hrs[0]) if len(hrs) > 1 else 0.02,
            color=WARM, label="invalid frames / min")
    axR.set_ylabel("invalid frames / min", fontsize=12, color=WARM)
    axR.tick_params(axis="y", labelcolor=WARM)
    axR.set_ylim(0, max(irate.max() * 1.4, 1))

    # annotate the hole and the burst
    hole = hrs[vrate == 0]
    if len(hole):
        axB.axvspan(hole.min(), hole.max(), color=MUTED, alpha=0.12)
        axB.text((hole.min() + hole.max()) / 2, max(vrate) * 0.6,
                 f"{(hole.max()-hole.min()):.1f} h\nno valid events", ha="center",
                 va="center", fontsize=11, color=MUTED, fontweight="bold")
    if irate.max() > 0:
        bx = hrs[np.argmax(irate)]
        axR.annotate(f"corruption burst\n{tl['bad_n']:,} frames  (\\$FF00|code)",
                     xy=(bx, irate.max()), xytext=(bx + 1.2, irate.max() * 0.9),
                     fontsize=10.5, color=WARM, fontweight="bold",
                     arrowprops=dict(arrowstyle="->", color=WARM))

    fig.suptitle("CSV faithfulness vs the TCLK spec  (resources/Tclk/)",
                 fontsize=15.5, fontweight="bold", color=INK, x=0.012, ha="left")
    fig.savefig(a.out, dpi=150, facecolor=SURF, bbox_inches="tight")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
