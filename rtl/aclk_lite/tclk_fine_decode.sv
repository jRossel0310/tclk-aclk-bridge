// rtl/aclk_lite/tclk_fine_decode.sv
// Thermometer decode of four phase-ordered line samples -> sub-bin + validity.
// samples[i] is the line at phase i (0 = earliest .. 3 = latest) of one 200 MHz
// period. A clean crossing is monotone; fine_phase is the leading-run length - 1.
// Any non-monotone (glitch) pattern raises fine_valid = 0 (graceful fallback).
`default_nettype none
module tclk_fine_decode (
    input  wire [3:0] samples,
    output reg  [1:0] fine_phase,
    output reg        fine_valid
);
    // leading-run length of samples[0] within samples[0..3]
    reg [2:0] run;
    always @(*) begin
        run = 3'd1;
        if (samples[1] == samples[0]) begin
            run = 3'd2;
            if (samples[2] == samples[0]) begin
                run = 3'd3;
                if (samples[3] == samples[0]) run = 3'd4;
            end
        end
    end

    // monotone-and-not-all-equal == exactly the six thermometer codes
    // (bit order {samples[3],samples[2],samples[1],samples[0]}).
    wire monotone =
        (samples == 4'b0001) | (samples == 4'b0011) | (samples == 4'b0111) |
        (samples == 4'b1110) | (samples == 4'b1100) | (samples == 4'b1000);

    always @(*) begin
        if (monotone) begin
            fine_valid = 1'b1;
            fine_phase = run[1:0] - 2'd1;      // run in 1..3 -> phase 0..2
        end else begin
            fine_valid = 1'b0;
            fine_phase = 2'd0;
        end
    end
endmodule
`default_nettype wire
