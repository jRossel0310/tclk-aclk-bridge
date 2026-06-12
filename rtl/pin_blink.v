// rtl/pin_blink.v
//
// Bring-up blinker: drive one PL output pin as a slow square wave so a physical
// package pin can be verified with a multimeter / scope before any real design
// uses it. It toggles every HALF_PERIOD clocks, i.e. HIGH for HALF_PERIOD/pl_clk0
// seconds, then LOW, repeating. With the default (HALF_PERIOD = 100_000_000 and
// pl_clk0 = 100 MHz) that is 1.0 s high then 1.0 s low: a 0.5 Hz square wave,
// slow enough to read on a DMM and obvious on a scope or LED.
//
// No reset / no enable: it free-runs from configuration (registers power up to 0),
// matching rtl/pl_heartbeat.v. Plain Verilog so it can be a block-design module
// reference directly (Vivado rejects a SystemVerilog module-reference BD top).

`timescale 1ns / 1ps

module pin_blink #(
    // Clocks per half period. Set this to your pl_clk0 frequency in Hz for a
    // 1 s / 1 s blink (100_000_000 at the KR260 default pl_clk0 = 100 MHz).
    parameter integer HALF_PERIOD = 100000000
)(
    input  wire clk,
    output wire pin
);
    reg [31:0] cnt   = 32'd0;
    reg        level = 1'b0;

    always @(posedge clk) begin
        if (cnt >= HALF_PERIOD - 1) begin
            cnt   <= 32'd0;
            level <= ~level;
        end else begin
            cnt <= cnt + 32'd1;
        end
    end

    assign pin = level;
endmodule
