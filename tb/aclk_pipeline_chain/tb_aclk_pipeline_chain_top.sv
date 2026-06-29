// tb/aclk_pipeline_chain/tb_aclk_pipeline_chain_top.sv
//
// Full pure-RTL pipeline chain testbench top (no GT).
//
// Chain:
//   tclk (raw biphase line, driven on clk_80m)
//     -> TCLK_RCV (clk_80m oversample, clk_40m decode)
//     -> tclk_readout_top  (readout #1, s_axi_*)
//     -> aclk_tclk_encoder (clk_40m src, clk_tx encode)
//     -> ACLK_RCV          (direct 16b+K feed, no GT)
//     -> aclk_gt_readout_top (readout #2, s2_s_axi_*)
//
// global_timebase provides a shared 64-bit tick counter:
//   ref_clk = pl_clk0, dst_clk_a = clk_40m -> ts_tclk -> readout#1
//             dst_clk_b = clk_tx  -> ts_aclk -> readout#2
//
// Both readouts use USE_EXT_TS=1 so they sample the shared timebase.
//
// AXI BFM signal-prefix convention:
//   pfx=""    -> s_axi_*      (readout #1: tclk_readout_top)
//   pfx="s2_" -> s2_s_axi_*  (readout #2: aclk_gt_readout_top)
//
// The AXI clock for both slaves is the same pl_clk0 signal, exposed here as
// both s_axi_aclk and s2_s_axi_aclk so the BFM can find them by name.

`timescale 1ns / 1ps

module tb_aclk_pipeline_chain_top (
    // Clocks (driven by cocotb)
    input  wire clk_80m,          // 80 MHz: TCLK_RCV oversample
    input  wire clk_40m,          // 40 MHz: TCLK_RCV decode + readout#1 rx_clk
    input  wire clk_tx,           // ~62.5 MHz: encoder TX + ACLK_RCV rx_clk
    input  wire pl_clk0,          // 100 MHz: AXI / global_timebase reference

    // Reset (active-low; single async reset for the whole chain)
    input  wire rstn,

    // TCLK biphase line input
    input  wire tclk,

    // ---- AXI4-Lite slave #1: tclk_readout_top (pfx="" -> s_axi_*) ----
    input  wire        s_axi_aclk,
    input  wire        s_axi_aresetn,
    input  wire [7:0]  s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    output wire [1:0]  s_axi_bresp,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [7:0]  s_axi_araddr,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rvalid,
    input  wire        s_axi_rready,

    // ---- AXI4-Lite slave #2: aclk_gt_readout_top (pfx="s2_" -> s2_s_axi_*) ----
    input  wire        s2_s_axi_aclk,
    input  wire        s2_s_axi_aresetn,
    input  wire [7:0]  s2_s_axi_awaddr,
    input  wire        s2_s_axi_awvalid,
    output wire        s2_s_axi_awready,
    input  wire [31:0] s2_s_axi_wdata,
    input  wire [3:0]  s2_s_axi_wstrb,
    input  wire        s2_s_axi_wvalid,
    output wire        s2_s_axi_wready,
    output wire [1:0]  s2_s_axi_bresp,
    output wire        s2_s_axi_bvalid,
    input  wire        s2_s_axi_bready,
    input  wire [7:0]  s2_s_axi_araddr,
    input  wire        s2_s_axi_arvalid,
    output wire        s2_s_axi_arready,
    output wire [31:0] s2_s_axi_rdata,
    output wire [1:0]  s2_s_axi_rresp,
    output wire        s2_s_axi_rvalid,
    input  wire        s2_s_axi_rready
);

    // ----------------------------------------------------------------
    // Shared timebase (ref = pl_clk0; distributed to both event domains)
    // ----------------------------------------------------------------
    wire [63:0] ts_tclk;   // sampled into clk_40m domain -> readout#1
    wire [63:0] ts_aclk;   // sampled into clk_tx  domain -> readout#2

    global_timebase u_tb (
        .ref_clk   (pl_clk0),
        .ref_rstn  (rstn),
        .dst_clk_a (clk_40m),
        .ts_a      (ts_tclk),
        .dst_clk_b (clk_tx),
        .ts_b      (ts_aclk)
    );

    // ----------------------------------------------------------------
    // TCLK readout #1
    // ----------------------------------------------------------------
    wire dbg_dav_1, dbg_perr_1, dbg_sig_err_1, dbg_hb_1, dropped_null_1;
    wire [7:0] dbg_data_1;

    tclk_readout_top #(
        .ADDR_WIDTH (6),
        .AXI_ADDR_W (8),
        .USE_EXT_TS (1'b1)
    ) u_tclk_rdout (
        .clk_80m       (clk_80m),
        .clk_40m       (clk_40m),
        .rstn          (rstn),
        .pps           (1'b0),
        .tclk          (tclk),
        .mmcm_locked   (1'b1),
        .ts_ext        (ts_tclk),
        .dbg_dav       (dbg_dav_1),
        .dbg_data      (dbg_data_1),
        .dbg_perr      (dbg_perr_1),
        .dbg_sig_err   (dbg_sig_err_1),
        .dbg_hb        (dbg_hb_1),
        .dropped_null  (dropped_null_1),
        // AXI slave #1 -- use s_axi_aclk directly from the cocotb port
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

    // ----------------------------------------------------------------
    // Tap the decoded TCLK byte stream for the encoder via debug ports.
    // dbg_dav_1 = ~DAVn (one high strobe per decoded byte, clk_40m domain)
    // Reconstruct active-low DAVn for the encoder.
    // ----------------------------------------------------------------
    wire tclk_davn_enc = ~dbg_dav_1;

    // ----------------------------------------------------------------
    // ACLK TCLK encoder: TCLK bytes -> 96-bit ACLK frames -> 16b+K words
    // ----------------------------------------------------------------
    wire [15:0] enc_data16;
    wire [1:0]  enc_k_out;

    aclk_tclk_encoder u_enc (
        .clk_tx    (clk_tx),
        .rstn_tx   (rstn),
        .clk_40m   (clk_40m),
        .tclk_data (dbg_data_1),
        .tclk_davn (tclk_davn_enc),
        .data16    (enc_data16),
        .k_out     (enc_k_out),
        .marker    ()
    );

    // ----------------------------------------------------------------
    // ACLK readout #2 (GT-less: feed encoder output directly to ACLK_RCV
    // inside aclk_gt_readout_top)
    // ----------------------------------------------------------------
    wire dbg_hb_2, dropped_null_2, rx_aligned_2, dbg_event_valid_2;
    wire [31:0] gt_ctrl_2;

    aclk_gt_readout_top #(
        .ADDR_WIDTH (6),
        .AXI_ADDR_W (8),
        .USE_EXT_TS (1'b1)
    ) u_aclk_rdout (
        .rx_clk         (clk_tx),
        .rx_rstn        (rstn),
        .dec_rstn       (rstn),       // no recovery FSM in this sim; tie to rx_rstn
        .pps            (1'b0),
        .data_from_xcvr (enc_data16),
        .k_from_xcvr    (enc_k_out),
        .mmcm_locked    (1'b1),
        .dbg_word_in    (32'b0),
        .ts_ext         (ts_aclk),
        .rx_aligned     (rx_aligned_2),
        .dbg_event_valid(dbg_event_valid_2),
        .dbg_hb         (dbg_hb_2),
        .dropped_null   (dropped_null_2),
        .gt_ctrl        (gt_ctrl_2),
        // AXI slave #2 -- use s2_s_axi_aclk directly from the cocotb port
        .s_axi_aclk    (s2_s_axi_aclk),
        .s_axi_aresetn (s2_s_axi_aresetn),
        .s_axi_awaddr  (s2_s_axi_awaddr),
        .s_axi_awvalid (s2_s_axi_awvalid),
        .s_axi_awready (s2_s_axi_awready),
        .s_axi_wdata   (s2_s_axi_wdata),
        .s_axi_wstrb   (s2_s_axi_wstrb),
        .s_axi_wvalid  (s2_s_axi_wvalid),
        .s_axi_wready  (s2_s_axi_wready),
        .s_axi_bresp   (s2_s_axi_bresp),
        .s_axi_bvalid  (s2_s_axi_bvalid),
        .s_axi_bready  (s2_s_axi_bready),
        .s_axi_araddr  (s2_s_axi_araddr),
        .s_axi_arvalid (s2_s_axi_arvalid),
        .s_axi_arready (s2_s_axi_arready),
        .s_axi_rdata   (s2_s_axi_rdata),
        .s_axi_rresp   (s2_s_axi_rresp),
        .s_axi_rvalid  (s2_s_axi_rvalid),
        .s_axi_rready  (s2_s_axi_rready)
    );

endmodule
