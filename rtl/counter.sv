// rtl/counter.sv
//
// Trivial up-counter: synchronous active-high reset, count enable.
// This is smoke-test RTL — its job is to validate the toolchain plumbing
// (compile -> simulate -> cocotb -> waveform), NOT to do anything useful.
//
// Kept deliberately simple. Real modules go in their own rtl/<name>.sv files.

`timescale 1ns / 1ps

module counter #(
    parameter int WIDTH = 8
) (
    input  logic             clk,
    input  logic             rst,    // synchronous, active-high
    input  logic             en,     // count enable
    output logic [WIDTH-1:0] count
);

    always_ff @(posedge clk) begin
        if (rst)
            count <= '0;
        else if (en)
            count <= count + 1'b1;
    end

endmodule
