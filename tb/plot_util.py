"""Shared plotting utility for cocotb readout testbenches.

Accepts a list of (time_ns, cumulative_in, cumulative_read) samples and writes
a two-panel FIFO-occupancy + cumulative-events figure to `out_path`.
"""

import warnings
from pathlib import Path


def save_fifo_plot(series, n_events, title, out_path):
    """Write a FIFO-occupancy / cumulative-events plot.

    Parameters
    ----------
    series      : list of (time_ns, cumulative_in, cumulative_read)
    n_events    : total event count for the title annotation
    title       : top-panel title string
    out_path    : Path (or str) -- destination .png file; parent dirs are created
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return None
    if not series:
        return None

    ts  = [s[0] for s in series]
    win = [s[1] for s in series]
    r   = [s[2] for s in series]
    occ = [a - b for a, b in zip(win, r)]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    ax1.plot(ts, occ, color="tab:blue", lw=1.6)
    ax1.set_ylabel("events buffered in FIFO")
    ax1.set_title(f"{title} ({n_events} events)")
    ax1.set_ylim(bottom=0)
    ax1.grid(True, alpha=0.3)
    ax2.plot(ts, win, color="tab:green",  lw=1.6, label="events into FIFO (decoder)")
    ax2.plot(ts, r,   color="tab:orange", lw=1.6, label="events read over AXI")
    ax2.set_xlabel("sim time (ns)")
    ax2.set_ylabel("cumulative events")
    ax2.legend(loc="upper left")
    ax2.grid(True, alpha=0.3)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


def save_word_stream_plot(words, out_path):
    """Write a two-panel DATA16 value + K_OUT flag vs sample index plot.

    Parameters
    ----------
    words    : list of (word16, k2) samples captured from the generator
    out_path : Path (or str) -- destination .png file; parent dirs are created

    Comma positions (K=01, low byte 0xBC) are marked with vertical lines.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return None

    indices = list(range(len(words)))
    values  = [w for w, _k in words]
    kflags  = [k for _w, k in words]
    commas  = [i for i, (w, k) in enumerate(words)
               if k == 0b01 and (w & 0xFF) == 0xBC]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6), sharex=True)

    ax1.step(indices, values, where="post", color="tab:blue", lw=1.4)
    for c in commas:
        ax1.axvline(c, color="tab:red", lw=0.8, alpha=0.7, linestyle="--")
    ax1.set_ylabel("DATA16 (hex)")
    ax1.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _: f"0x{int(v):04X}"))
    ax1.set_title(
        "aclk_gt_frame_gen word stream (red = comma word boundary)")
    ax1.grid(True, alpha=0.3)

    ax2.step(indices, kflags, where="post", color="tab:orange", lw=1.4)
    for c in commas:
        ax2.axvline(c, color="tab:red", lw=0.8, alpha=0.7, linestyle="--")
    ax2.set_ylabel("K_OUT")
    ax2.set_xlabel("sample index (clock cycle)")
    ax2.set_ylim(-0.1, 1.1)
    ax2.grid(True, alpha=0.3)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


def save_cumulative_plot(times_ns, cumulative_decoded, align_time_ns,
                         out_path):
    """Write a cumulative-decoded-events vs sim-time plot.

    Parameters
    ----------
    times_ns           : list of sim timestamps in nanoseconds
    cumulative_decoded : list of cumulative event counts (same length)
    align_time_ns      : sim time (ns) when RX first aligned, or None
    out_path           : Path (or str) -- destination .png file; parent dirs
                         are created
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return None

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(times_ns, cumulative_decoded, color="tab:blue", lw=1.6,
            label="cumulative decoded events")
    if align_time_ns is not None:
        ax.axvline(align_time_ns, color="tab:red", lw=1.2, linestyle="--",
                   label=f"RX_ALIGNED at {align_time_ns} ns")
    ax.set_xlabel("sim time (ns)")
    ax.set_ylabel("cumulative events decoded")
    ax.set_title(
        "aclk_gt_frame_gen -> ACLK_RCV loopback: decoded events vs time")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
