"""Cocotb 2.0 runner for rtl/cdc_word_pulse.sv (shared plumbing: tb/runner_common.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_cdc_word_pulse():
    run_cocotb(
        "cdc_word_pulse",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/cdc_word_pulse.sv",
        ],
        hdl_toplevel="cdc_word_pulse",
    )


if __name__ == "__main__":
    test_cdc_word_pulse()
