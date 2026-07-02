"""Cocotb 2.0 runner for rtl/aclk_lite/aclk_lite_encoder.sv (biphase-mark encoder).
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_lite_encoder():
    run_cocotb(
        "aclk_lite_encoder",
        sources=["rtl/aclk_lite/aclk_lite_encoder.sv"],
        hdl_toplevel="aclk_lite_encoder",
    )


if __name__ == "__main__":
    test_aclk_lite_encoder()
