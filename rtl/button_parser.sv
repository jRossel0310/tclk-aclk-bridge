// rtl/button_parser.sv
//
// Button input conditioning chain: synchronizer -> debouncer -> edge_detector.
// Takes raw, asynchronous, bouncy button inputs and produces a clean one-cycle
// pulse on `out[i]` each time button `i` is pressed (its debounced level goes
// 0 -> 1). Operates per-bit, so WIDTH buttons are handled in parallel.
//
// Override SAMPLE_CNT_MAX / PULSE_CNT_MAX to set the debounce window (see
// debouncer.sv); the defaults give ~100 ms at 125 MHz.

`timescale 1ns / 1ps

module button_parser #(
    parameter int WIDTH          = 1,
    parameter int SAMPLE_CNT_MAX = 62500,
    parameter int PULSE_CNT_MAX  = 200
) (
    input  wire              clk,
    input  wire  [WIDTH-1:0] in,
    output logic [WIDTH-1:0] out
);

    wire [WIDTH-1:0] synchronized_signals;
    wire [WIDTH-1:0] debounced_signals;

    synchronizer #(
        .WIDTH(WIDTH)
    ) button_synchronizer (
        .clk         (clk),
        .async_signal(in),
        .sync_signal (synchronized_signals)
    );

    debouncer #(
        .WIDTH         (WIDTH),
        .SAMPLE_CNT_MAX(SAMPLE_CNT_MAX),
        .PULSE_CNT_MAX (PULSE_CNT_MAX)
    ) button_debouncer (
        .clk            (clk),
        .glitchy_signal (synchronized_signals),
        .debounced_signal(debounced_signals)
    );

    edge_detector #(
        .WIDTH(WIDTH)
    ) button_edge_detector (
        .clk             (clk),
        .signal_in       (debounced_signals),
        .edge_detect_pulse(out)
    );

endmodule
