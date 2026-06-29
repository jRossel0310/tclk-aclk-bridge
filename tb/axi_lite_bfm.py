"""Reusable hand-rolled AXI4-Lite master BFM for cocotb testbenches that expose
the standard s_axi_* signal names. Single outstanding transaction, valid/ready
handshakes. Used by the readout AXI testbenches to stand in for the PS software.

Optional signal-prefix support: pass pfx="s2_" to target a second slave whose
signals are named s2_axi_araddr etc. Default pfx="" keeps every existing call
working unchanged (resolves dut.s_axi_araddr etc. as before).
"""

from cocotb.triggers import RisingEdge, Timer


def _b(sig) -> int:
    try:
        return int(sig.value)
    except Exception:
        return -1


def _sig(dut, pfx, name):
    """Return getattr(dut, pfx + 's_axi_' + name)."""
    return getattr(dut, pfx + "s_axi_" + name)


async def axi_read(dut, addr, pfx=""):
    """Read one 32-bit word from byte address `addr`. Returns the data.

    pfx: optional signal-name prefix (e.g. 's2_') to select a second slave.
    Default pfx='' accesses the standard s_axi_* signals.
    """
    aclk = _sig(dut, pfx, "aclk")
    await RisingEdge(aclk)
    _sig(dut, pfx, "araddr").value = addr
    _sig(dut, pfx, "arvalid").value = 1
    _sig(dut, pfx, "rready").value = 0
    while True:                                  # wait until read data is presented
        await RisingEdge(aclk)
        await Timer(1, unit="ns")
        if _b(_sig(dut, pfx, "rvalid")) == 1:
            break
    val = int(_sig(dut, pfx, "rdata").value)
    _sig(dut, pfx, "arvalid").value = 0
    _sig(dut, pfx, "rready").value = 1           # accept the data beat
    await RisingEdge(aclk)
    await Timer(1, unit="ns")
    _sig(dut, pfx, "rready").value = 0
    return val


async def axi_write(dut, addr, data=0, pfx=""):
    """Write one 32-bit word to byte address `addr`.

    pfx: optional signal-name prefix (e.g. 's2_') to select a second slave.
    Default pfx='' accesses the standard s_axi_* signals.
    """
    aclk = _sig(dut, pfx, "aclk")
    await RisingEdge(aclk)
    _sig(dut, pfx, "awaddr").value = addr
    _sig(dut, pfx, "awvalid").value = 1
    _sig(dut, pfx, "wdata").value = data
    _sig(dut, pfx, "wstrb").value = 0xF
    _sig(dut, pfx, "wvalid").value = 1
    _sig(dut, pfx, "bready").value = 0
    while True:                                  # wait for the write response
        await RisingEdge(aclk)
        await Timer(1, unit="ns")
        if _b(_sig(dut, pfx, "bvalid")) == 1:
            break
    _sig(dut, pfx, "awvalid").value = 0
    _sig(dut, pfx, "wvalid").value = 0
    _sig(dut, pfx, "bready").value = 1
    await RisingEdge(aclk)
    await Timer(1, unit="ns")
    _sig(dut, pfx, "bready").value = 0
