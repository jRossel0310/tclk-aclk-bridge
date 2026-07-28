// rtl/aclk_lite/tclk_fine_decode.sv
// Thermometer decode of five phase-ordered line samples -> sub-bin + validity.
// samples[i] is the line at chronological position i (0 = earliest .. 4 = latest)
// of a 5-sample window: the previous period's phase-270 sample (delayed one
// clk_p0 cycle) followed by the four current-period phase samples. That window
// spans exactly one 200 MHz period across the period boundary, so it resolves
// 4 interior crossing positions (a bare 4-sample window only resolves 3; the
// 4th quarter aliases across the boundary and is lost without the extra tap).
// A clean crossing is monotone; fine_phase is the leading-run length - 1,
// spanning 0..3. Any non-monotone (glitch) pattern raises fine_valid = 0
// (graceful fallback).
`default_nettype none
module tclk_fine_decode (
    input  wire [4:0] samples,
    output reg  [1:0] fine_phase,
    output reg        fine_valid
);
    // leading-run length of samples[0] within samples[0..4] (1..5; 5 only
    // occurs for the all-equal patterns, which are excluded by `monotone`
    // below, so the value actually used downstream is always 1..4).
    reg [2:0] run;
    always @(*) begin
        run = 3'd1;
        if (samples[1] == samples[0]) begin
            run = 3'd2;
            if (samples[2] == samples[0]) begin
                run = 3'd3;
                if (samples[3] == samples[0]) begin
                    run = 3'd4;
                    if (samples[4] == samples[0]) run = 3'd5;
                end
            end
        end
    end

    // monotone-and-not-all-equal == exactly the eight thermometer codes
    // (bit order {samples[4],samples[3],samples[2],samples[1],samples[0]}).
    wire monotone =
        (samples == 5'b00001) | (samples == 5'b00011) |
        (samples == 5'b00111) | (samples == 5'b01111) |
        (samples == 5'b11110) | (samples == 5'b11100) |
        (samples == 5'b11000) | (samples == 5'b10000);

    always @(*) begin
        if (monotone) begin
            fine_valid = 1'b1;
            fine_phase = run[1:0] - 2'd1;      // run in 1..4 -> phase 0..3
        end else begin
            fine_valid = 1'b0;
            fine_phase = 2'd0;
        end
    end
endmodule
`default_nettype wire
