// rtl/aclk_readout/aclk_readout_core.sv
//
// ACLK readout datapath: it takes the decoded outputs of ACLK_RCV, stamps each
// event with a hardware timestamp, and buffers every good, non-null event into a
// dual-clock FIFO so the PS-facing AXI logic can read it on its own clock.
//
// Timestamp: a free-running 64-bit counter in the rx_clk (recovered-RX) domain,
// latched into the packed word at the event's VALID cycle. The `pps` input
// synchronously clears it, so once a White Rabbit pulse-per-second is wired in,
// the timestamp becomes "rx_clk ticks since the last PPS" (a seconds field can
// be layered on later). For bring-up, tie pps low and it is a plain monotonic
// counter. Because the timestamp rides inside the FIFO word, it crosses the
// clock domain together with its event and needs no separate CDC.
//
// Per event (one ACLK_VALID strobe):
//   - null / idle packets (ACLK_EVENT[7:0] == 0xFF) are dropped.
//   - everything else is packed into { TS[63:0], EVENT[15:0], DATA[63:0] } and
//     pushed into the FIFO.
// ACLK_ERROR (a bad-CRC packet) never asserts ACLK_VALID, so it never enters the
// FIFO; the AXI layer counts it separately.

`timescale 1ns / 1ps

module aclk_readout_core #(
    parameter int ADDR_WIDTH = 6           // FIFO depth = 2**ADDR_WIDTH
) (
    // ---- rx_clk domain: decoded stream from ACLK_RCV ----
    input  logic         rx_clk,
    input  logic         rx_rstn,          // async, active-low
    input  logic         pps,              // optional: synchronously clears the timestamp
    input  logic         aclk_valid,
    input  logic [15:0]  aclk_event,
    input  logic [63:0]  aclk_data,

    // ---- rd_clk domain: PS-facing read side ----
    input  logic         rd_clk,
    input  logic         rd_rstn,          // async, active-low
    input  logic         rd_en,
    output logic [143:0] rd_data,          // { TS[63:0], EVENT[15:0], DATA[63:0] }
    output logic         empty,
    output logic         overflow,         // an event was lost (FIFO was full)
    output logic         dropped_null      // rx-domain strobe: a null packet was dropped
);

    // Free-running timestamp counter (recovered-RX domain).
    logic [63:0] ts;
    always_ff @(posedge rx_clk or negedge rx_rstn) begin
        if (!rx_rstn) ts <= '0;
        else if (pps)  ts <= '0;
        else           ts <= ts + 1'b1;
    end

    // A null / idle packet is one whose low event byte is 0xFF (per top_module.v).
    wire is_null = (aclk_event[7:0] == 8'hFF);
    wire push    = aclk_valid && !is_null;

    assign dropped_null = aclk_valid && is_null;

    wire [143:0] packed_word = {ts, aclk_event, aclk_data};

    async_fifo #(.WIDTH(144), .ADDR_WIDTH(ADDR_WIDTH)) u_fifo (
        .wr_clk   (rx_clk),
        .wr_rstn  (rx_rstn),
        .wr_en    (push),
        .wr_data  (packed_word),
        .full     (),               // intentionally unused; overflow is the alarm
        .overflow (overflow),
        .rd_clk   (rd_clk),
        .rd_rstn  (rd_rstn),
        .rd_en    (rd_en),
        .rd_data  (rd_data),
        .empty    (empty)
    );

endmodule
