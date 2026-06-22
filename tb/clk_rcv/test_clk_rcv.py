"""Drive real-framing 1/2/12-byte frames at the serial line; check clk_rcv decodes
event_id, data, is_tclk in order, and that a bad-parity frame raises parity_error and
produces no event. serdec needs a warm-up to lock (its transient is driven before
monitoring starts, mirroring tb/tclk_rcv)."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from clk_tx_model import stream_frames, drive_samples, SAMPLES_PER_CELL

WARMUP_CELLS = 40


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


async def reset_dut(dut):
    dut.clkline.value = 1
    dut.RESETn.value = 0
    await ClockCycles(dut.CLK_80M, 10)
    await Timer(1, unit="ns")
    dut.RESETn.value = 1
    await ClockCycles(dut.CLK_80M, 10)


async def monitor(dut, events, perrs):
    while True:
        await RisingEdge(dut.CLK_40M)
        await Timer(1, unit="ns")
        if _b(dut.event_valid) == 1:
            events.append((_b(dut.event_id), _b(dut.is_tclk), _b(dut.data_valid), _b(dut.data)))
        if _b(dut.parity_error) == 1:
            perrs.append(True)


def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.CLK_80M, 12500, unit="ps").start())
    cocotb.start_soon(Clock(dut.CLK_40M, 25000, unit="ps").start())


async def _warmup_then_monitor(dut, frames, events, perrs):
    samples = stream_frames(frames, warmup_cells=WARMUP_CELLS)
    warm_n = WARMUP_CELLS * SAMPLES_PER_CELL
    await drive_samples(dut.CLK_80M, dut.clkline, samples[:warm_n])
    await ClockCycles(dut.CLK_40M, 2)
    cocotb.start_soon(monitor(dut, events, perrs))
    await drive_samples(dut.CLK_80M, dut.clkline, samples[warm_n:])
    await ClockCycles(dut.CLK_40M, 40)


@cocotb.test()
async def test_decode_mixed_frames(dut):
    """A TCLK (1-byte), an ACLK event (2-byte), and a full packet (12-byte) decode in
    order with correct id/data/is_tclk/data_valid."""
    _start_clocks(dut)
    await reset_dut(dut)

    frames = [
        [0x07],                                                            # TCLK event 0x07
        [0x9D, 0xD2],                                                      # ACLK event 0x9DD2
        [0x12, 0x34,                                                       # event 0x1234
         0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE, 0x00, 0x01,                   # data
         0x5A, 0xC3],                                                      # CRC, control (ignored)
    ]
    expected = [
        (0x0007, 1, 0, 0),
        (0x9DD2, 0, 0, 0),
        (0x1234, 0, 1, 0xDEADBEEFCAFE0001),
    ]

    events, perrs = [], []
    await _warmup_then_monitor(dut, frames, events, perrs)

    assert not perrs, f"unexpected parity_error on clean frames: {len(perrs)}"
    assert events == expected, f"decoded {[ (hex(e),t,d,hex(x)) for (e,t,d,x) in events]} != expected"
    dut._log.info(f"unified decode OK: {len(events)} frames (1/2/12-byte) decoded in order")


@cocotb.test()
async def test_parity_error(dut):
    """A 2-byte ACLK frame with a flipped parity bit raises parity_error and yields no
    event; good frames around it still decode."""
    _start_clocks(dut)
    await reset_dut(dut)

    frames = [
        [0x3C],                       # good TCLK
        ([0xAA, 0x55], 1),            # 2-byte frame, byte index 1 has bad parity
        [0x42],                       # good TCLK
    ]
    events, perrs = [], []
    await _warmup_then_monitor(dut, frames, events, perrs)

    ids = [e[0] for e in events]
    assert perrs, "bad-parity frame did not raise parity_error"
    assert 0xAA55 not in ids, f"bad-parity frame leaked an event: {[hex(i) for i in ids]}"
    assert 0x003C in ids and 0x0042 in ids, f"good frames lost: {[hex(i) for i in ids]}"
    dut._log.info("unified parity path OK: parity_error raised, good frames decoded")
