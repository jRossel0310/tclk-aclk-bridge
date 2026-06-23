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
