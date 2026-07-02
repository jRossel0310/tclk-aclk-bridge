"""Cocotb 2.0 runner for the counter smoke test (shared plumbing: tb/runner_common.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tb/ for runner_common
from runner_common import run_cocotb


def test_counter():
    run_cocotb("counter", sources=["rtl/counter.sv"], hdl_toplevel="counter")


if __name__ == "__main__":
    test_counter()
