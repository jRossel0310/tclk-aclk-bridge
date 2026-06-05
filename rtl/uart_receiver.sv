// rtl/uart_receiver.sv
//
// 8-N-1 UART receiver (8 data bits, no parity, 1 stop bit), LSB first.
//
// Drives a ready/valid handshake on the output side:
//   - `data_out_valid` goes high once a full byte has been received and stays
//     high until the consumer accepts it by asserting `data_out_ready`.
//   - `data_out` holds the received byte while valid.
//
// `serial_in` is expected to idle high; reception starts on the falling edge
// (start bit). Each symbol is CLOCK_FREQ / BAUD_RATE clk cycles long and each
// bit is sampled at the midpoint of its symbol for noise immunity.
//
// NOTE: `serial_in` must already be synchronous to `clk`; feed an asynchronous
// line through a synchronizer first.

`timescale 1ns / 1ps

module uart_receiver #(
    parameter int CLOCK_FREQ = 125_000_000,
    parameter int BAUD_RATE  = 115_200
) (
    input  logic       clk,
    input  logic       reset,          // synchronous, active-high

    output logic [7:0] data_out,
    output logic       data_out_valid,
    input  logic       data_out_ready,

    input  logic       serial_in
);

    localparam int SYMBOL_EDGE_TIME    = CLOCK_FREQ / BAUD_RATE;
    localparam int SAMPLE_TIME         = SYMBOL_EDGE_TIME / 2;
    localparam int CLOCK_COUNTER_WIDTH = $clog2(SYMBOL_EDGE_TIME);

    logic symbol_edge;   // last cycle of the current symbol
    logic sample;        // midpoint of the current symbol
    logic start;         // time to begin receiving a new character
    logic rx_running;    // currently shifting a character in
    logic has_byte;      // a complete byte is waiting to be read

    logic [9:0]                     rx_shift;       // {stop, data[7:0], start}
    logic [3:0]                     bit_counter;    // bits left to receive
    logic [CLOCK_COUNTER_WIDTH-1:0] clock_counter;  // cycles within a symbol

    //--| Signal assignments |--------------------------------------------------
    assign symbol_edge = (clock_counter == CLOCK_COUNTER_WIDTH'(SYMBOL_EDGE_TIME - 1));
    assign sample      = (clock_counter == CLOCK_COUNTER_WIDTH'(SAMPLE_TIME));
    assign start       = !serial_in && !rx_running;   // falling edge while idle
    assign rx_running  = (bit_counter != 4'd0);

    assign data_out       = rx_shift[8:1];
    assign data_out_valid = has_byte && !rx_running;

    //--| Symbol counter |------------------------------------------------------
    // Counts clk cycles within a symbol; restarts at each symbol edge / start.
    always_ff @(posedge clk) begin
        if (start || reset || symbol_edge)
            clock_counter <= '0;
        else
            clock_counter <= clock_counter + CLOCK_COUNTER_WIDTH'(1);
    end

    //--| Bit counter |---------------------------------------------------------
    // Loaded with 10 (start + 8 data + stop) and counted down one per symbol.
    always_ff @(posedge clk) begin
        if (reset)
            bit_counter <= 4'd0;
        else if (start)
            bit_counter <= 4'd10;
        else if (symbol_edge && rx_running)
            bit_counter <= bit_counter - 4'd1;
    end

    //--| Shift register |------------------------------------------------------
    // Sample each bit at the symbol midpoint; LSB arrives first.
    always_ff @(posedge clk) begin
        if (sample && rx_running)
            rx_shift <= {serial_in, rx_shift[9:1]};
    end

    //--| Ready/valid bookkeeping |---------------------------------------------
    always_ff @(posedge clk) begin
        if (reset)
            has_byte <= 1'b0;
        else if (bit_counter == 4'd1 && symbol_edge)  // last (stop) bit done
            has_byte <= 1'b1;
        else if (data_out_ready)
            has_byte <= 1'b0;
    end

endmodule
