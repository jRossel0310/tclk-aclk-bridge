// tb/aclk_readout_ext_ts/tb_ext_ts_top.sv
//
// Testbench top for the external-timestamp (USE_EXT_TS=1) path.
// Instantiates aclk_readout_axi with USE_EXT_TS=1 and DROP_NULL=0.
// The event stream (aclk_valid/aclk_event/aclk_data/flags) is driven
// directly by cocotb; no decoder sits above this block.

`timescale 1ns / 1ps

module tb_ext_ts_top (
    // RX domain
    input  logic        rx_clk,
    input  logic        rx_rstn,

    // Event inputs (driven directly by cocotb)
    input  logic        aclk_valid,
    input  logic [15:0] aclk_event,
    input  logic [63:0] aclk_data,
    input  logic [15:0] flags,

    // External shared timestamp
    input  logic [63:0] ts_ext,

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
    input  logic        s_axi_rready
);

    aclk_readout_axi #(
        .ADDR_WIDTH (6),
        .AXI_ADDR_W (8),
        .DROP_NULL  (1'b0),
        .USE_EXT_TS (1'b1)
    ) u_axi (
        .rx_clk        (rx_clk),
        .rx_rstn       (rx_rstn),
        .pps           (1'b0),
        .aclk_valid    (aclk_valid),
        .aclk_event    (aclk_event),
        .aclk_data     (aclk_data),
        .flags         (flags),
        .aclk_error    (1'b0),
        .dropped_null  (),
        .dbg_word      (32'd0),
        .mmcm_locked   (1'b1),
        .dbg_hb        (),
        .gt_ctrl       (),
        .ts_ext        (ts_ext),

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
