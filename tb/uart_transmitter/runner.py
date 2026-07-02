"""Cocotb 2.0 Python runner for the uart_transmitter testbench.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_uart_transmitter():
    run_cocotb(
        "uart_transmitter",
        sources=["rtl/uart_transmitter.sv"],
        hdl_toplevel="uart_transmitter",
        parameters={"CLOCK_FREQ": 100, "BAUD_RATE": 10},
    )


if __name__ == "__main__":
    test_uart_transmitter()
