// rtl/aclk_gt/aclk_gt_readout_top.sv
//
// Gigabit-ACLK RX readout brain: the inherited ACLK_RCV decoder (fed by a GT
// transceiver's 16-bit + K word stream on the recovered RX clock) through a
// trivial adapter into the shared decoder-agnostic aclk_readout_axi. Mirrors
// rtl/aclk_lite/aclk_lite_readout_top.sv. The GT transceiver itself lives in the
// Phase B integration top (aclk_gt_*_top); this module is pure RTL so it sims in
// Icarus exactly as the ACLK-Lite readout top does.
//
// Adapter: every aligned 96-bit packet carries EVENT[15:0] + DATA[63:0], so
// flags = has_data=1, is_tclk=0; DROP_NULL=1 drops the 0xFF-low-byte nulls.
// ACLK_VALID / ACLK_ERROR are one-cycle pulses (CRC-gated), so ACLK_ERROR feeds
// the readout error counter directly (no sticky edge-detect, unlike TCLK PERR).

`timescale 1ns / 1ps

module aclk_gt_readout_top #(
    parameter int ADDR_WIDTH = 6,
    parameter int AXI_ADDR_W = 8
) (
    // ---- recovered-RX (GT user) domain ----
    input  logic        rx_clk,
    input  logic        rx_rstn,
    input  logic        pps,
    input  logic [15:0] data_from_xcvr,
    input  logic [1:0]  k_from_xcvr,
    input  logic        mmcm_locked,       // GT/MMCM locked (async) -> AXI 0xC0 LOCK
    output logic        rx_aligned,         // ACLK_RCV comma alignment (debug/bring-up)
    output logic        dbg_event_valid,    // decoder valid pulse (debug/plot)
    output logic        dbg_hb,
    output logic        dropped_null,

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
    input  logic                   s_axi_rready
);
    // ---- inherited gigabit-ACLK decoder ----
    wire [15:0] aclk_event;
    wire [63:0] aclk_data;
    wire        aclk_valid;
    wire        aclk_error;
    wire [3:0]  diag;

    ACLK_RCV u_rcv (
        .RESETn         (rx_rstn),
        .CLK1           (rx_clk),
        .DATA_FROM_XCVR (data_from_xcvr),
        .K_FROM_XCVR    (k_from_xcvr),
        .ACLK_EVENT     (aclk_event),
        .ACLK_DATA      (aclk_data),
        .ACLK_VALID     (aclk_valid),
        .ACLK_ERROR     (aclk_error),
        .RX_ALIGNED_OUT (rx_aligned),
        .DIAG           (diag)
    );

    assign dbg_event_valid = aclk_valid;

    // ---- GT/decoder link-health diagnostic word (-> AXI 0xA0 DEBUG) ----
    // {algn_s (bit31), 1'b0 (bit30), frame_count_dst[29:0]} synced into the AXI domain.
    wire [29:0] frame_count_dst;
    cdc_gray_count #(.W(30)) u_cnt_frames (
        .src_clk(rx_clk), .src_rstn(rx_rstn), .incr(aclk_valid),
        .dst_clk(s_axi_aclk), .count_dst(frame_count_dst));
    logic algn_m, algn_s;
    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin algn_m <= 1'b0; algn_s <= 1'b0; end
        else begin algn_m <= rx_aligned; algn_s <= algn_m; end
    end
    wire [31:0] aclkgt_dbg_word = {algn_s, 1'b0, frame_count_dst};

    // ---- adapter: every packet carries 64-bit data ----
    wire [15:0] adapt_flags = 16'h0001;   // bit0 has_data=1, bit1 is_tclk=0

    aclk_readout_axi #(
        .ADDR_WIDTH (ADDR_WIDTH),
        .AXI_ADDR_W (AXI_ADDR_W),
        .DROP_NULL  (1'b1)
    ) u_axi (
        .rx_clk        (rx_clk),
        .rx_rstn       (rx_rstn),
        .pps           (pps),
        .aclk_valid    (aclk_valid),
        .aclk_event    (aclk_event),
        .aclk_data     (aclk_data),
        .flags         (adapt_flags),
        .aclk_error    (aclk_error),
        .dropped_null  (dropped_null),
        .dbg_word      (aclkgt_dbg_word),
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
