// rtl/aclk_lite/aclk_lite_readout_top.sv
//
// The PL readout block for the KR260: the Manchester ACLK-Lite decoder (ADM)
// wired to the AXI-Lite readout. This is what Phase B synthesizes (the on-board
// Manchester stimulus generator feeds `line`; the PS reads events over AXI).
//
// Adapter between the decoder and the readout:
//   - aclk_valid = event_valid (one strobe per decoded event)
//   - aclk_data  = data only when the event carried a 64-bit payload, else 0
//   - flags      = { ..., is_tclk, has_data }, so the PS knows whether DATA is
//                  meaningful and whether this was a legacy 8-bit TCLK event
//   - aclk_error = parity_error (bad/malformed frame), counted by the readout

`timescale 1ns / 1ps

module aclk_lite_readout_top #(
    parameter int OVERSAMPLE = 16,         // decoder oversampling cycles per bit
    parameter int ADDR_WIDTH = 6,          // FIFO depth = 2**ADDR_WIDTH
    parameter int AXI_ADDR_W = 8
) (
    // ---- recovered-RX / oversampling domain ----
    input  logic        rx_clk,
    input  logic        rx_rstn,
    input  logic        pps,
    input  logic        line,              // Manchester serial input

    // ---- AXI4-Lite slave (PS clock) ----
    input  logic                   s_axi_aclk,
    input  logic                   s_axi_aresetn,
    input  logic [AXI_ADDR_W-1:0]  s_axi_awaddr,
    input  logic                   s_axi_awvalid,
    output logic                   s_axi_awready,
    input  logic [31:0]            s_axi_wdata,
    input  logic [3:0]             s_axi_wstrb,
    input  logic                   s_axi_wvalid,
    output logic                   s_axi_wready,
    output logic [1:0]             s_axi_bresp,
    output logic                   s_axi_bvalid,
    input  logic                   s_axi_bready,
    input  logic [AXI_ADDR_W-1:0]  s_axi_araddr,
    input  logic                   s_axi_arvalid,
    output logic                   s_axi_arready,
    output logic [31:0]            s_axi_rdata,
    output logic [1:0]             s_axi_rresp,
    output logic                   s_axi_rvalid,
    input  logic                   s_axi_rready,

    // ---- debug (rx domain) ----
    output logic        dbg_event_valid,
    output logic        dbg_data_valid,
    output logic        dbg_is_tclk,
    output logic        dropped_null
);

    // ---- decoder ----
    logic        event_valid, data_valid, parity_error, is_tclk;
    logic [15:0] event_id;
    logic [63:0] data;

    aclk_lite_decoder #(.OVERSAMPLE(OVERSAMPLE)) u_dec (
        .clk          (rx_clk),
        .rstn         (rx_rstn),
        .line         (line),
        .event_valid  (event_valid),
        .event_id     (event_id),
        .data_valid   (data_valid),
        .data         (data),
        .parity_error (parity_error),
        .is_tclk      (is_tclk)
    );

    // ---- adapter: decoder outputs -> readout inputs ----
    wire [63:0] adapt_data  = data_valid ? data : 64'd0;
    wire [15:0] adapt_flags = {14'b0, is_tclk, data_valid};   // bit0 has_data, bit1 is_tclk

    assign dbg_event_valid = event_valid;
    assign dbg_data_valid  = data_valid;
    assign dbg_is_tclk     = is_tclk;

    // ---- readout + AXI-Lite slave ----
    aclk_readout_axi #(.ADDR_WIDTH(ADDR_WIDTH), .AXI_ADDR_W(AXI_ADDR_W)) u_axi (
        .rx_clk        (rx_clk),
        .rx_rstn       (rx_rstn),
        .pps           (pps),
        .aclk_valid    (event_valid),
        .aclk_event    (event_id),
        .aclk_data     (adapt_data),
        .flags         (adapt_flags),
        .aclk_error    (parity_error),
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
