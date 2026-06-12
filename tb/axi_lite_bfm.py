"""Reusable hand-rolled AXI4-Lite master BFM for cocotb testbenches that expose
the standard s_axi_* signal names. Single outstanding transaction, valid/ready
handshakes. Used by the readout AXI testbenches to stand in for the PS software.
"""

from cocotb.triggers import RisingEdge, Timer


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


async def axi_read(dut, addr):
    """Read one 32-bit word from byte address `addr`. Returns the data."""
    await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_araddr.value = addr
    dut.s_axi_arvalid.value = 1
    dut.s_axi_rready.value = 0
    while True:                                  # wait until read data is presented
        await RisingEdge(dut.s_axi_aclk)
        await Timer(1, unit="ns")
        if _b(dut.s_axi_rvalid) == 1:
            break
    val = int(dut.s_axi_rdata.value)
    dut.s_axi_arvalid.value = 0
    dut.s_axi_rready.value = 1                    # accept the data beat
    await RisingEdge(dut.s_axi_aclk)
    await Timer(1, unit="ns")
    dut.s_axi_rready.value = 0
    return val


async def axi_write(dut, addr, data=0):
    """Write one 32-bit word to byte address `addr`."""
    await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_awaddr.value = addr
    dut.s_axi_awvalid.value = 1
    dut.s_axi_wdata.value = data
    dut.s_axi_wstrb.value = 0xF
    dut.s_axi_wvalid.value = 1
    dut.s_axi_bready.value = 0
    while True:                                  # wait for the write response
        await RisingEdge(dut.s_axi_aclk)
        await Timer(1, unit="ns")
        if _b(dut.s_axi_bvalid) == 1:
            break
    dut.s_axi_awvalid.value = 0
    dut.s_axi_wvalid.value = 0
    dut.s_axi_bready.value = 1
    await RisingEdge(dut.s_axi_aclk)
    await Timer(1, unit="ns")
    dut.s_axi_bready.value = 0
