"""Full pure-RTL chain: a TCLK biphase stimulus must appear at BOTH readouts.
Readout #1 (tclk_readout_top) decodes the raw TCLK byte over s_axi_*;
readout #2 (aclk_gt_readout_top) decodes the same event re-encoded as ACLK
over s2_s_axi_* (pfx="s2_" in the BFM). Both timestamps come from the shared
global_timebase and the ACLK-side timestamp must be >= the TCLK-side timestamp
for a matched event (the ACLK event is stamped after it traverses the encoder
and ACLK_RCV).
"""

import warnings
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from axi_lite_bfm import axi_read, axi_write   # noqa: E402
from tclk_tx_model import stream_samples, drive_samples  # noqa: E402

# Register byte offsets (same for both readouts)
STATUS      = 0x00
EVENT       = 0x10
DATA_HI     = 0x20
DATA_LO     = 0x30
TS_HI       = 0x40
TS_LO       = 0x50
POP         = 0x60
EVENT_COUNT = 0x70

# TCLK event bytes to inject (3 distinct codes)
EVENTS = [0x02, 0x07, 0x42]

# Warm-up: allow the TCLK serdec to lock and the encoder's 256-cycle RAM-zeroing
# sweep to finish, then 5 null frames for ACLK_RCV to align. Use a generous
# warm-up and gap so each event is fully framed and crosses both CDC boundaries
# before the next one arrives.
WARMUP_CELLS = 200   # idle biphase cells before first event (serdec lock + RX align)
GAP_CELLS    = 800   # idle biphase cells between events (encoder frame + ACLK_RCV settle)


async def pop_all(dut, pfx):
    """Drain all events from the FIFO selected by `pfx`. Returns list of (ev, ts)."""
    out = []
    while True:
        status = await axi_read(dut, STATUS, pfx=pfx)
        if status & 0x1:   # empty bit set
            break
        ev = await axi_read(dut, EVENT, pfx=pfx)
        ts = ((await axi_read(dut, TS_HI, pfx=pfx)) << 32) | (await axi_read(dut, TS_LO, pfx=pfx))
        await axi_read(dut, DATA_HI, pfx=pfx)
        await axi_read(dut, DATA_LO, pfx=pfx)
        await axi_write(dut, POP, 0, pfx=pfx)
        out.append((ev & 0xFFFF, ts))
    return out


@cocotb.test()
async def test_full_chain(dut):
    # Start all clocks
    # s_axi_aclk and s2_s_axi_aclk are driven at the same 100 MHz as pl_clk0
    # (all three serve as the AXI / shared-timebase reference clock)
    cocotb.start_soon(Clock(dut.clk_80m,        12.5, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_40m,        25,   unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_tx,         16,   unit="ns").start())
    cocotb.start_soon(Clock(dut.pl_clk0,        10,   unit="ns").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk,     10,   unit="ns").start())
    cocotb.start_soon(Clock(dut.s2_s_axi_aclk,  10,   unit="ns").start())

    # Assert resets
    dut.rstn.value             = 0
    dut.s_axi_aresetn.value    = 0
    dut.s2_s_axi_aresetn.value = 0
    dut.tclk.value             = 1

    # Zero all AXI handshake signals for both slaves
    # slave#1: s_axi_*      (pfx="" in BFM)
    # slave#2: s2_s_axi_*   (pfx="s2_" in BFM)
    for sig in ("awaddr", "awvalid", "wdata", "wstrb", "wvalid",
                "bready", "araddr", "arvalid", "rready"):
        getattr(dut, "s_axi_"    + sig).value = 0
        getattr(dut, "s2_s_axi_" + sig).value = 0

    await ClockCycles(dut.pl_clk0, 12)
    await Timer(1, unit="ns")

    # Deassert resets
    dut.rstn.value              = 1
    dut.s_axi_aresetn.value     = 1
    dut.s2_s_axi_aresetn.value  = 1

    # Wait for the encoder's 256-cycle RAM-zeroing sweep (in clk_tx) and for
    # ACLK_RCV to see 5 good null frames and align. 350 clk_tx cycles >> 256
    # (zeroing) + ~48 (5 frames x 6 cycles each) + pipeline margin.
    await ClockCycles(dut.clk_tx, 350)

    # Build the biphase sample stream and drive it
    samples = stream_samples(EVENTS, warmup_cells=WARMUP_CELLS, gap_cells=GAP_CELLS)
    cocotb.start_soon(drive_samples(dut.clk_80m, dut.tclk, samples))

    # Wait for all samples to be driven + generous settle for both readouts.
    # clk_80m is 12.5 ns, so each sample takes one clk_80m cycle.
    # After the samples, wait an additional 5000 clk_80m cycles for the last
    # ACLK frame to propagate through the encoder, ACLK_RCV, and both CDC FIFOs.
    await ClockCycles(dut.clk_80m, len(samples) + 5000)

    # Also give the AXI domain a chance to see the CDC-crossed counts
    await ClockCycles(dut.s_axi_aclk, 20)

    # Drain both readout FIFOs
    # slave#1 (TCLK): pfx="" -> s_axi_*
    # slave#2 (ACLK): pfx="s2_" -> s2_s_axi_*
    tclk_events = await pop_all(dut, "")
    aclk_events = await pop_all(dut, "s2_")

    dut._log.info(f"TCLK readout events: {tclk_events}")
    dut._log.info(f"ACLK readout events: {aclk_events}")

    # Readout #1: event byte in bits [7:0] of the EVENT register (is_tclk=1)
    got_tclk = [e & 0xFF for (e, _) in tclk_events]
    # Readout #2: event byte in bits [7:0]; encoder sets EVENT[15:8]=0x00
    got_aclk = [e & 0xFF for (e, _) in aclk_events]

    assert got_tclk == EVENTS, f"readout#1 {got_tclk} != {EVENTS}"
    assert got_aclk == EVENTS, f"readout#2 {got_aclk} != {EVENTS}"

    # Shared-timebase ordering: the ACLK event is stamped AFTER the TCLK event
    # (it must traverse the encoder and ACLK_RCV first), so ACLK ts >= TCLK ts.
    assert aclk_events[0][1] >= tclk_events[0][1], (
        f"ACLK ts ({aclk_events[0][1]}) precedes TCLK ts ({tclk_events[0][1]})"
    )

    dut._log.info(
        f"chain OK: tclk_events={tclk_events} aclk_events={aclk_events}"
    )

    # Write the cumulative-decoded plot
    _save_chain_plot(tclk_events, aclk_events, len(EVENTS))


def _save_chain_plot(tclk_events, aclk_events, n_events):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return
    fig, ax = plt.subplots(figsize=(9, 5))
    tclk_x = list(range(1, len(tclk_events) + 1))
    aclk_x = list(range(1, len(aclk_events) + 1))
    ax.step(tclk_x, [ts for (_, ts) in tclk_events],
            where="post", color="tab:blue",  lw=1.8, label="TCLK readout timestamps")
    ax.step(aclk_x, [ts for (_, ts) in aclk_events],
            where="post", color="tab:orange", lw=1.8, label="ACLK readout timestamps")
    ax.set_xlabel("event index")
    ax.set_ylabel("shared timebase tick")
    ax.set_title(
        f"Pipeline chain: {n_events} TCLK events decoded at both readouts "
        f"(shared timebase ordering)"
    )
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    out_dir = (Path(__file__).resolve().parents[2]
               / "sim_build" / "aclk_pipeline_chain" / "plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "pipeline_chain_timestamps.png"
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    import cocotb
    cocotb.log.info(f"pipeline chain plot written to {path}")
