"""Cocotb 2.0 Python runner for the uart_echo_top testbench.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_uart_echo_top():
    run_cocotb(
        "uart_echo_top",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/uart_receiver.sv",
            "rtl/uart_transmitter.sv",
            "rtl/fifo.sv",
            "rtl/uart_echo_top.sv",
        ],
        hdl_toplevel="uart_echo_top",
        parameters={"CLOCK_FREQ": 100, "BAUD_RATE": 10, "FIFO_DEPTH": 8},
    )


if __name__ == "__main__":
    test_uart_echo_top()
