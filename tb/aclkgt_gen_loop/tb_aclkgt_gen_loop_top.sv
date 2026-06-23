// tb/aclkgt_gen_loop/tb_aclkgt_gen_loop_top.sv
//
// Generator-to-receiver loopback harness (no GT transceiver).
// Wires aclk_gt_frame_gen's 16-bit + K word stream directly into ACLK_RCV
// (which contains its own GEARBOX_16_TO_96 and CRC8_CALC). All on one CLK1.
// cocotb drives CLK1/RESETn and observes the decoder outputs.
`timescale 1ns/1ps
module tb_aclkgt_gen_loop_top (
    input  wire        CLK1,
    input  wire        RESETn,
    output wire [15:0] ACLK_EVENT,
    output wire [63:0] ACLK_DATA,
    output wire        ACLK_VALID,
    output wire        ACLK_ERROR,
    output wire        RX_ALIGNED_OUT
);
    wire [15:0] w_data16;
    wire [1:0]  w_k;
    aclk_gt_frame_gen #(.N_EVENTS(3)) u_gen (
        .CLK1(CLK1), .RESETn(RESETn), .DATA16(w_data16), .K_OUT(w_k), .MARKER());
    ACLK_RCV u_rcv (
        .RESETn(RESETn), .CLK1(CLK1),
        .DATA_FROM_XCVR(w_data16), .K_FROM_XCVR(w_k),
        .ACLK_EVENT(ACLK_EVENT), .ACLK_DATA(ACLK_DATA),
        .ACLK_VALID(ACLK_VALID), .ACLK_ERROR(ACLK_ERROR),
        .RX_ALIGNED_OUT(RX_ALIGNED_OUT), .DIAG());
endmodule
