// rtl/global_timebase.v
// One free-running 64-bit tick counter in ref_clk, distributed to two event
// domains via the project's gray-code CDC. Both cdc_gray_count instances share
// ref_clk and ref_rstn with incr=1, so their source counters are bit-identical:
// ts_a and ts_b are the same timebase, each safely sampled into its domain.
`timescale 1ns / 1ps
module global_timebase (
    input  wire        ref_clk,
    input  wire        ref_rstn,
    input  wire        dst_clk_a,
    output wire [63:0] ts_a,
    input  wire        dst_clk_b,
    output wire [63:0] ts_b
);
    cdc_gray_count #(.W(64)) u_a (
        .src_clk(ref_clk), .src_rstn(ref_rstn), .incr(1'b1),
        .dst_clk(dst_clk_a), .count_dst(ts_a));
    cdc_gray_count #(.W(64)) u_b (
        .src_clk(ref_clk), .src_rstn(ref_rstn), .incr(1'b1),
        .dst_clk(dst_clk_b), .count_dst(ts_b));
endmodule
