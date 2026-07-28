"""Full pure-RTL chain with the WR timebase: a TCLK biphase stimulus must appear
at BOTH readouts stamped on the shared WR {sec, ns} timeline.

Phases:
  1. UNSYNCED: an event driven before arming carries ts == 0 at both readouts.
  2. Arm over the s3 slave, wait for all three lock bits.
  3. Drive 3 events: both readouts decode them, timestamps are WR sec:ns,
     the matched pairs agree (ACLK stamped later, within a bounded delta).

Sim second = 50 WR cells = 5000 ns (tb/wr_model.py)."""

import warnings
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from axi_lite_bfm import axi_read, axi_write   # noqa: E402
from tclk_tx_model import stream_samples, drive_samples  # noqa: E402
from wr_model import WrGen  # noqa: E402

# Readout register byte offsets (same for both readouts)
STATUS      = 0x00
EVENT       = 0x10
DATA_HI     = 0x20
DATA_LO     = 0x30
TS_HI       = 0x40
TS_LO       = 0x50
POP         = 0x60
EVENT_COUNT = 0x70

# wr_timebase_axi register byte offsets (s3_ slave)
WR_STATUS, WR_SEC_ARM, WR_SEC_NOW, WR_NS_NOW = 0x00, 0x10, 0x20, 0x30

PRE_EVENT = [0x05]           # driven BEFORE arming: must stamp ts == 0
EVENTS    = [0x02, 0x07, 0x42]

SIM_NS_PER_SEC = 5000        # 50 cells * 100 ns
SEC0 = 1_751_800_000

WARMUP_CELLS = 200
GAP_CELLS    = 800

# clk_p90/p180/p270: fine-TDC quadrature companions to clk_40m (clk_p0 inside
# tclk_readout_top). Without these the fine-TDC's off-phase samplers never
# toggle (X in sim / dead in hardware), so frozen_coarse never updates off its
# reset value -- the TCLK path decodes fine but every packed timestamp is
# wrong. This TB's clk_40m runs at 25 ns (the pre-decoupled 2:1 clk_80:clk_40
# ratio, NOT the board's decoupled 200 MHz clk_40m -- see
# rtl/aclk_pipeline_bd_top.v / vivado/build_aclk_pipeline.tcl for that), so the
# phase clocks are started at clk_40m's ACTUAL period here, not a hardcoded
# 200 MHz: the fine-TDC only requires clk_p0/p90/p180/p270 share one period at
# true quadrature (see rtl/aclk_lite/tclk_fine_tdc.sv), it has no built-in
# absolute-frequency assumption. Retuning this TB's clk_40m itself to 200 MHz
# would also require re-deriving the WR-timebase watchdog constants below
# (CLK_PERIOD_DS/CLK10_TIMEOUT/PPS_TIMEOUT for u_tb_tclk) for a 5x faster
# clock -- out of scope for wiring the fine-TDC's phase-clock inputs.
CLK40_PERIOD_PS = 25_000
PHASE_PS = CLK40_PERIOD_PS // 4


async def _start_quadrature(dut):
    # NOTE: duplicated (fixed-period variant) -- see the parametrized copy in
    # tb/tclk_readout/test_tclk_readout.py for why it is not hoisted to a shared
    # module.
    # Same fixed-per-step-delay pattern as the Part-1 sweep test's
    # _start_phases (tb/tclk_fine_tdc/test_tclk_fine_tdc.py): starting each
    # phase clock PHASE_PS after the previous one lands the four rising edges
    # at true 0/90/180/270 degree offsets within one clk_40m period. clk_40m
    # itself is started separately in test_full_chain_wr (its 0-degree edge is
    # the reference the loop below counts from).
    for sig in (dut.clk_p90, dut.clk_p180, dut.clk_p270):
        await Timer(PHASE_PS, unit="ps")
        cocotb.start_soon(Clock(sig, CLK40_PERIOD_PS, unit="ps").start())


def _split(ts):
    return (ts >> 32) & 0xFFFFFFFF, ts & 0xFFFFFFFF


def _combined(ts):
    sec, ns = _split(ts)
    return (sec - SEC0) * SIM_NS_PER_SEC + ns


async def pop_all(dut, pfx):
    """Drain all events from the FIFO selected by `pfx`. Returns list of (ev, ts)."""
    out = []
    while True:
        status = await axi_read(dut, STATUS, pfx=pfx)
        if status & 0x1:
            break
        ev = await axi_read(dut, EVENT, pfx=pfx)
        ts = ((await axi_read(dut, TS_HI, pfx=pfx)) << 32) | (await axi_read(dut, TS_LO, pfx=pfx))
        await axi_read(dut, DATA_HI, pfx=pfx)
        await axi_read(dut, DATA_LO, pfx=pfx)
        await axi_write(dut, POP, 0, pfx=pfx)
        out.append((ev & 0xFFFF, ts))
    return out


async def drive_events(dut, events):
    samples = stream_samples(events, warmup_cells=WARMUP_CELLS, gap_cells=GAP_CELLS)
    await drive_samples(dut.clk_80m, dut.tclk, samples)
    # settle: last frame through encoder, ACLK_RCV, and both CDC FIFOs
    await ClockCycles(dut.clk_80m, 5000)
    await ClockCycles(dut.s_axi_aclk, 20)


@cocotb.test()
async def test_full_chain_wr(dut):
    # Start all clocks (s_axi/s2/s3 aclk all 100 MHz, same as pl_clk0 on HW)
    cocotb.start_soon(Clock(dut.clk_80m,       12.5, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_40m,       25,   unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_tx,        16,   unit="ns").start())
    cocotb.start_soon(Clock(dut.pl_clk0,       10,   unit="ns").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk,    10,   unit="ns").start())
    cocotb.start_soon(Clock(dut.s2_s_axi_aclk, 10,   unit="ns").start())
    cocotb.start_soon(Clock(dut.s3_s_axi_aclk, 10,   unit="ns").start())
    cocotb.start_soon(_start_quadrature(dut))

    dut.rstn.value              = 0
    dut.s_axi_aresetn.value     = 0
    dut.s2_s_axi_aresetn.value  = 0
    dut.s3_s_axi_aresetn.value  = 0
    dut.tclk.value              = 1
    dut.wr_clk10.value          = 0
    dut.wr_pps.value            = 0

    for sig in ("awaddr", "awvalid", "wdata", "wstrb", "wvalid",
                "bready", "araddr", "arvalid", "rready"):
        getattr(dut, "s_axi_"    + sig).value = 0
        getattr(dut, "s2_s_axi_" + sig).value = 0
        getattr(dut, "s3_s_axi_" + sig).value = 0

    await ClockCycles(dut.pl_clk0, 12)
    await Timer(1, unit="ns")
    dut.rstn.value              = 1
    dut.s_axi_aresetn.value     = 1
    dut.s2_s_axi_aresetn.value  = 1
    dut.s3_s_axi_aresetn.value  = 1

    gen = WrGen(dut.wr_clk10, dut.wr_pps)
    gen.start()

    # encoder RAM-zeroing sweep + ACLK_RCV alignment
    await ClockCycles(dut.clk_tx, 350)

    # ---- phase 1: UNSYNCED event carries ts == 0 at both readouts ----
    await drive_events(dut, PRE_EVENT)
    pre_tclk = await pop_all(dut, "")
    pre_aclk = await pop_all(dut, "s2_")
    assert [e & 0xFF for (e, _) in pre_tclk] == PRE_EVENT
    assert [e & 0xFF for (e, _) in pre_aclk] == PRE_EVENT
    assert pre_tclk[0][1] == 0, f"unsynced TCLK ts {pre_tclk[0][1]:#x} != 0"
    assert pre_aclk[0][1] == 0, f"unsynced ACLK ts {pre_aclk[0][1]:#x} != 0"

    # ---- phase 2: arm over the s3 slave, wait for all three lock bits ----
    await axi_write(dut, WR_SEC_ARM, SEC0, pfx="s3_")
    for _ in range(600):
        s = await axi_read(dut, WR_STATUS, pfx="s3_")
        if (s & 0x7) == 0x7:
            break
        await ClockCycles(dut.s3_s_axi_aclk, 20)
    else:
        raise AssertionError(f"timebase never fully locked: STATUS=0x{s:08X}")

    # ---- phase 3: events land on the shared WR timeline ----
    await drive_events(dut, EVENTS)
    tclk_events = await pop_all(dut, "")
    aclk_events = await pop_all(dut, "s2_")
    dut._log.info(f"TCLK readout events: {tclk_events}")
    dut._log.info(f"ACLK readout events: {aclk_events}")

    assert [e & 0xFF for (e, _) in tclk_events] == EVENTS
    assert [e & 0xFF for (e, _) in aclk_events] == EVENTS

    for (ev_t, ts_t), (ev_a, ts_a) in zip(tclk_events, aclk_events):
        sec_t, ns_t = _split(ts_t)
        sec_a, ns_a = _split(ts_a)
        assert sec_t >= SEC0, f"TCLK sec {sec_t} below armed label"
        assert sec_a >= SEC0, f"ACLK sec {sec_a} below armed label"
        assert ns_t < SIM_NS_PER_SEC and ns_a < SIM_NS_PER_SEC
        ct, ca = _combined(ts_t), _combined(ts_a)
        # ACLK is stamped after the encoder + ACLK_RCV traversal: later than
        # TCLK (allow 150 ns of cross-domain sync skew), within a bounded delta.
        assert ca >= ct - 150, f"ACLK ts precedes TCLK ts: {ca} < {ct}"
        assert ca - ct <= 20_000, f"ACLK ts too far after TCLK ts: {ca - ct} ns"

    _save_chain_plot(tclk_events, aclk_events, len(EVENTS))
    gen.stop()


def _save_chain_plot(tclk_events, aclk_events, n_events):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return
    fig, ax = plt.subplots(figsize=(9, 5))
    xs = list(range(1, len(tclk_events) + 1))
    ax.step(xs, [_combined(ts) for (_, ts) in tclk_events],
            where="post", color="tab:blue", lw=1.8, label="TCLK readout (WR ns since arm)")
    ax.step(list(range(1, len(aclk_events) + 1)),
            [_combined(ts) for (_, ts) in aclk_events],
            where="post", color="tab:orange", lw=1.8, label="ACLK readout (WR ns since arm)")
    ax.set_xlabel("event index")
    ax.set_ylabel("WR timeline (sim ns since armed second)")
    ax.set_title(f"Pipeline chain: {n_events} events on the shared WR timeline")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    out_dir = (Path(__file__).resolve().parents[2]
               / "sim_build" / "aclk_pipeline_chain" / "plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_dir / "pipeline_chain_timestamps.png", dpi=120)
    plt.close(fig)
