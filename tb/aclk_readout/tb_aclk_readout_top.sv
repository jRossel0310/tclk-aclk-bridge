// tb/aclk_readout/tb_aclk_readout_top.sv
//
// Test top: the real ACLK_RCV decoder feeding the readout core. The receive-side
// ports keep ACLK_RCV's names (CLK1, RESETn, DATA_FROM_XCVR, K_FROM_XCVR) so the
// shared TX model's stream_frames() can drive it unchanged. CLK1 is the recovered
// RX clock domain; rd_clk is the independent PS-facing read clock.

`timescale 1ns / 1ps

module tb_aclk_readout_top #(
    parameter int ADDR_WIDTH = 6
) (
    // receive-side word stream (ACLK_RCV port names, driven by the TX model)
    input  logic        CLK1,
    input  logic        RESETn,
    input  logic [15:0] DATA_FROM_XCVR,
    input  logic [1:0]  K_FROM_XCVR,

    // PS-facing read side
    input  logic        rd_clk,
    input  logic        rd_rstn,
    input  logic        rd_en,
    output logic [143:0] rd_data,
    output logic        empty,
    output logic        overflow,
    output logic        dropped_null,

    // exposed for visibility / debug
    output logic        aclk_valid,
    output logic        aclk_error,
    output logic        rx_aligned
);

    logic [15:0] aclk_event;
    logic [63:0] aclk_data;
    logic [3:0]  diag;

    ACLK_RCV u_rcv (
        .RESETn         (RESETn),
        .CLK1           (CLK1),
        .DATA_FROM_XCVR (DATA_FROM_XCVR),
        .K_FROM_XCVR    (K_FROM_XCVR),
        .ACLK_EVENT     (aclk_event),
        .ACLK_DATA      (aclk_data),
        .ACLK_VALID     (aclk_valid),
        .ACLK_ERROR     (aclk_error),
        .RX_ALIGNED_OUT (rx_aligned),
        .DIAG           (diag)
    );

    aclk_readout_core #(.ADDR_WIDTH(ADDR_WIDTH)) u_core (
        .rx_clk       (CLK1),
        .rx_rstn      (RESETn),
        .pps          (1'b0),               // no PPS in bring-up; free-running timestamp
        .aclk_valid   (aclk_valid),
        .aclk_event   (aclk_event),
        .aclk_data    (aclk_data),
        .rd_clk       (rd_clk),
        .rd_rstn      (rd_rstn),
        .rd_en        (rd_en),
        .rd_data      (rd_data),
        .empty        (empty),
        .overflow     (overflow),
        .dropped_null (dropped_null)
    );

endmodule
