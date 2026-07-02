"""Cocotb 2.0 Python runner for the fifo testbench.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_fifo():
    run_cocotb(
        "fifo",
        sources=["rtl/fifo.sv"],
        hdl_toplevel="fifo",
        parameters={"WIDTH": 8, "DEPTH": 4},
    )


if __name__ == "__main__":
    test_fifo()
