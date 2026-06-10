`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 02/25/2026 11:55:26 AM
// Design Name: 
// Module Name: FrameEncoder
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////


//Modified Manchester Frame Encoder
//author: Jose Berlioz
//Needs a clock that is 2x as fast for the bitencoder.

module FrameEncoder(
  input wire clk_10Mhz,
  input wire clk_20Mhz,
  input wire reset,
  input wire rd_en,
  input [7:0] frame_in,
  output reg busy,
  output wire encoded_data
);

  localparam IDLE = 3'b000, SEND_ZERO = 3'b001, SEND_DATA = 3'b010,SEND_PARITY = 3'b011, SEND_ONES = 3'b100, DONE = 3'b111;
  reg [2:0] state = IDLE;
  reg [2:0] bit_counter = 0;
  reg valid_bit;
  reg encoder_data_in;
  reg parity_bit;

  always @(posedge clk_10Mhz or posedge reset) begin
    if (reset) begin
      valid_bit <= 1'b0;
      busy  <= 1'b1;
      state <= IDLE;
      bit_counter <= 0;
      encoder_data_in     <= 1'b1;    //Send Zero
    end else begin
      case(state)  
        IDLE: begin
          valid_bit   <= 1'b0;
          busy        <= 1'b0;
          parity_bit  <= 1'b0;
            if(rd_en == 1'b1) begin
                busy          <= 1'b1;
                state         <= SEND_ZERO;
                bit_counter   <= 0;
            end
        end
        SEND_ZERO: begin
          encoder_data_in     <= 1'b0;    //Send Zero
          valid_bit           <= 1'b1;
          state               <= SEND_DATA;
          bit_counter         <= 0;
        end
        SEND_DATA: begin
          bit_counter <= bit_counter + 1;
          encoder_data_in     <= frame_in[7-bit_counter];
          parity_bit          <= parity_bit + frame_in[7-bit_counter];
          if (bit_counter == 7) begin
            state             <= SEND_PARITY;
            bit_counter       <= 0;
          end
        end
        SEND_PARITY: begin
          encoder_data_in   <= parity_bit;  
          state             <= SEND_ONES;
        end
        SEND_ONES: begin
          encoder_data_in   <= 1'b1;  
          bit_counter       <= bit_counter + 1;

          if (bit_counter == 1) begin
            state           <= DONE;
          end
        end
        DONE: begin
            state <= IDLE;
            valid_bit   <= 1'b0;
            busy        <= 1'b0;
        end
      endcase
    end
  end  

BitEncoder encoder(
  .clk(clk_20Mhz),
  .reset(reset),
  .valid(valid_bit),
  .data_in(encoder_data_in),
  .encoded_data(encoded_data)
);

endmodule
