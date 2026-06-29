"""Encoder -> ACLK_RCV: each injected TCLK event must decode as EVENT={0x00,ev},
DATA={32'h0,count} where count is the per-event-code occurrence number (1-based),
nulls (event[7:0]==0xFF) excluded, in order, with no CRC errors."""
import sys
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from plot_util import save_cumulative_plot  # noqa: E402

# (event_byte) sequence to inject on TCLK; repeats exercise the count RAM.
EVENTS = [0x02, 0x07, 0x02, 0x42, 0x07, 0x02]


async def drive_event(dut, byte):
    """One DAVn strobe (active-low) in the clk_40m domain with the data byte."""
    await RisingEdge(dut.clk_40m)
    dut.tclk_data.value = byte
    dut.tclk_davn.value = 0
    await RisingEdge(dut.clk_40m)
    dut.tclk_davn.value = 1
    # space events out so each is framed before the next arrives
    await ClockCycles(dut.clk_40m, 60)


@cocotb.test()
async def test_encoder_to_rcv(dut):
    cocotb.start_soon(Clock(dut.clk_tx, 16, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_40m, 25, unit="ns").start())
    dut.tclk_davn.value = 1
    dut.tclk_data.value = 0
    dut.rstn.value = 0
    await ClockCycles(dut.clk_tx, 10)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    await ClockCycles(dut.clk_tx, 300)  # wait out the 256-cycle count-RAM zeroing sweep + RX alignment

    captured = []
    errors = 0

    async def collector():
        nonlocal errors
        while True:
            await RisingEdge(dut.clk_tx)
            await Timer(1, unit="ns")
            if int(dut.ACLK_VALID.value) == 1:
                ev = int(dut.ACLK_EVENT.value)
                da = int(dut.ACLK_DATA.value)
                if (ev & 0xFF) != 0xFF:        # skip nulls
                    captured.append((ev, da))
            if int(dut.ACLK_ERROR.value) == 1:
                errors += 1

    cocotb.start_soon(collector())

    for b in EVENTS:
        await drive_event(dut, b)
    await ClockCycles(dut.clk_tx, 400)

    assert int(dut.RX_ALIGNED_OUT.value) == 1, "RX never aligned"
    assert errors == 0, f"unexpected CRC errors: {errors}"
    assert captured, "no real events decoded"

    # Expected per-event-code occurrence count (1-based), in injection order.
    counts = {}
    expected = []
    for b in EVENTS:
        counts[b] = counts.get(b, 0) + 1
        expected.append((0x0000 | b, counts[b]))

    got = [(ev & 0xFFFF, da & 0xFFFFFFFF) for (ev, da) in captured]
    assert got == expected, f"decoded {got} != expected {expected}"
    dut._log.info(f"encoder<->rcv agree: {got}")

    out = (Path(__file__).resolve().parents[2] / "sim_build"
           / "aclk_tclk_encoder_loop" / "plots" / "encoder_events.png")
    save_cumulative_plot(list(range(len(got))), list(range(1, len(got) + 1)), None, out)
