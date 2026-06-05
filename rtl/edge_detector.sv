// rtl/edge_detector.sv
//
// Rising-edge detector: emits a single one-cycle pulse on `edge_detect_pulse`
// the cycle after each 0->1 transition on `signal_in`. Works per-bit on a bus.
//
// Assumes `signal_in` is already synchronous to `clk` (e.g. it has been through
// a synchronizer/debouncer). The output is registered, so there is one cycle of
// latency between the input edge and the pulse.

`timescale 1ns / 1ps

module edge_detector #(
    parameter int WIDTH = 1
)(
    input  wire              clk,
    input  wire  [WIDTH-1:0] signal_in,
    output logic [WIDTH-1:0] edge_detect_pulse
);

    // Initialized so the first cycle produces a clean 0 instead of X in sim.
    logic [WIDTH-1:0] last_state = '0;

    always_ff @(posedge clk) begin
        edge_detect_pulse <= ~last_state & signal_in;  // 1 only on a 0->1 edge
        last_state        <= signal_in;
    end

endmodule
