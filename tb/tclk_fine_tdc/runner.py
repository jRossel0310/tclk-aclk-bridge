import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_tclk_fine_decode():
    run_cocotb("tclk_fine_tdc",
               sources=["rtl/aclk_lite/tclk_fine_decode.sv"],
               hdl_toplevel="tclk_fine_decode",
               test_module="test_tclk_fine_decode")


def test_tclk_fine_tdc():
    run_cocotb("tclk_fine_tdc",
               sources=["rtl/synchronizer.sv",
                        "rtl/aclk_lite/tclk_fine_decode.sv",
                        "rtl/aclk_lite/tclk_fine_tdc.sv"],
               hdl_toplevel="tclk_fine_tdc",
               test_module="test_tclk_fine_tdc")


if __name__ == "__main__":
    test_tclk_fine_decode()
    test_tclk_fine_tdc()
