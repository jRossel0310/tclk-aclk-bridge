import os, sys
from pathlib import Path
from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "icarus")
TB_DIR = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR = PROJ_DIR / "rtl"
BUILD = PROJ_DIR / "sim_build" / "aclkgt_readout"
sys.path.insert(0, str(TB_DIR))
sys.path.insert(0, str(TB_DIR.parent))
_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")

def test_aclkgt_readout():
    runner = get_runner(SIM)
    runner.build(
        sources=[
            RTL_DIR / "aclk_bridge" / "crc8_calc.v",
            RTL_DIR / "aclk_bridge" / "GEARBOX_16_TO_96.v",
            RTL_DIR / "aclk_bridge" / "ACLK_REV.v",
            RTL_DIR / "synchronizer.sv",
            RTL_DIR / "async_fifo.sv",
            RTL_DIR / "cdc_gray_count.sv",
            RTL_DIR / "aclk_readout" / "aclk_readout_core.sv",
            RTL_DIR / "aclk_readout" / "aclk_readout_axi.sv",
            RTL_DIR / "aclk_gt" / "aclk_gt_readout_top.sv",
            TB_DIR / "tb_aclkgt_readout_top.sv",
        ],
        hdl_toplevel="tb_aclkgt_readout_top",
        build_dir=BUILD, timescale=("1ns", "1ps"), waves=True, always=True,
    )
    runner.test(hdl_toplevel="tb_aclkgt_readout_top",
                test_module="test_aclkgt_readout", build_dir=BUILD, waves=True)

if __name__ == "__main__":
    test_aclkgt_readout()
