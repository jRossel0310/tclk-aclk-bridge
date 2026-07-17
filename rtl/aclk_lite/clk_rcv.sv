// rtl/aclk_lite/clk_rcv.sv
//
// Unified ACLK/TCLK receiver: the proven biphase bit recovery (serdec4_9MHz, 80 MHz)
// feeding the length-aware byte framer (clk_byte_framer, 40 MHz). Decodes real TCLK
// (1-byte frames) and real ACLK-Lite (2- and 12-byte frames) from one serial line.
// Counterpart to TCLK_RCV (which pairs serdec with the 1-byte TCLK_DESERIALIZER2).

`timescale 1ns / 1ps

module clk_rcv (
    input  logic        RESETn,
    input  logic        CLK_40M,
    input  logic        CLK_80M,
    input  logic        clkline,        // raw Manchester serial line (ACLK-Lite or TCLK)
    output logic        event_valid,
    output logic [15:0] event_id,
    output logic        data_valid,
    output logic [63:0] data,
    output logic        parity_error,
    output logic        is_tclk,
    output logic        sig_err
);
    wire sclk, sdata;

    serdec4_9MHz u_serdec (
        .RESETn   (RESETn),
        .CLK_80M  (CLK_80M),
        .TCLK     (clkline),
        .RATE     (1'b1),              // 10 MHz mode
        .SCLK     (sclk),
        .SDATA    (sdata),
        .TCLK_CAR (),
        .SIG_ERR  (sig_err)
    );

    clk_byte_framer u_framer (
        .clk          (CLK_40M),
        .rstn         (RESETn),
        .sclk         (sclk),
        .sdata        (sdata),
        .event_valid  (event_valid),
        .event_id     (event_id),
        .data_valid   (data_valid),
        .data         (data),
        .parity_error (parity_error),
        .is_tclk      (is_tclk)
    );
endmodule
