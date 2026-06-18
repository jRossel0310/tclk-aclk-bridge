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
    input  logic        mmcm_locked,       // MMCM locked (async) -> AXI 0xC0 LOCK

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
    output logic        dbg_hb,            // deep cdc heartbeat[12] -> pin probe
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

    // ---- line-activity diagnostic (-> DEBUG register 0xA0) ----
    // 2FF-synchronize the async Manchester line into rx_clk, count every transition,
    // and cross the count to the AXI domain with the same Gray counter the readout
    // uses elsewhere. A live line makes this climb even if framing never decodes, so
    // the PS can tell "signal present but not decoding" from "no signal at the pin".
    logic line_m, line_s2, line_s_d;
    always_ff @(posedge rx_clk or negedge rx_rstn) begin
        if (!rx_rstn) begin
            line_m   <= 1'b1;
            line_s2  <= 1'b1;
            line_s_d <= 1'b1;
        end else begin
            line_m   <= line;
            line_s2  <= line_m;
            line_s_d <= line_s2;
        end
    end
    wire line_edge = line_s2 ^ line_s_d;        // one rx_clk pulse per transition

    wire [29:0] edge_count;
    cdc_gray_count #(.W(30)) u_cnt_edge (
        .src_clk(rx_clk), .src_rstn(rx_rstn), .incr(line_edge),
        .dst_clk(s_axi_aclk), .count_dst(edge_count));

    // Live level, synchronized into the AXI domain (read at 0xA0 bit30).
    logic lvl_m, lvl_s;
    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            lvl_m <= 1'b0; lvl_s <= 1'b0;
        end else begin
            lvl_m <= line; lvl_s <= lvl_m;
        end
    end

    wire [31:0] aclk_dbg_word = {1'b0, lvl_s, edge_count};

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
        .dbg_word      (aclk_dbg_word),
        .mmcm_locked   (mmcm_locked),
        .dbg_hb        (dbg_hb),

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
