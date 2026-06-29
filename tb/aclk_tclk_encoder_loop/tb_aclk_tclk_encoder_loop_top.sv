`timescale 1ns / 1ps
// Drives the encoder from a clk_40m-domain TCLK event stream and pipes its
// 16b+K output straight into ACLK_RCV (no GT, exactly like tb_aclkgt_gen_loop).
module tb_aclk_tclk_encoder_loop_top (
    input  wire        clk_tx,        // ~62.5 MHz encoder + RX clock
    input  wire        clk_40m,       // event-input domain
    input  wire        rstn,
    input  wire [7:0]  tclk_data,
    input  wire        tclk_davn,
    output wire [15:0] ACLK_EVENT,
    output wire [63:0] ACLK_DATA,
    output wire        ACLK_VALID,
    output wire        ACLK_ERROR,
    output wire        RX_ALIGNED_OUT
);
    wire [15:0] w_data16;
    wire [1:0]  w_k;

    aclk_tclk_encoder u_enc (
        .clk_tx   (clk_tx),
        .clk_40m  (clk_40m),
        .rstn_tx  (rstn),
        .tclk_data(tclk_data),
        .tclk_davn(tclk_davn),
        .data16   (w_data16),
        .k_out    (w_k),
        .marker   ()
    );

    ACLK_RCV u_rcv (
        .RESETn         (rstn),
        .CLK1           (clk_tx),
        .DATA_FROM_XCVR (w_data16),
        .K_FROM_XCVR    (w_k),
        .ACLK_EVENT     (ACLK_EVENT),
        .ACLK_DATA      (ACLK_DATA),
        .ACLK_VALID     (ACLK_VALID),
        .ACLK_ERROR     (ACLK_ERROR),
        .RX_ALIGNED_OUT (RX_ALIGNED_OUT),
        .DIAG           ()
    );
endmodule
