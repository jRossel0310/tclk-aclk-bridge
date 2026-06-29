"""Cocotb test for the aclk_lite_bridge -> aclk_lite_encoder -> clk_rcv end-to-end chain.

Tests:
  1. A real ACLK event (event_id=0x1234, data=0xDEADBEEFCAFE0001) is driven on the
     rx side; the bridge crosses it to the encoder domain and asserts enc_start; the
     encoder serialises a 12-byte frame; clk_rcv decodes it and recovers the exact
     event_id and data.
  2. A null event (aclk_event[7:0]==0xFF) is driven; the bridge must NOT assert
     enc_start (it is filtered out before the FIFO).

A matplotlib step plot of the encoder line (enc_line) is saved to
sim_build/aclk_lite_bridge/plots/enc_line.png on completion of test 1.
"""

from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer
import cocotb.utils

# ---- timing constants ----
# enc_clk = 80 MHz -> 12.5 ns period
ENC_CLK_PS = 12_500   # picoseconds
CLK_40M_PS = 25_000
RX_CLK_PS  = 12_500   # rx_clk at 80 MHz for simplicity

# serdec needs idle cells to lock before we start monitoring.
# 50 idle cells * 8 samples/cell * 12.5 ns = 5 us warm-up.
WARMUP_CELLS = 50

PLOT_DIR = (
    Path(__file__).resolve().parents[2]
    / "sim_build" / "aclk_lite_bridge" / "plots"
)


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


def _save_line_plot(timestamps_ns, values, path):
    """Emit a step plot of the encoder Manchester line. Guarded import."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        Path(path).parent.mkdir(parents=True, exist_ok=True)

        fig, ax = plt.subplots(figsize=(14, 2))
        ax.step(timestamps_ns, values, where="post", linewidth=0.8, color="royalblue")
        ax.set_xlabel("Time (ns)")
        ax.set_ylabel("enc_line")
        ax.set_title("aclk_lite_bridge: encoder Manchester line (0x1234 / 0xDEADBEEFCAFE0001)")
        ax.set_ylim(-0.2, 1.3)
        ax.set_yticks([0, 1])
        fig.tight_layout()
        fig.savefig(str(path), dpi=150)
        plt.close(fig)
    except Exception:
        pass   # matplotlib absent or other error; test still passes


def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.enc_clk, ENC_CLK_PS, unit="ps").start())
    cocotb.start_soon(Clock(dut.clk_40m, CLK_40M_PS, unit="ps").start())
    cocotb.start_soon(Clock(dut.rx_clk,  RX_CLK_PS,  unit="ps").start())


async def _reset(dut):
    """Assert both resets, release together."""
    dut.rx_rstn.value      = 0
    dut.enc_rstn.value     = 0
    dut.aclk_valid.value   = 0
    dut.aclk_event.value   = 0
    dut.aclk_data.value    = 0
    await ClockCycles(dut.enc_clk, 20)
    await Timer(1, unit="ns")
    dut.rx_rstn.value  = 1
    dut.enc_rstn.value = 1
    await ClockCycles(dut.enc_clk, 20)


async def _wait_enc_start(dut, timeout_cycles=500):
    """Return True if enc_start pulses within timeout_cycles enc_clk cycles."""
    for _ in range(timeout_cycles):
        await RisingEdge(dut.enc_clk)
        await Timer(1, unit="ns")
        if _b(dut.enc_start) == 1:
            return True
    return False


async def _collect_decoded(dut, timeout_40m_cycles=4000):
    """Wait for clk_rcv to produce an event_valid pulse; return (event_id, data_valid,
    data) or None on timeout."""
    for _ in range(timeout_40m_cycles):
        await RisingEdge(dut.clk_40m)
        await Timer(1, unit="ns")
        if _b(dut.event_valid) == 1:
            return (_b(dut.event_id), _b(dut.data_valid), _b(dut.data))
    return None


@cocotb.test()
async def test_real_event_recovered(dut):
    """A real ACLK event crosses the bridge and is recovered by clk_rcv end-to-end."""
    _start_clocks(dut)
    await _reset(dut)

    # ---- collect enc_line samples for the plot ----
    line_ts: list  = []
    line_val: list = []

    async def _sample_line():
        while True:
            await RisingEdge(dut.enc_clk)
            await Timer(1, unit="ns")
            t_ns = cocotb.utils.get_sim_time(unit="ns")
            line_ts.append(t_ns)
            line_val.append(_b(dut.enc_line))

    sample_task = cocotb.start_soon(_sample_line())

    # Let serdec warm up: encoder runs idle -> serdec locks to carrier
    warmup_ns = WARMUP_CELLS * 8 * ENC_CLK_PS // 1000
    await Timer(warmup_ns, unit="ns")

    # ---- drive one real event on the rx side ----
    EVENT_ID = 0x1234
    DATA_VAL = 0xDEADBEEFCAFE0001

    await RisingEdge(dut.rx_clk)
    dut.aclk_event.value = EVENT_ID
    dut.aclk_data.value  = DATA_VAL
    dut.aclk_valid.value = 1
    await RisingEdge(dut.rx_clk)
    dut.aclk_valid.value = 0   # one-cycle pulse

    # Wait for bridge to dispatch to the encoder
    got_start = await _wait_enc_start(dut, timeout_cycles=300)
    assert got_start, "enc_start never asserted after a real ACLK event"
    dut._log.info("enc_start asserted - bridge dispatched the event to the encoder")

    # Wait for clk_rcv to decode and recover the event
    result = await _collect_decoded(dut, timeout_40m_cycles=4000)
    assert result is not None, "clk_rcv did not produce event_valid within the timeout"

    ev_id, dv, data_out = result
    assert ev_id == EVENT_ID, (
        f"recovered event_id 0x{ev_id:04X} != expected 0x{EVENT_ID:04X}"
    )
    assert dv == 1, f"data_valid not set for 12-byte frame (got {dv})"
    assert data_out == DATA_VAL, (
        f"recovered data 0x{data_out:016X} != expected 0x{DATA_VAL:016X}"
    )

    dut._log.info(
        f"END-TO-END OK: event_id=0x{ev_id:04X}  data=0x{data_out:016X}"
    )

    # Stop sampling and emit the plot
    sample_task.cancel()
    _save_line_plot(line_ts, line_val, PLOT_DIR / "enc_line.png")
    dut._log.info(f"line plot saved to {PLOT_DIR / 'enc_line.png'}")


@cocotb.test()
async def test_null_event_suppressed(dut):
    """A null event (aclk_event[7:0]==0xFF) must NOT produce an enc_start pulse."""
    _start_clocks(dut)
    await _reset(dut)

    # Let serdec warm up
    warmup_ns = WARMUP_CELLS * 8 * ENC_CLK_PS // 1000
    await Timer(warmup_ns, unit="ns")

    # Drive a null event
    await RisingEdge(dut.rx_clk)
    dut.aclk_event.value = 0x00FF   # lower byte == 0xFF -> null, must be filtered
    dut.aclk_data.value  = 0x0
    dut.aclk_valid.value = 1
    await RisingEdge(dut.rx_clk)
    dut.aclk_valid.value = 0

    # Verify enc_start does NOT fire in the next 300 enc_clk cycles
    null_start_seen = False
    for _ in range(300):
        await RisingEdge(dut.enc_clk)
        await Timer(1, unit="ns")
        if _b(dut.enc_start) == 1:
            null_start_seen = True
            break

    assert not null_start_seen, (
        "enc_start asserted for a null event (event[7:0]==0xFF) - bridge must suppress it"
    )
    dut._log.info("null-event suppression OK: enc_start stayed low for 300 cycles")
