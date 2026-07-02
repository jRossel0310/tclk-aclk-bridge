"""Cocotb 2.0 runner for uart_receiver (shared plumbing: tb/runner_common.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_uart_receiver():
    run_cocotb(
        "uart_receiver",
        sources=["rtl/uart_receiver.sv"],
        hdl_toplevel="uart_receiver",
        parameters={"CLOCK_FREQ": 100, "BAUD_RATE": 10},
    )


if __name__ == "__main__":
    test_uart_receiver()
