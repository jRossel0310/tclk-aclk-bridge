// tb/aclk_lite_gen_loopback/tb_aclk_gen_loopback.sv
//
// Loopback harness: the hardcoded timeline's Manchester line feeds the real
// aclk_lite_decoder. Both run at OVERSAMPLE=12. IDLE_GAP/TRIO_GAP are shrunk so a
// few trios run quickly in sim. cocotb drives clk/rstn and watches the decoder.

`timescale 1ns / 1ps

module tb_aclk_gen_loopback #(
    parameter int OVERSAMPLE = 12,
    parameter int IDLE_GAP   = 32,
    parameter int TRIO_GAP   = 64
) (
    input  logic        clk,
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
        .OVERSAMPLE(OVERSAMPLE), .IDLE_GAP(IDLE_GAP), .TRIO_GAP(TRIO_GAP)
    ) u_gen (
        .clk(clk), .rstn(rstn), .line(line), .frame_sync(frame_sync)
    );

    aclk_lite_decoder #(.OVERSAMPLE(OVERSAMPLE)) u_dec (
        .clk(clk), .rstn(rstn), .line(line),
        .event_valid(event_valid), .event_id(event_id),
        .data_valid(data_valid), .data(data),
        .parity_error(parity_error), .is_tclk(is_tclk)
    );

endmodule
