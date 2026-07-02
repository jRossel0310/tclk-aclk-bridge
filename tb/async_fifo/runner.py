"""Cocotb 2.0 Python runner for the async_fifo testbench.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_async_fifo():
    run_cocotb(
        "async_fifo",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/async_fifo.sv",
        ],
        hdl_toplevel="async_fifo",
    )


if __name__ == "__main__":
    test_async_fifo()
