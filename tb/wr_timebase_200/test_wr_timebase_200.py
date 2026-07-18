"""The 200 MHz TCLK wr_timebase constants: strict-zero before arm, lock at PPS,
ns interpolates on a 5 ns clock, and 10 MHz loss unlocks within CLK10_TIMEOUT.
Sim second = 5000 ns (50 cells * 100 ns), per tb/wr_model.py."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer
from cocotb.utils import get_sim_time

from cocotb_helpers import _b
from wr_model import WrGen

SEC0 = 1_751_800_000
SIM_NS_PER_SEC = 5000


def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.clk, 5000, unit="ps").start())     # 200 MHz
    cocotb.start_soon(Clock(dut.cfg_clk, 10, unit="ns").start())


async def _reset(dut):
    dut.rstn.value = 0; dut.cfg_rstn.value = 0
    dut.cfg_valid.value = 0; dut.cfg_disarm.value = 0; dut.cfg_sec.value = 0
    dut.wr_clk10.value = 0; dut.wr_pps.value = 0
    await ClockCycles(dut.cfg_clk, 10); await Timer(1, unit="ns")
    dut.rstn.value = 1; dut.cfg_rstn.value = 1
    await ClockCycles(dut.cfg_clk, 10)


async def _arm(dut, sec):
    await RisingEdge(dut.cfg_clk)
    dut.cfg_sec.value = sec; dut.cfg_disarm.value = 0; dut.cfg_valid.value = 1
    await RisingEdge(dut.cfg_clk)
    dut.cfg_valid.value = 0


async def _wait_locked(dut, timeout_ns=3 * SIM_NS_PER_SEC):
    for _ in range(timeout_ns // 5):
        await RisingEdge(dut.clk); await Timer(1, unit="ns")
        if _b(dut.locked) == 1:
            return
    raise AssertionError("timebase never locked after arm")


@cocotb.test()
async def test_lock_and_track_200mhz(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps); gen.start()

    # strict zero before arm. Loop long enough to clear a full 5000 ns WR
    # second (>1 real PPS-to-PPS interval) so cells_last reflects a genuine
    # closed interval rather than the short artificial boundary before the
    # first modeled PPS (40 iters * 40 clk * 5 ns = 8000 ns; cf. the 40 MHz
    # suite's 30 * 20 clk * 25 ns = 15000 ns, same >1-second margin).
    for _ in range(40):
        await ClockCycles(dut.clk, 40); await Timer(1, unit="ns")
        assert _b(dut.ts) == 0 and _b(dut.locked) == 0, "ts/locked nonzero before arm"
    assert _b(dut.cells_last) == 50, f"cells_last {_b(dut.cells_last)} != 50 (10 MHz miscount)"

    # arm and lock
    await _arm(dut, SEC0); await _wait_locked(dut)
    sec = (_b(dut.ts) >> 32) & 0xFFFFFFFF
    ns = _b(dut.ts) & 0xFFFFFFFF
    assert sec == SEC0, f"sec {sec} != {SEC0}"
    assert ns < SIM_NS_PER_SEC, f"ns {ns} out of range"

    # ns interpolates and tracks wall time within a sync/2-FF margin
    for i in range(120):
        await ClockCycles(dut.clk, 13 + (i % 7)); await Timer(1, unit="ns")
        expected = get_sim_time(unit="ns") - gen.pps_times_ns[-1]
        ns = _b(dut.ts) & 0xFFFFFFFF
        if expected > 200:
            assert ns <= expected + 50,  f"ns {ns} leads wall {expected}"
            assert ns >= expected - 250, f"ns {ns} lags wall {expected}"
    gen.stop()


@cocotb.test()
async def test_clk10_loss_unlocks_200mhz(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps); gen.start()
    await ClockCycles(dut.clk, 1500)
    await _arm(dut, SEC0); await _wait_locked(dut)

    # 10 MHz dies: CLK10_TIMEOUT=80 clk = 400 ns; unlock well within 200 clk
    gen.clk10_on = False; dut.wr_clk10.value = 0
    await ClockCycles(dut.clk, 200); await Timer(1, unit="ns")
    assert _b(dut.locked) == 0 and _b(dut.ts) == 0, "clk10 loss did not unlock"
    gen.stop()
