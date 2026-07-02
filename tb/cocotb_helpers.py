"""Shared cocotb test helpers, importable from any suite because runner_common
puts tb/ on the simulator's PYTHONPATH."""
import cocotb
from cocotb.clock import Clock


def _b(sig) -> int:
    """Signal value as int; -1 while unresolved (x/z)."""
    try:
        return int(sig.value)
    except Exception:
        return -1


def start_clock(sig, period_ns=10):
    """Start a free-running clock on `sig` (pass the clock SIGNAL, e.g. dut.clk)."""
    cocotb.start_soon(Clock(sig, period_ns, unit="ns").start())
