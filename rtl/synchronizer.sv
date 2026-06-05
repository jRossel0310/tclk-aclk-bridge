// rtl/synchronizer.sv
//
// Multi-stage flip-flop synchronizer for bringing an asynchronous signal into
// the `clk` domain. Each bit passes through STAGES back-to-back flops so that
// metastability on the first flop has time to resolve before the value is used.
//
// - Purely a CDC primitive: it does NOT debounce or filter, it only resamples.
// - Latency is STAGES clock cycles. Two stages is the usual minimum.
// - For independent multi-bit buses each bit is synchronized separately; do not
//   use this to cross a multi-bit value where bit-skew matters (use a handshake
//   or gray-coded pointer for that).

`timescale 1ns / 1ps

module synchronizer #(
    parameter int WIDTH  = 1,
    parameter int STAGES = 2   // number of flop stages (>= 2 for MTBF)
) (
    input  wire              clk,
    input  wire  [WIDTH-1:0] async_signal,
    output logic [WIDTH-1:0] sync_signal
);

    // ASYNC_REG keeps the chain packed together (Xilinx) for best MTBF; other
    // tools ignore the attribute harmlessly.
    (* ASYNC_REG = "true" *) logic [WIDTH-1:0] sync_ff [STAGES];

    always_ff @(posedge clk) begin
        sync_ff[0] <= async_signal;
        for (int i = 1; i < STAGES; i++)
            sync_ff[i] <= sync_ff[i-1];
    end

    assign sync_signal = sync_ff[STAGES-1];

endmodule
