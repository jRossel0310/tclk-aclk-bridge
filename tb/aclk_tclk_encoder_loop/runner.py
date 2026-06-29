import os, sys
from pathlib import Path
from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")
TB_DIR = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR = PROJ_DIR / "rtl"
BUILD = PROJ_DIR / "sim_build" / "aclk_tclk_encoder_loop"
sys.path.insert(0, str(TB_DIR)); sys.path.insert(0, str(TB_DIR.parent))
_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")


def test_aclk_tclk_encoder_loop():
    runner = get_runner(SIM)
    runner.build(
        sources=[
            RTL_DIR / "aclk_bridge" / "crc8_calc.v",   # file is lowercase; module is CRC8_CALC
            RTL_DIR / "aclk_bridge" / "gearbox_96_to_16.v",
            RTL_DIR / "aclk_bridge" / "GEARBOX_16_TO_96.v",
            RTL_DIR / "aclk_bridge" / "ACLK_REV.v",
            RTL_DIR / "aclk_gt" / "aclk_tclk_encoder.v",
            TB_DIR / "tb_aclk_tclk_encoder_loop_top.sv",
        ],
        hdl_toplevel="tb_aclk_tclk_encoder_loop_top",
        build_dir=BUILD, timescale=("1ns", "1ps"), waves=True, always=True,
    )
    runner.test(hdl_toplevel="tb_aclk_tclk_encoder_loop_top",
                test_module="test_aclk_tclk_encoder_loop", build_dir=BUILD, waves=True)


if __name__ == "__main__":
    test_aclk_tclk_encoder_loop()
