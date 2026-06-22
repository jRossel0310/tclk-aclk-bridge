// tb/aclk_lite_gen_loopback/tb_aclk_gen_loopback.sv
//
// Loopback harness: the rewritten timeline's biphase-mark line feeds the REAL unified
// receiver (clk_rcv = serdec4_9MHz + clk_byte_framer). The generator and serdec run on
// clk_80m; the framer runs on clk_40m. IDLE_GAP/TRIO_GAP are shrunk so a few trios run
// quickly. cocotb drives the clocks/reset and watches the decoder.

`timescale 1ns / 1ps

module tb_aclk_gen_loopback #(
    parameter int SAMPLES_PER_CELL = 8,
    parameter int IDLE_GAP = 48,
    parameter int TRIO_GAP = 96
) (
    input  logic        clk_80m,
    input  logic        clk_40m,
    input  logic        rstn,
    output logic        event_valid,
    output logic [15:0] event_id,
    output logic        data_valid,
    output logic [63:0] data,
    output logic        parity_error,
    output logic        is_tclk,
    output logic        frame_sync
);
    logic line;

    aclk_lite_gen_timeline #(
        .SAMPLES_PER_CELL(SAMPLES_PER_CELL), .IDLE_GAP(IDLE_GAP), .TRIO_GAP(TRIO_GAP)
    ) u_gen (
        .clk(clk_80m), .rstn(rstn), .line(line), .frame_sync(frame_sync)
    );

    clk_rcv u_rcv (
        .RESETn      (rstn),
        .CLK_40M     (clk_40m),
        .CLK_80M     (clk_80m),
        .clkline     (line),
        .event_valid (event_valid),
        .event_id    (event_id),
        .data_valid  (data_valid),
        .data        (data),
        .parity_error(parity_error),
        .is_tclk     (is_tclk),
        .sig_err     ()
    );
endmodule
