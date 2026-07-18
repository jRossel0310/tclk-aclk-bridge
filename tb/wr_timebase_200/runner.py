"""Cocotb 2.0 runner for the 200 MHz TCLK wr_timebase constants."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_wr_timebase_200():
    run_cocotb(
        "wr_timebase_200",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/cdc_word_pulse.sv",
            "rtl/wr_timebase.sv",
            "tb/wr_timebase_200/tb_wr_timebase_200_top.sv",
        ],
        hdl_toplevel="tb_wr_timebase_200_top",
    )


if __name__ == "__main__":
    test_wr_timebase_200()
