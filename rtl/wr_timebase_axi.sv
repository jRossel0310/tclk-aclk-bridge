// rtl/wr_timebase_axi.sv
//
// AXI4-Lite face of the White Rabbit timebase (bus S_AXI3 in the pipeline).
// Contains the MONITOR wr_timebase instance (s_axi_aclk domain) that backs the
// status registers, and fans the arm/disarm cfg strobes out to the event-domain
// replicas (which CDC them in themselves via cdc_word_pulse).
//
// Registers are spaced 16 BYTES apart (project convention; see the aliasing
// note in rtl/aclk_readout/aclk_readout_axi.sv). Register select = addr[7:4].
//
//   0x00 STATUS      RO  [0] locked_tclk  [1] locked_aclk  [2] locked_mon
//                        [3] pps_alive    [4] clk10_alive  [5] arm_pending
//                        [8] lost_lock (sticky, cleared by CTRL[0])
//   0x10 SEC_ARM     RW  Unix UTC label of the NEXT PPS; the write arms
//   0x20 SEC_NOW     RO  monitor seconds; the read atomically latches NS_NOW
//   0x30 NS_NOW      RO  ns latched by the last SEC_NOW read
//   0x40 PPS_COUNT   RO  PPS edges seen since reset
//   0x50 CELLS_LAST  RO  10 MHz cells in the last PPS interval (expect 10,000,000)
//   0x60 CTRL        WO  [0] clear lost_lock  [1] broadcast disarm
//
// STRICT semantics note: SEC_NOW/NS_NOW read 0 while the monitor is unlocked
// (the monitor's ts is forced to 0), so a zero pair means "not synced".

`timescale 1ns / 1ps

module wr_timebase_axi #(
    parameter int AXI_ADDR_W = 8,
    parameter int unsigned MON_CLK_PERIOD_DS = 100,        // s_axi_aclk = 100 MHz
    parameter int unsigned MON_CLK10_TIMEOUT = 40,         // 400 ns at 100 MHz
    parameter int unsigned MON_PPS_TIMEOUT   = 110_000_000 // 1.1 s at 100 MHz
) (
    // ---- WR pins (async) ----
    input  logic        wr_clk10,
    input  logic        wr_pps,
    // ---- event-domain replica lock status (async; 2-FF synced here) ----
    input  logic        locked_a,       // TCLK-domain replica
    input  logic        locked_b,       // ACLK-domain replica
    // ---- cfg fan-out (s_axi_aclk domain; replicas CDC it in themselves) ----
    output logic        cfg_valid,      // 1-cycle strobe
    output logic        cfg_disarm,     // qualifies cfg_valid
    output logic [31:0] cfg_sec,
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

    // ---------------------------------------------------------------
    // Monitor timebase instance (everything in the s_axi_aclk domain;
    // its internal cfg CDC just adds a few cycles of latency).
    // ---------------------------------------------------------------
    wire [63:0] ts_mon;
    wire        locked_mon, arm_pending_mon, pps_alive_mon, clk10_alive_mon;
    wire        pps_edge_mon;
    wire [31:0] cells_last_mon;

    wr_timebase #(
        .CLK_PERIOD_DS (MON_CLK_PERIOD_DS),
        .CLK10_TIMEOUT (MON_CLK10_TIMEOUT),
        .PPS_TIMEOUT   (MON_PPS_TIMEOUT)
    ) u_mon (
        .clk         (s_axi_aclk),
        .rstn        (s_axi_aresetn),
        .wr_clk10    (wr_clk10),
        .wr_pps      (wr_pps),
        .cfg_clk     (s_axi_aclk),
        .cfg_rstn    (s_axi_aresetn),
        .cfg_valid   (cfg_valid),
        .cfg_disarm  (cfg_disarm),
        .cfg_sec     (cfg_sec),
        .ts          (ts_mon),
        .locked      (locked_mon),
        .arm_pending (arm_pending_mon),
        .pps_alive   (pps_alive_mon),
        .clk10_alive (clk10_alive_mon),
        .pps_edge    (pps_edge_mon),
        .cells_last  (cells_last_mon)
    );

    // ---------------------------------------------------------------
    // Replica lock bits, 2-FF synced into the AXI domain.
    // ---------------------------------------------------------------
    wire locked_a_s, locked_b_s;
    synchronizer #(.WIDTH(1), .STAGES(2)) u_sync_la (
        .clk(s_axi_aclk), .async_signal(locked_a), .sync_signal(locked_a_s));
    synchronizer #(.WIDTH(1), .STAGES(2)) u_sync_lb (
        .clk(s_axi_aclk), .async_signal(locked_b), .sync_signal(locked_b_s));

    // ---------------------------------------------------------------
    // AXI4-Lite read channel (single outstanding, 16-byte stride).
    // ---------------------------------------------------------------
    logic        arready_r, rvalid_r;
    logic [31:0] rdata_r, ns_latch, sec_arm_reg, pps_count;
    logic        lost_lock;
    wire [AXI_ADDR_W-5:0] rsel = s_axi_araddr[AXI_ADDR_W-1:4];

    wire [31:0] status_word = {23'b0, lost_lock, 2'b0, arm_pending_mon,
                               clk10_alive_mon, pps_alive_mon, locked_mon,
                               locked_b_s, locked_a_s};

    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            arready_r <= 1'b1;
            rvalid_r  <= 1'b0;
            rdata_r   <= 32'b0;
            ns_latch  <= 32'b0;
        end else if (arready_r && s_axi_arvalid) begin
            arready_r <= 1'b0;
            rvalid_r  <= 1'b1;
            case (rsel)
                'd0: rdata_r <= status_word;
                'd1: rdata_r <= sec_arm_reg;
                'd2: begin                               // SEC_NOW latches NS_NOW
                    rdata_r  <= ts_mon[63:32];
                    ns_latch <= ts_mon[31:0];
                end
                'd3: rdata_r <= ns_latch;
                'd4: rdata_r <= pps_count;
                'd5: rdata_r <= cells_last_mon;
                default: rdata_r <= 32'b0;
            endcase
        end else if (rvalid_r && s_axi_rready) begin
            rvalid_r  <= 1'b0;
            arready_r <= 1'b1;
        end
    end

    assign s_axi_arready = arready_r;
    assign s_axi_rvalid  = rvalid_r;
    assign s_axi_rdata   = rdata_r;
    assign s_axi_rresp   = 2'b00;            // OKAY

    // ---------------------------------------------------------------
    // AXI4-Lite write channel: AW and W accepted INDEPENDENTLY (the proven
    // no-deadlock pattern from aclk_readout_axi.sv). Also home of the
    // PPS_COUNT counter and the lost_lock sticky (all s_axi_aclk domain).
    // ---------------------------------------------------------------
    logic awready_r, wready_r, bvalid_r;
    logic [AXI_ADDR_W-5:0] waddr_q;
    logic [31:0] wdata_q;
    logic la_d, lb_d, lm_d;

    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            awready_r   <= 1'b1;
            wready_r    <= 1'b1;
            bvalid_r    <= 1'b0;
            waddr_q     <= '0;
            wdata_q     <= '0;
            sec_arm_reg <= '0;
            cfg_valid   <= 1'b0;
            cfg_disarm  <= 1'b0;
            pps_count   <= '0;
            la_d        <= 1'b0;
            lb_d        <= 1'b0;
            lm_d        <= 1'b0;
            lost_lock   <= 1'b0;
        end else begin
            cfg_valid <= 1'b0;

            if (pps_edge_mon) pps_count <= pps_count + 1'b1;

            // lost_lock sticky: any copy dropping out of lock (a disarm also
            // sets it; the PS clears the sticky after a deliberate re-arm).
            la_d <= locked_a_s;
            lb_d <= locked_b_s;
            lm_d <= locked_mon;
            if ((la_d && !locked_a_s) || (lb_d && !locked_b_s) ||
                (lm_d && !locked_mon))
                lost_lock <= 1'b1;

            if (s_axi_awvalid && awready_r) begin
                awready_r <= 1'b0;
                waddr_q   <= s_axi_awaddr[AXI_ADDR_W-1:4];
            end
            if (s_axi_wvalid && wready_r) begin
                wready_r <= 1'b0;
                wdata_q  <= s_axi_wdata;
            end
            if (!awready_r && !wready_r && !bvalid_r) begin
                bvalid_r <= 1'b1;
                if (waddr_q == 'd1) begin                 // SEC_ARM @ 0x10
                    sec_arm_reg <= wdata_q;
                    cfg_valid   <= 1'b1;
                    cfg_disarm  <= 1'b0;
                end
                if (waddr_q == 'd6) begin                 // CTRL @ 0x60
                    if (wdata_q[0]) lost_lock <= 1'b0;
                    if (wdata_q[1]) begin
                        cfg_valid  <= 1'b1;
                        cfg_disarm <= 1'b1;
                    end
                end
            end
            if (bvalid_r && s_axi_bready) begin
                bvalid_r  <= 1'b0;
                awready_r <= 1'b1;
                wready_r  <= 1'b1;
            end
        end
    end

    assign s_axi_awready = awready_r;
    assign s_axi_wready  = wready_r;
    assign s_axi_bvalid  = bvalid_r;
    assign s_axi_bresp   = 2'b00;            // OKAY
    assign cfg_sec       = sec_arm_reg;

endmodule
