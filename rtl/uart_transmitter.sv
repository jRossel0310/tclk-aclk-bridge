// rtl/uart_transmitter.sv
//
// 8-N-1 UART transmitter (8 data bits, no parity, 1 stop bit), LSB first.
//
// Ready/valid handshake on the input side:
//   - `data_in_ready` is high whenever the transmitter is idle.
//   - A byte is accepted on any cycle where both `data_in_valid` and
//     `data_in_ready` are high; transmission of that byte then begins.
// `serial_out` idles high. The line sequence per byte is:
//   start(0), data[0..7] (LSB first), stop(1).
//
// Bit timing is CLOCK_FREQ / BAUD_RATE clk cycles per symbol.

`timescale 1ns / 1ps

module uart_transmitter #(
    parameter int CLOCK_FREQ = 125_000_000,
    parameter int BAUD_RATE  = 115_200
) (
    input  logic       clk,
    input  logic       reset,        // synchronous, active-high

    input  logic [7:0] data_in,
    input  logic       data_in_valid,
    output logic       data_in_ready,

    output logic       serial_out
);

    localparam int SYMBOL_EDGE_TIME    = CLOCK_FREQ / BAUD_RATE;
    localparam int CLOCK_COUNTER_WIDTH = $clog2(SYMBOL_EDGE_TIME);

    localparam logic IDLE    = 1'b0;
    localparam logic SENDING = 1'b1;

    logic state, next_state;

    logic [CLOCK_COUNTER_WIDTH-1:0] baud_counter;   // cycles within a symbol
    logic [3:0]                     bit_sending;     // index of bit being sent

    // High on the last clk cycle of each symbol.
    logic symbol_edge;
    assign symbol_edge = (baud_counter == CLOCK_COUNTER_WIDTH'(SYMBOL_EDGE_TIME - 1));

    // Ready to accept a new byte only while idle.
    assign data_in_ready = (state == IDLE);

    // 10-bit shift register: {stop, data[7:0], start}, shifted out LSB first.
    logic [9:0] frame;
    assign serial_out = frame[0];

    // State register, counters, and the outgoing shift register.
    always_ff @(posedge clk) begin
        state <= next_state;

        if (next_state == IDLE) begin
            baud_counter <= '0;
            bit_sending  <= '0;
            frame        <= 10'b11_1111_1111;        // line idles high
        end else begin // next_state == SENDING
            if (state == IDLE) begin
                // First sending cycle: latch the frame {stop, data, start}.
                frame <= {1'b1, data_in, 1'b0};
            end else if (symbol_edge) begin
                // Symbol complete: advance to the next bit, shift in idle '1'.
                baud_counter <= '0;
                bit_sending  <= bit_sending + 4'd1;
                frame        <= {1'b1, frame[9:1]};
            end else begin
                baud_counter <= baud_counter + CLOCK_COUNTER_WIDTH'(1);
            end
        end
    end

    // Next-state logic.
    always_comb begin
        next_state = state;
        if (reset) begin
            next_state = IDLE;
        end else begin
            unique case (state)
                IDLE:    if (data_in_valid)                       next_state = SENDING;
                SENDING: if (bit_sending == 4'd9 && symbol_edge)  next_state = IDLE;
            endcase
        end
    end

endmodule
