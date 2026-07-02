"""Cocotb 2.0 Python runner for the edge_detector testbench.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_edge_detector():
    run_cocotb("edge_detector", sources=["rtl/edge_detector.sv"], hdl_toplevel="edge_detector")


if __name__ == "__main__":
    test_edge_detector()
