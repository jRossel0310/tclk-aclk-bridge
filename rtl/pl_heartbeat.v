// rtl/pl_heartbeat.v
//
// Dead-simple "is the PL alive?" heartbeat: a free-running 32-bit counter with NO
// reset and NO enable — it just increments on every pl_clk0 edge from power-up.
// Its value is exposed to the PS through an AXI GPIO; read that register twice
// from Linux and if the number changed, the PL fabric is configured and clocked.
//
// Plain Verilog (not SV) so it can be a block-design module reference directly.

`timescale 1ns / 1ps

module pl_heartbeat (
    input  wire        clk,
    output wire [31:0] count
);
    reg [31:0] c = 32'd0;
    always @(posedge clk) c <= c + 32'd1;
    assign count = c;
endmodule
