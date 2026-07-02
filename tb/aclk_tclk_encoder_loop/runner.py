"""Cocotb 2.0 runner for the aclk_tclk_encoder -> ACLK_RCV loopback sim.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_tclk_encoder_loop():
    run_cocotb(
        "aclk_tclk_encoder_loop",
        sources=[
            "rtl/aclk_bridge/crc8_calc.v",   # file is lowercase; module is CRC8_CALC
            "rtl/aclk_bridge/gearbox_96_to_16.v",
            "rtl/aclk_bridge/GEARBOX_16_TO_96.v",
            "rtl/aclk_bridge/ACLK_REV.v",
            "rtl/aclk_gt/aclk_tclk_encoder.v",
            "tb/aclk_tclk_encoder_loop/tb_aclk_tclk_encoder_loop_top.sv",
        ],
        hdl_toplevel="tb_aclk_tclk_encoder_loop_top",
    )


if __name__ == "__main__":
    test_aclk_tclk_encoder_loop()
