// tb/wr_timebase/tb_wr_timebase_top.sv
//
// Two wr_timebase replicas on one WR pin pair:
//   u_a: 25.0 ns local clock (clk_40m-like, integer ns period)
//   u_b:  6.4 ns local clock (fractional period, exercises the 0.1 ns
//         interpolator remainder path; no production domain needs it today)
// Sim-scaled watchdogs for a 50-cell (5 us) 'second':
//   CLK10_TIMEOUT ~= 400 ns of cycles, PPS_TIMEOUT ~= 6 us of cycles.

`timescale 1ns / 1ps

module tb_wr_timebase_top (
    input  wire        clk_a,        // 25 ns
    input  wire        clk_b,        // 6.4 ns
    input  wire        cfg_clk,      // 10 ns (AXI-like)
    input  wire        rstn,
    input  wire        cfg_rstn,
    input  wire        wr_clk10,
    input  wire        wr_pps,
    input  wire        cfg_valid,
    input  wire        cfg_disarm,
    input  wire [31:0] cfg_sec,

    output wire [63:0] ts_a,
    output wire        locked_a,
    output wire        arm_pending_a,
    output wire        pps_alive_a,
    output wire        clk10_alive_a,
    output wire        pps_edge_a,
    output wire [31:0] cells_last_a,

    output wire [63:0] ts_b,
    output wire        locked_b
);

    wr_timebase #(
        .CLK_PERIOD_DS (250),
        .CLK10_TIMEOUT (16),     // 400 ns at 40 MHz
        .PPS_TIMEOUT   (240)     // 6 us at 40 MHz
    ) u_a (
        .clk(clk_a), .rstn(rstn), .wr_clk10(wr_clk10), .wr_pps(wr_pps),
        .cfg_clk(cfg_clk), .cfg_rstn(cfg_rstn),
        .cfg_valid(cfg_valid), .cfg_disarm(cfg_disarm), .cfg_sec(cfg_sec),
        .ts(ts_a), .locked(locked_a), .arm_pending(arm_pending_a),
        .pps_alive(pps_alive_a), .clk10_alive(clk10_alive_a),
        .pps_edge(pps_edge_a), .cells_last(cells_last_a)
    );

    wr_timebase #(
        .CLK_PERIOD_DS (64),
        .CLK10_TIMEOUT (63),     // ~403 ns at 156.25 MHz
        .PPS_TIMEOUT   (940)     // ~6 us at 156.25 MHz
    ) u_b (
        .clk(clk_b), .rstn(rstn), .wr_clk10(wr_clk10), .wr_pps(wr_pps),
        .cfg_clk(cfg_clk), .cfg_rstn(cfg_rstn),
        .cfg_valid(cfg_valid), .cfg_disarm(cfg_disarm), .cfg_sec(cfg_sec),
        .ts(ts_b), .locked(locked_b), .arm_pending(),
        .pps_alive(), .clk10_alive(), .pps_edge(), .cells_last()
    );

endmodule
