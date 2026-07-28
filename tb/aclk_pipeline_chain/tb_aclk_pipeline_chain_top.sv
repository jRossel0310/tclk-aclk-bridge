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
// Two wr_timebase replicas + the wr_timebase_axi monitor/register slave
// (s3_s_axi_*) replace global_timebase: both readouts stamp events with the
// same WR-disciplined {sec, ns} timeline, strictly zero until armed + locked.
// Sim second = 50 WR cells = 5 us (watchdog params scaled to match).
//
// Both readouts use USE_EXT_TS=1 so they sample the shared timebase.
//
// clk_p90/p180/p270: 200 MHz quadrature companions to clk_40m (clk_p0), driven
// into readout #1's fine-TDC (tclk_fine_tdc, inside tclk_readout_top). Without
// these the TDC's samplers sit at X and the packed TCLK timestamp is broken.
//
// AXI BFM signal-prefix convention:
//   pfx=""    -> s_axi_*      (readout #1: tclk_readout_top)
//   pfx="s2_" -> s2_s_axi_*  (readout #2: aclk_gt_readout_top)
//   pfx="s3_" -> s3_s_axi_*  (wr_timebase_axi: the WR timebase monitor/registers)
//
// All THREE AXI slaves share the same pl_clk0 net, exposed here as s_axi_aclk /
// s2_s_axi_aclk / s3_s_axi_aclk so the BFM can find them by name. Load-bearing
// assumption: the two wr_timebase replicas take cfg_clk(s_axi_aclk) while their
// cfg_valid/cfg_disarm/cfg_sec strobes are generated inside u_tb_axi on
// s3_s_axi_aclk. That cross-slave handoff is glitch-free only because all three
// AXI clocks are the same physical net (true on hardware, and true here since
// all three clocks are driven identically). Task 5 copies this into the HW top.

`timescale 1ns / 1ps

module tb_aclk_pipeline_chain_top (
    // Clocks (driven by cocotb)
    input  wire clk_80m,          // 80 MHz: TCLK_RCV oversample
    input  wire clk_40m,          // 40 MHz: TCLK_RCV decode + readout#1 rx_clk
    input  wire clk_p90,          // fine-TDC quadrature companion: clk_40m +90 deg
    input  wire clk_p180,         // fine-TDC quadrature companion: clk_40m +180 deg
    input  wire clk_p270,         // fine-TDC quadrature companion: clk_40m +270 deg
    input  wire clk_tx,           // ~62.5 MHz: encoder TX + ACLK_RCV rx_clk
    input  wire pl_clk0,          // 100 MHz: AXI / WR timebase monitor reference

    // Reset (active-low; single async reset for the whole chain)
    input  wire rstn,

    // TCLK biphase line input
    input  wire tclk,

    // White Rabbit reference inputs (async; shared by all timebase copies)
    input  wire wr_clk10,
    input  wire wr_pps,

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
    input  wire        s2_s_axi_rready,

    // ---- AXI4-Lite slave #3: wr_timebase_axi (pfx="s3_" -> s3_s_axi_*) ----
    input  wire        s3_s_axi_aclk,
    input  wire        s3_s_axi_aresetn,
    input  wire [7:0]  s3_s_axi_awaddr,
    input  wire        s3_s_axi_awvalid,
    output wire        s3_s_axi_awready,
    input  wire [31:0] s3_s_axi_wdata,
    input  wire [3:0]  s3_s_axi_wstrb,
    input  wire        s3_s_axi_wvalid,
    output wire        s3_s_axi_wready,
    output wire [1:0]  s3_s_axi_bresp,
    output wire        s3_s_axi_bvalid,
    input  wire        s3_s_axi_bready,
    input  wire [7:0]  s3_s_axi_araddr,
    input  wire        s3_s_axi_arvalid,
    output wire        s3_s_axi_arready,
    output wire [31:0] s3_s_axi_rdata,
    output wire [1:0]  s3_s_axi_rresp,
    output wire        s3_s_axi_rvalid,
    input  wire        s3_s_axi_rready
);

    // ----------------------------------------------------------------
    // WR timebase: one replica per event domain + the AXI monitor slave.
    // Sim-scaled watchdogs for the 50-cell (5 us) sim second.
    // ----------------------------------------------------------------
    wire        cfg_valid, cfg_disarm;
    wire [31:0] cfg_sec;
    wire [63:0] ts_tclk;   // clk_40m domain -> readout#1
    wire [63:0] ts_aclk;   // clk_tx domain -> readout#2
    wire        tb_locked_tclk, tb_locked_aclk;

    wr_timebase #(
        .CLK_PERIOD_DS (250),
        .CLK10_TIMEOUT (16),     // 400 ns at 40 MHz
        .PPS_TIMEOUT   (240)     // 6 us at 40 MHz
    ) u_tb_tclk (
        .clk(clk_40m), .rstn(rstn), .wr_clk10(wr_clk10), .wr_pps(wr_pps),
        .cfg_clk(s_axi_aclk), .cfg_rstn(s_axi_aresetn),
        .cfg_valid(cfg_valid), .cfg_disarm(cfg_disarm), .cfg_sec(cfg_sec),
        .ts(ts_tclk), .locked(tb_locked_tclk), .arm_pending(),
        .pps_alive(), .clk10_alive(), .pps_edge(), .cells_last()
    );

    wr_timebase #(
        .CLK_PERIOD_DS (160),
        .CLK10_TIMEOUT (25),     // 400 ns at 62.5 MHz
        .PPS_TIMEOUT   (375)     // 6 us at 62.5 MHz
    ) u_tb_aclk (
        .clk(clk_tx), .rstn(rstn), .wr_clk10(wr_clk10), .wr_pps(wr_pps),
        .cfg_clk(s_axi_aclk), .cfg_rstn(s_axi_aresetn),
        .cfg_valid(cfg_valid), .cfg_disarm(cfg_disarm), .cfg_sec(cfg_sec),
        .ts(ts_aclk), .locked(tb_locked_aclk), .arm_pending(),
        .pps_alive(), .clk10_alive(), .pps_edge(), .cells_last()
    );

    wr_timebase_axi #(
        .AXI_ADDR_W        (8),
        .MON_CLK10_TIMEOUT (40),    // 400 ns at 100 MHz
        .MON_PPS_TIMEOUT   (600)    // 6 us at 100 MHz
    ) u_tb_axi (
        .wr_clk10   (wr_clk10),
        .wr_pps     (wr_pps),
        .locked_a   (tb_locked_tclk),
        .locked_b   (tb_locked_aclk),
        .cfg_valid  (cfg_valid),
        .cfg_disarm (cfg_disarm),
        .cfg_sec    (cfg_sec),
        .s_axi_aclk    (s3_s_axi_aclk),
        .s_axi_aresetn (s3_s_axi_aresetn),
        .s_axi_awaddr  (s3_s_axi_awaddr),
        .s_axi_awvalid (s3_s_axi_awvalid),
        .s_axi_awready (s3_s_axi_awready),
        .s_axi_wdata   (s3_s_axi_wdata),
        .s_axi_wstrb   (s3_s_axi_wstrb),
        .s_axi_wvalid  (s3_s_axi_wvalid),
        .s_axi_wready  (s3_s_axi_wready),
        .s_axi_bresp   (s3_s_axi_bresp),
        .s_axi_bvalid  (s3_s_axi_bvalid),
        .s_axi_bready  (s3_s_axi_bready),
        .s_axi_araddr  (s3_s_axi_araddr),
        .s_axi_arvalid (s3_s_axi_arvalid),
        .s_axi_arready (s3_s_axi_arready),
        .s_axi_rdata   (s3_s_axi_rdata),
        .s_axi_rresp   (s3_s_axi_rresp),
        .s_axi_rvalid  (s3_s_axi_rvalid),
        .s_axi_rready  (s3_s_axi_rready)
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
        .clk_p90       (clk_p90),
        .clk_p180      (clk_p180),
        .clk_p270      (clk_p270),
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
