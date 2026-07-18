"""Cocotb tests for the inherited TCLK receiver (rtl/aclk_bridge/TCLK_RCV.v),
validated against the documented Fermilab TCLK biphase-mark format. The
tb/tclk_tx_model.py model drives the TCLK line; the test checks that known event
codes decode (DATA + DAVn) and that a bad-parity frame raises PERR.

CLK_80M = 80 MHz (8x oversample of the 10 MHz TCLK), CLK_40M = 40 MHz, TCLK_RATE
= 1 (10 MHz mode). The receiver emits one spurious PERR while serdec first locks
to the carrier, so the warm-up is driven with PERR_CLR held and monitoring only
begins once the link is settled (a real receiver clears PERR after init too).
"""

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from tclk_tx_model import stream_samples, drive_samples, add_ringing, SAMPLES_PER_CELL
from cocotb_helpers import _b

CLK80_PERIOD_PS = 100_000 // SAMPLES_PER_CELL     # OSR*10 MHz  (12500 at OSR=8)
# DECOUPLED: default is the old 2:1 CLK80:CLK40 relationship (CLK40 derived from
# CLK80); override with TCLK_CLK40_PS to prove the deserializer/timestamp clock
# independently, e.g. TCLK_CLK40_PS=5000 for the 200 MHz board build.
CLK40_PERIOD_PS = int(os.getenv("TCLK_CLK40_PS", str(200_000 // SAMPLES_PER_CELL)))
WARMUP_CELLS = 40


async def reset_dut(dut):
    dut.TCLK.value = 1                  # idle high
    dut.TCLK_RATE.value = 1             # 10 MHz mode
    dut.PERR_CLR.value = 0
    dut.SIG_ERR_CLR.value = 0
    dut.RESETn.value = 0
    await ClockCycles(dut.CLK_80M, 10)
    await Timer(1, unit="ns")
    dut.RESETn.value = 1
    await ClockCycles(dut.CLK_80M, 10)


async def monitor(dut, captured, perrs):
    """Capture DATA on each DAVn strobe (active low) and note PERR rising."""
    prev_perr = 0
    while True:
        await RisingEdge(dut.CLK_40M)
        await Timer(1, unit="ns")
        if _b(dut.DAVn) == 0:
            captured.append(_b(dut.DATA))
        p = _b(dut.PERR)
        if p == 1 and prev_perr == 0:
            perrs.append(True)
        prev_perr = p


def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.CLK_80M, CLK80_PERIOD_PS, unit="ps").start())
    cocotb.start_soon(Clock(dut.CLK_40M, CLK40_PERIOD_PS, unit="ps").start())


def _ring_every_edge(samples, width):
    """Apply add_ringing at every real transition in `samples`, independently and
    bounded to just that transition's own window.

    add_ringing's own scan re-triggers on the artificial seam it creates (forced-old
    sample followed by a natural sample at the new level look like another
    transition to its `out[i] != out[i-1]` check), so handing it a whole multi-cell
    stream chops every long run into a repeating [new, old*width] pattern for the
    run's entire length, not just "right after" the edge. On real biphase runs
    (>= HALF samples between edges, always > width) that never lets the debounce see
    a run long enough to accept -- decode freezes at the idle level. Slicing to
    exactly [edge-1 : edge+1+width] before calling add_ringing gives it just one
    transition to see, so its internal `i` counter runs off the end of the slice
    after the single forced region and never re-triggers: exactly the one-bounce-
    then-settles behavior add_ringing's own docstring describes. add_ringing itself
    is untouched; this only controls how much of the stream it sees at a time.

    A leading virtual idle-high sample (matching TCLK's reset/idle level, see
    reset_dut and stream_samples' default level=1) is prepended before scanning so
    the very first real transition -- which otherwise has no k-1 sample in `samples`
    to compare against and would silently go un-rung -- gets the same treatment as
    every other edge. Without it, only that first edge's gap to the next one comes
    out ~width+1 samples short relative to the clean stream, which desyncs the
    decode FSM's phase just enough to flip a bit deep in the first frame."""
    out = [1] + list(samples)
    n = len(out)
    k = 1
    while k < n:
        if out[k] != out[k - 1]:
            end = min(k + 1 + width, n)
            out[k - 1:end] = add_ringing(out[k - 1:end], width)
            k = end + 1      # past the one artificial seam this creates
        else:
            k += 1
    return out[1:]


async def _warmup_then_monitor(dut, events, captured, perrs):
    """Drive the idle warm-up with PERR_CLR held (to swallow the serdec lock
    transient), then start monitoring and drive the events as one continuous
    biphase stream (the slice keeps the line level continuous)."""
    samples = stream_samples(events, warmup_cells=WARMUP_CELLS)
    warm_n = WARMUP_CELLS * SAMPLES_PER_CELL

    dut.PERR_CLR.value = 1
    await drive_samples(dut.CLK_80M, dut.TCLK, samples[:warm_n])
    dut.PERR_CLR.value = 0
    await ClockCycles(dut.CLK_40M, 2)

    cocotb.start_soon(monitor(dut, captured, perrs))
    await drive_samples(dut.CLK_80M, dut.TCLK, samples[warm_n:])
    await ClockCycles(dut.CLK_40M, 30)


@cocotb.test()
async def test_decode_known_events(dut):
    """A sequence of known TCLK event codes decodes back in order, no parity
    errors. 0x9D and 0xD2 are the two events from Fig. 1 of the TCLK paper."""
    _start_clocks(dut)
    await reset_dut(dut)

    events = [0x9D, 0xD2, 0x00, 0x07, 0x0F, 0xA5, 0x29]
    captured, perrs = [], []
    await _warmup_then_monitor(dut, events, captured, perrs)

    assert not perrs, f"unexpected PERR on clean frames: {len(perrs)}"
    assert captured == events, (
        f"decoded {[f'0x{x:02X}' for x in captured]} != "
        f"sent {[f'0x{x:02X}' for x in events]}"
    )
    dut._log.info(f"TCLK decode OK: {len(captured)} events decoded in order: "
                  f"{[f'0x{x:02X}' for x in captured]}")


@cocotb.test()
async def test_parity_error(dut):
    """A frame with a flipped parity bit must raise PERR and not produce DAVn."""
    _start_clocks(dut)
    await reset_dut(dut)

    # one good event, then a bad-parity event, then a good one
    events = [0x3C, (0x55, True), 0x42]
    captured, perrs = [], []
    await _warmup_then_monitor(dut, events, captured, perrs)

    assert perrs, "bad-parity frame did not raise PERR"
    assert 0x55 not in captured, f"bad-parity frame leaked into DATA: {captured}"
    assert 0x3C in captured and 0x42 in captured, \
        f"good frames around the bad one were lost: {[f'0x{x:02X}' for x in captured]}"
    dut._log.info(f"TCLK parity path OK: PERR raised, good frames {[f'0x{x:02X}' for x in captured]}")


@cocotb.test()
async def test_decode_survives_ringing(dut):
    """Real-line ringing injected after every transition (sub-DB-window spikes)
    must be rejected by the serdec debounce, leaving decode clean. Only meaningful
    at OSR>8, where the debounce is active (DB=0, i.e. pass-through, at OSR<=8 -
    driving ringing there would legitimately corrupt decode, so this test is N/A)."""
    if SAMPLES_PER_CELL <= 8:
        dut._log.info("debounce is pass-through, N/A")
        return

    _start_clocks(dut)
    await reset_dut(dut)

    events = [0x9D, 0xD2, 0x00, 0x07, 0x0F, 0xA5, 0x29]
    samples = stream_samples(events, warmup_cells=WARMUP_CELLS)
    warm_n = WARMUP_CELLS * SAMPLES_PER_CELL
    # Ring only the actual event data, not the idle warm-up carrier: warm-up's job is
    # letting serdec lock cleanly on a plain carrier (the module docstring's "one
    # spurious PERR while first locks", already fully handled by holding PERR_CLR for
    # the whole warm-up - see _warmup_then_monitor); perturbing that lock-on process
    # itself isn't the bug under test here. Every warm-up cell is a "1" bit, which
    # nets zero level change (two toggles), so samples[warm_n-1] == 1, matching
    # _ring_every_edge's own leading-sample assumption for the slice below.
    # width=3 samples = 7.5 ns at OSR=40, well under DB -> must be rejected at every
    # single edge in the event stream (see _ring_every_edge for why it's per-edge).
    samples = samples[:warm_n] + _ring_every_edge(samples[warm_n:], width=3)

    captured, perrs = [], []
    dut.PERR_CLR.value = 1
    await drive_samples(dut.CLK_80M, dut.TCLK, samples[:warm_n])
    dut.PERR_CLR.value = 0
    await ClockCycles(dut.CLK_40M, 2)

    cocotb.start_soon(monitor(dut, captured, perrs))
    await drive_samples(dut.CLK_80M, dut.TCLK, samples[warm_n:])
    await ClockCycles(dut.CLK_40M, 30)

    assert not perrs, f"unexpected PERR under injected ringing: {len(perrs)}"
    assert captured == events, (
        f"decoded {[f'0x{x:02X}' for x in captured]} != "
        f"sent {[f'0x{x:02X}' for x in events]} under injected ringing"
    )
    dut._log.info(f"TCLK decode survives ringing (width=3 < DB): {len(captured)} events "
                  f"decoded in order: {[f'0x{x:02X}' for x in captured]}")
