`timescale 1ns / 1ps
// One wr_timebase replica at the pipeline's 200 MHz TCLK constants. PPS_TIMEOUT is
// a sim-scaled value (1200 clk = 6 us > one 5 us sim-second) so PPS-loss is testable;
// the real build uses 220_000_000 (1.1 s), which is arithmetic + board only.
module tb_wr_timebase_200_top (
    input  logic        clk,
    input  logic        rstn,
    input  logic        wr_clk10,
    input  logic        wr_pps,
    input  logic        cfg_clk,
    input  logic        cfg_rstn,
    input  logic        cfg_valid,
    input  logic        cfg_disarm,
    input  logic [31:0] cfg_sec,
    output logic [63:0] ts,
    output logic        locked,
    output logic        clk10_alive,
    output logic        pps_alive,
    output logic [31:0] cells_last
);
    wr_timebase #(
        .CLK_PERIOD_DS (50),        // 5.0 ns at 200 MHz  (the real value)
        .CLK10_TIMEOUT (80),        // 400 ns window       (the real value)
        .PPS_TIMEOUT   (1200),      // 6 us  (SIM-SCALED; real build = 220_000_000)
        .PPS_MIN_CELLS (45)         // 0.9 s (SIM-SCALED; real build = 9_000_000)
    ) u_tb (
        .clk(clk), .rstn(rstn),
        .wr_clk10(wr_clk10), .wr_pps(wr_pps),
        .cfg_clk(cfg_clk), .cfg_rstn(cfg_rstn),
        .cfg_valid(cfg_valid), .cfg_disarm(cfg_disarm), .cfg_sec(cfg_sec),
        .ts(ts), .locked(locked), .arm_pending(),
        .pps_alive(pps_alive), .clk10_alive(clk10_alive),
        .pps_edge(), .cells_last(cells_last)
    );
endmodule
