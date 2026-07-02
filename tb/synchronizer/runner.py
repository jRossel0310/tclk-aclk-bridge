"""Cocotb 2.0 Python runner for the synchronizer testbench.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_synchronizer():
    run_cocotb("synchronizer", sources=["rtl/synchronizer.sv"], hdl_toplevel="synchronizer")


if __name__ == "__main__":
    test_synchronizer()
