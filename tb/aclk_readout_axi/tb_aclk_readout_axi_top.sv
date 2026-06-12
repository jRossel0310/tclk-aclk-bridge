// tb/aclk_readout_axi/tb_aclk_readout_axi_top.sv
//
// Full hardware-shaped test top: the real ACLK_RCV decoder feeding
// aclk_readout_axi (timestamping packer + FIFO + AXI4-Lite slave). This is the
// configuration Phase B synthesizes for the KR260. The receive-side ports keep
// ACLK_RCV's names so the shared TX model drives them; the AXI-Lite slave is
// exercised by a cocotb master on the s_axi_aclk clock.

`timescale 1ns / 1ps

module tb_aclk_readout_axi_top (
    // receive-side word stream (recovered-RX domain)
    input  logic        CLK1,
    input  logic        RESETn,
    input  logic [15:0] DATA_FROM_XCVR,
    input  logic [1:0]  K_FROM_XCVR,

    // AXI4-Lite slave (PS clock)
    input  logic        s_axi_aclk,
    input  logic        s_axi_aresetn,
    input  logic [7:0]  s_axi_awaddr,
    input  logic        s_axi_awvalid,
    output logic        s_axi_awready,
    input  logic [31:0] s_axi_wdata,
    input  logic [3:0]  s_axi_wstrb,
    input  logic        s_axi_wvalid,
    output logic        s_axi_wready,
    output logic [1:0]  s_axi_bresp,
    output logic        s_axi_bvalid,
    input  logic        s_axi_bready,
    input  logic [7:0]  s_axi_araddr,
    input  logic        s_axi_arvalid,
    output logic        s_axi_arready,
    output logic [31:0] s_axi_rdata,
    output logic [1:0]  s_axi_rresp,
    output logic        s_axi_rvalid,
    input  logic        s_axi_rready,

    // exposed for the rx-domain monitor / plot
    output logic        aclk_valid,
    output logic        dropped_null,
    output logic        rx_aligned
);

    logic [15:0] aclk_event;
    logic [63:0] aclk_data;
    logic        aclk_error;
    logic [3:0]  diag;

    ACLK_RCV u_rcv (
        .RESETn         (RESETn),
        .CLK1           (CLK1),
        .DATA_FROM_XCVR (DATA_FROM_XCVR),
        .K_FROM_XCVR    (K_FROM_XCVR),
        .ACLK_EVENT     (aclk_event),
        .ACLK_DATA      (aclk_data),
        .ACLK_VALID     (aclk_valid),
        .ACLK_ERROR     (aclk_error),
        .RX_ALIGNED_OUT (rx_aligned),
        .DIAG           (diag)
    );

    aclk_readout_axi #(.ADDR_WIDTH(6), .AXI_ADDR_W(8)) u_axi (
        .rx_clk        (CLK1),
        .rx_rstn       (RESETn),
        .pps           (1'b0),
        .aclk_valid    (aclk_valid),
        .aclk_event    (aclk_event),
        .aclk_data     (aclk_data),
        .flags         (16'h0001),            // ACLK_RCV events always carry 64-bit data
        .aclk_error    (aclk_error),
        .dropped_null  (dropped_null),
        .dbg_word      (32'd0),

        .s_axi_aclk    (s_axi_aclk),
        .s_axi_aresetn (s_axi_aresetn),
        .s_axi_awaddr  (s_axi_awaddr),
        .s_axi_awvalid (s_axi_awvalid),
        .s_axi_awready (s_axi_awready),
        .s_axi_wdata   (s_axi_wdata),
        .s_axi_wstrb   (s_axi_wstrb),
        .s_axi_wvalid  (s_axi_wvalid),
        .s_axi_wready  (s_axi_wready),
        .s_axi_bresp   (s_axi_bresp),
        .s_axi_bvalid  (s_axi_bvalid),
        .s_axi_bready  (s_axi_bready),
        .s_axi_araddr  (s_axi_araddr),
        .s_axi_arvalid (s_axi_arvalid),
        .s_axi_arready (s_axi_arready),
        .s_axi_rdata   (s_axi_rdata),
        .s_axi_rresp   (s_axi_rresp),
        .s_axi_rvalid  (s_axi_rvalid),
        .s_axi_rready  (s_axi_rready)
    );

endmodule
