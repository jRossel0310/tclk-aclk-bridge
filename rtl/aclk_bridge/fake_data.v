`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 02/18/2026 02:04:25 PM
// Design Name: 
// Module Name: fake_data
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

module fake_tx_data (
    input  wire        CLK,        // ACLK 60 MHz
    input  wire        RESETn,

    output reg  [15:0] DATA_OUT,   // To gtwiz_userdata_tx_in
    output reg  [1:0]  K_OUT       // To txctrl2_in[1:0]
);

    reg [7:0] counter;
    reg [3:0] comma_ctr;

    // K28.5 = 8'hBC, a standard comma character
    localparam K285 = 8'hBC;

    always @(posedge CLK or negedge RESETn) begin
        if (!RESETn) begin
            counter    <= 8'h00;
            comma_ctr  <= 4'h0;
            DATA_OUT   <= 16'h0000;
            K_OUT      <= 2'b00;
        end else begin
            comma_ctr <= comma_ctr + 1'b1;

            if (comma_ctr == 4'h0) begin
                // Every 16 cycles, send a K28.5 comma on byte 0
                // Data byte 1 carries counter value
                DATA_OUT <= {counter, K285};   // [15:8]=data, [7:0]=K28.5
                K_OUT    <= 2'b01;             // byte 0 is K-char
                counter  <= counter + 1'b1;
            end else begin
                // Normal data: two incrementing bytes
                DATA_OUT <= {counter + 1'b1, counter};
                K_OUT    <= 2'b00;             // both are data bytes
                counter  <= counter + 2'd2;
            end
        end
    end


endmodule