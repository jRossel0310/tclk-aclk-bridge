`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 04/02/2024 02:11:37 PM
// Design Name: 
// Module Name: TimelineGeneratorSV
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
module TimelineGeneratorSV(
    input clk_10Mhz,
    input clk_20Mhz,
    input reset,
    output busy,
    output encodedData
    );

// Replacing SystemVerilog struct with flat arrays
parameter NUM_TIMELINE_EVENTS = 6;

// Replacing eventTimepair struct array with two separate arrays
reg [63:0] timeline_eventTime [0:NUM_TIMELINE_EVENTS-1];
reg [7:0]  timeline_tclkEvent [0:NUM_TIMELINE_EVENTS-1];

reg [39:0] currentTime;
reg [39:0] timeLastEventStarted;
reg [7:0]  nextFrame;
reg        enableFrameEncoder;
reg [4:0]  currentTimelineEvent;

// Replacing SystemVerilog enum with localparams
localparam waitForNextEvent = 1'b0;
localparam transmitEvent    = 1'b1;
reg state;

parameter nanosecondsPer10MHzClk        = 100;
parameter EVENT_TIME_LENGTH             = 1300000 / nanosecondsPer10MHzClk;
parameter TIME_TO_WAIT_BEFORE_DISABLING = 2;

// Five Seconds
parameter [63:0] TIME_BEFORE_RESET = 64'd5000000000;

initial begin
    timeline_eventTime[0] = 64'd2000000;  timeline_tclkEvent[0] = 8'h02;
    timeline_eventTime[1] = 64'd4000000;  timeline_tclkEvent[1] = 8'h07;
    timeline_eventTime[2] = 64'd6000000;  timeline_tclkEvent[2] = 8'h09;
    timeline_eventTime[3] = 64'd8000000;  timeline_tclkEvent[3] = 8'h42;
    timeline_eventTime[4] = 64'd10000000; timeline_tclkEvent[4] = 8'h24;
    timeline_eventTime[5] = 64'd12000000; timeline_tclkEvent[5] = 8'h78;

    currentTime           = 0;
    currentTimelineEvent  = 8'h00;
    state                 = waitForNextEvent;
    enableFrameEncoder    = 1'b0;
end

always @(posedge clk_10Mhz) begin
    currentTime <= currentTime + nanosecondsPer10MHzClk;
    case (state)
        waitForNextEvent: begin
            if (currentTimelineEvent < NUM_TIMELINE_EVENTS) begin
                if (currentTime > timeline_eventTime[currentTimelineEvent]) begin
                    enableFrameEncoder   <= 1'b1;
                    nextFrame            <= timeline_tclkEvent[currentTimelineEvent];
                    state                <= transmitEvent;
                    timeLastEventStarted <= currentTime;
                    currentTimelineEvent <= currentTimelineEvent + 1;
                end
            end
            if (TIME_BEFORE_RESET - nanosecondsPer10MHzClk <= currentTime) begin
                state                <= waitForNextEvent;
                currentTime          <= 0;
                currentTimelineEvent <= 0;
                enableFrameEncoder   <= 1'b0;
            end
        end
        transmitEvent: begin
            if (timeLastEventStarted + TIME_TO_WAIT_BEFORE_DISABLING - 1 >= currentTime) begin
                enableFrameEncoder <= 1'b0;
            end
            if (!busy) begin
                enableFrameEncoder <= 1'b0;
                state              <= waitForNextEvent;
            end
        end
    endcase
end

FrameEncoder frameEncoder(
    .clk_10Mhz(clk_10Mhz),
    .clk_20Mhz(clk_20Mhz),
    .reset(reset),
    .rd_en(enableFrameEncoder),
    .frame_in(nextFrame),
    .busy(busy),
    .encoded_data(encodedData)
);

endmodule