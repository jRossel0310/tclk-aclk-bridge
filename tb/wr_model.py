"""White Rabbit stimulus model: a 10 MHz cell clock and a phase-aligned PPS.

The 'second' is SHORTENED for simulation: every `cells_per_second` wr_clk10
rising edges (default 50, i.e. 5000 ns) one PPS pulse rises aligned with the
cell edge and stays high for `pps_high_cells` cells. DUT watchdog parameters
are scaled down to match (see the suite runners).

Gates model line faults: set `clk10_on` / `pps_on` False (and drive the signal
low from the test) to simulate a dead line; set back True to restore it.
"""
import cocotb
from cocotb.triggers import Timer
from cocotb.utils import get_sim_time


class WrGen:
    CELL_NS = 100          # 10 MHz: one cell per 100 ns

    def __init__(self, clk10_sig, pps_sig, cells_per_second=50, pps_high_cells=5):
        self.clk10 = clk10_sig
        self.pps = pps_sig
        self.cps = cells_per_second
        self.high = pps_high_cells
        self.clk10_on = True
        self.pps_on = True
        self.pps_times_ns = []
        self._task = None

    def start(self):
        self.clk10.value = 0
        self.pps.value = 0
        self._task = cocotb.start_soon(self._drive())

    def stop(self):
        if self._task is not None:
            self._task.cancel()   # cocotb 2.0: kill() is deprecated
            self._task = None
        self.clk10.value = 0
        self.pps.value = 0

    async def _drive(self):
        cell = 0
        while True:
            if cell == 0 and self.pps_on:
                self.pps.value = 1
                self.pps_times_ns.append(get_sim_time(unit="ns"))
            if cell == self.high:
                self.pps.value = 0
            if self.clk10_on:
                self.clk10.value = 1
            await Timer(self.CELL_NS // 2, unit="ns")
            if self.clk10_on:
                self.clk10.value = 0
            await Timer(self.CELL_NS // 2, unit="ns")
            cell = (cell + 1) % self.cps
