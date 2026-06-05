// rtl/debouncer.sv
//
// Switch/button debouncer. Each input bit must stay high continuously for a
// debounce window before the corresponding output goes high; it drops back to
// low as soon as the input is sampled low.
//
// How it works:
//   - A free-running "wrapping" counter divides `clk` down to a sample tick
//     once every SAMPLE_CNT_MAX cycles (sample_pulse).
//   - On each sample tick, a per-bit saturating counter increments while the
//     input is high and resets to 0 while it is low.
//   - Once a bit's saturating counter reaches PULSE_CNT_MAX it is considered
//     debounced and the output asserts.
//
// Debounce time ~= SAMPLE_CNT_MAX * PULSE_CNT_MAX / f_clk. The defaults
// (62500 * 200 @ 125 MHz) give ~100 ms. Override both for simulation so the
// window is only a handful of cycles.

`timescale 1ns / 1ps

module debouncer #(
    parameter int WIDTH              = 1,
    parameter int SAMPLE_CNT_MAX     = 62500,
    parameter int PULSE_CNT_MAX      = 200,
    parameter int WRAPPING_CNT_WIDTH = $clog2(SAMPLE_CNT_MAX),
    parameter int SAT_CNT_WIDTH      = $clog2(PULSE_CNT_MAX) + 1
) (
    input  wire              clk,
    input  wire  [WIDTH-1:0] glitchy_signal,
    output logic [WIDTH-1:0] debounced_signal
);

    logic [WRAPPING_CNT_WIDTH-1:0] wrapping_counter;
    logic [SAT_CNT_WIDTH-1:0]      saturating_counter [WIDTH];

    logic             sample_pulse;
    logic [WIDTH-1:0] saturate_enable;
    logic [WIDTH-1:0] saturate_reset;

    // Power-on state (there is no reset port). Synthesizable on FPGAs and
    // honored by simulators; gives a clean start instead of X.
    initial begin
        wrapping_counter = '0;
        foreach (saturating_counter[i])
            saturating_counter[i] = '0;
    end

    always_ff @(posedge clk) begin
        // Divide clk down to one sample tick every SAMPLE_CNT_MAX cycles.
        if (wrapping_counter == WRAPPING_CNT_WIDTH'(SAMPLE_CNT_MAX - 1))
            wrapping_counter <= '0;
        else
            wrapping_counter <= wrapping_counter + WRAPPING_CNT_WIDTH'(1);

        // Per-bit saturating counters: count up while held, reset while released.
        for (int i = 0; i < WIDTH; i++) begin
            if (saturate_reset[i])
                saturating_counter[i] <= '0;
            else if (saturate_enable[i] && (saturating_counter[i] < SAT_CNT_WIDTH'(PULSE_CNT_MAX)))
                saturating_counter[i] <= saturating_counter[i] + SAT_CNT_WIDTH'(1);
        end
    end

    always_comb begin
        sample_pulse    = (wrapping_counter == WRAPPING_CNT_WIDTH'(SAMPLE_CNT_MAX - 1));
        saturate_enable = {WIDTH{sample_pulse}} &  glitchy_signal;
        saturate_reset  = {WIDTH{sample_pulse}} & ~glitchy_signal;

        for (int i = 0; i < WIDTH; i++)
            debounced_signal[i] = (saturating_counter[i] >= SAT_CNT_WIDTH'(PULSE_CNT_MAX));
    end

endmodule
