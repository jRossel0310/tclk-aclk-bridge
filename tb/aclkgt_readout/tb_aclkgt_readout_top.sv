`timescale 1ns/1ps
module tb_aclkgt_readout_top (
    input  wire        CLK1,
    input  wire        rx_rstn,
    input  wire        pps,
    input  wire [15:0] DATA_FROM_XCVR,
    input  wire [1:0]  K_FROM_XCVR,
    input  wire        mmcm_locked,
    output wire        rx_aligned,
    output wire        aclk_valid,
    output wire        dropped_null,
    input  wire        s_axi_aclk,
    input  wire        s_axi_aresetn,
    input  wire [7:0]  s_axi_awaddr,  input wire s_axi_awvalid, output wire s_axi_awready,
    input  wire [31:0] s_axi_wdata,   input wire [3:0] s_axi_wstrb, input wire s_axi_wvalid, output wire s_axi_wready,
    output wire [1:0]  s_axi_bresp,   output wire s_axi_bvalid,  input wire s_axi_bready,
    input  wire [7:0]  s_axi_araddr,  input wire s_axi_arvalid,  output wire s_axi_arready,
    output wire [31:0] s_axi_rdata,   output wire [1:0] s_axi_rresp, output wire s_axi_rvalid, input wire s_axi_rready
);
    aclk_gt_readout_top #(.ADDR_WIDTH(6), .AXI_ADDR_W(8)) dut (
        .rx_clk(CLK1), .rx_rstn(rx_rstn), .pps(pps),
        .data_from_xcvr(DATA_FROM_XCVR), .k_from_xcvr(K_FROM_XCVR),
        .mmcm_locked(mmcm_locked), .dbg_word_in(32'b0), .rx_aligned(rx_aligned),
        .dbg_event_valid(aclk_valid), .dbg_hb(), .dropped_null(dropped_null),
        .s_axi_aclk(s_axi_aclk), .s_axi_aresetn(s_axi_aresetn),
        .s_axi_awaddr(s_axi_awaddr), .s_axi_awvalid(s_axi_awvalid), .s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata), .s_axi_wstrb(s_axi_wstrb), .s_axi_wvalid(s_axi_wvalid), .s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp), .s_axi_bvalid(s_axi_bvalid), .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr), .s_axi_arvalid(s_axi_arvalid), .s_axi_arready(s_axi_arready),
        .s_axi_rdata(s_axi_rdata), .s_axi_rresp(s_axi_rresp), .s_axi_rvalid(s_axi_rvalid), .s_axi_rready(s_axi_rready)
    );
endmodule
