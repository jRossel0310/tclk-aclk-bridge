// rtl/aclk_lite/clk_readout_top.sv
//
// Unified ACLK/TCLK PL readout: clk_rcv (serdec + clk_byte_framer) -> adapter ->
// shared aclk_readout_axi (timestamp + async FIFO + AXI4-Lite, 16-byte map, filter).
// Reads real TCLK (8-bit events) and real ACLK-Lite (16-bit events + 64-bit data) from
// one serial line on H12. Counterpart to tclk_readout_top / aclk_lite_readout_top.
//
// Adapter: aclk_event = event_id; aclk_data = data when data_valid else 0; flags =
// {.., is_tclk, has_data}; aclk_error = parity_error (already a 1-cycle strobe, so no
// sticky-PERR edge-detect is needed unlike the TCLK_DESERIALIZER2 path). DROP_NULL = 0
// (no on-wire null code). DEBUG word mirrors the TCLK top: serial-line activity.

`timescale 1ns / 1ps

module clk_readout_top #(
    parameter int ADDR_WIDTH = 6,
    parameter int AXI_ADDR_W = 8
) (
    // ---- receive domain ----
    input  logic        clk_80m,
    input  logic        clk_40m,
    input  logic        rstn,
    input  logic        pps,
    input  logic        clkline,        // raw Manchester serial line (LVCMOS33 baseband)
    input  logic        mmcm_locked,

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

    // ---- debug ----
    output logic        dbg_event_valid,
    output logic [15:0] dbg_event,
    output logic        dbg_sig_err,
    output logic        dbg_hb,
    output logic        dropped_null
);
    // ---- unified decoder ----
    logic        ev_valid, dv, perr, is_tclk, sig_err;
    logic [15:0] ev_id;
    logic [63:0] dat;

    clk_rcv u_rcv (
        .RESETn       (rstn),
        .CLK_40M      (clk_40m),
        .CLK_80M      (clk_80m),
        .clkline      (clkline),
        .event_valid  (ev_valid),
        .event_id     (ev_id),
        .data_valid   (dv),
        .data         (dat),
        .parity_error (perr),
        .is_tclk      (is_tclk),
        .sig_err      (sig_err)
    );

    // ---- adapter ----
    wire [63:0] adapt_data  = dv ? dat : 64'd0;
    wire [15:0] adapt_flags = {14'b0, is_tclk, dv};   // bit0 has_data, bit1 is_tclk

    assign dbg_event_valid = ev_valid;
    assign dbg_event       = ev_id;
    assign dbg_sig_err     = sig_err;

    // ---- serial-line activity diagnostic (-> DEBUG register 0xA0) ----
    // 2FF-sync the raw line into clk_80m, count every transition, cross to the AXI
    // domain with the same Gray counter the readout uses elsewhere.
    logic line_m, line_s, line_s_d;
    always_ff @(posedge clk_80m or negedge rstn) begin
        if (!rstn) begin line_m <= 1'b1; line_s <= 1'b1; line_s_d <= 1'b1; end
        else       begin line_m <= clkline; line_s <= line_m; line_s_d <= line_s; end
    end
    wire line_edge = line_s ^ line_s_d;

    wire [29:0] edge_count;
    cdc_gray_count #(.W(30)) u_cnt_edge (
        .src_clk(clk_80m), .src_rstn(rstn), .incr(line_edge),
        .dst_clk(s_axi_aclk), .count_dst(edge_count));

    logic lvl_m, lvl_s, serr_m, serr_s;
    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin lvl_m <= 1'b0; lvl_s <= 1'b0; serr_m <= 1'b0; serr_s <= 1'b0; end
        else begin lvl_m <= clkline; lvl_s <= lvl_m; serr_m <= sig_err; serr_s <= serr_m; end
    end
    wire [31:0] clk_dbg_word = {serr_s, lvl_s, edge_count};

    // ---- readout + AXI-Lite slave (null-drop disabled) ----
    aclk_readout_axi #(.ADDR_WIDTH(ADDR_WIDTH), .AXI_ADDR_W(AXI_ADDR_W), .DROP_NULL(1'b0)) u_axi (
        .rx_clk        (clk_40m),
        .rx_rstn       (rstn),
        .pps           (pps),
        .aclk_valid    (ev_valid),
        .aclk_event    (ev_id),
        .aclk_data     (adapt_data),
        .flags         (adapt_flags),
        .aclk_error    (perr),
        .dropped_null  (dropped_null),
        .dbg_word      (clk_dbg_word),
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
