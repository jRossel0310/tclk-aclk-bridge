// rtl/aclk_lite_bridge.v
// Decoded-back ACLK real events -> drive aclk_lite_encoder (full 12-byte frames).
// Real events (event[7:0]!=0xFF) cross rx_clk->enc_clk via async_fifo; on the enc
// side, when the encoder is idle, pop one and pulse start. FIFO-full drops + counts.
`timescale 1ns / 1ps
module aclk_lite_bridge (
    input  wire        rx_clk,
    input  wire        rx_rstn,
    input  wire        aclk_valid,
    input  wire [15:0] aclk_event,
    input  wire [63:0] aclk_data,
    input  wire        enc_clk,
    input  wire        enc_rstn,
    output reg  [15:0] enc_event_id,
    output reg  [63:0] enc_data,
    output wire [1:0]  enc_frame_type,
    output reg         enc_start,
    input  wire        enc_busy,
    output reg  [15:0] dropped_count
);
    assign enc_frame_type = 2'd2;          // full 12-byte packet

    wire is_real = aclk_valid && (aclk_event[7:0] != 8'hFF);

    wire        full, empty;
    wire [79:0] rd_data;
    reg         rd_en;

    async_fifo #(.WIDTH(80), .ADDR_WIDTH(4)) u_fifo (
        .wr_clk(rx_clk), .wr_rstn(rx_rstn), .wr_en(is_real && !full),
        .wr_data({aclk_event, aclk_data}), .full(full), .overflow(),
        .rd_clk(enc_clk), .rd_rstn(enc_rstn), .rd_en(rd_en),
        .rd_data(rd_data), .empty(empty)
    );

    // drop accounting (rx_clk domain)
    always @(posedge rx_clk or negedge rx_rstn) begin
        if (!rx_rstn) dropped_count <= 16'd0;
        else if (is_real && full) dropped_count <= dropped_count + 16'd1;
    end

    // enc_clk dispatch FSM: pop -> latch -> start -> wait busy HIGH -> wait busy LOW
    localparam [2:0] S_IDLE=3'd0, S_LATCH=3'd1, S_START=3'd2, S_WAIT_BUSY=3'd3, S_WAIT_DONE=3'd4;
    reg [2:0] st;
    always @(posedge enc_clk or negedge enc_rstn) begin
        if (!enc_rstn) begin
            st <= S_IDLE; rd_en <= 1'b0; enc_start <= 1'b0;
            enc_event_id <= 16'd0; enc_data <= 64'd0;
        end else begin
            rd_en <= 1'b0; enc_start <= 1'b0;
            case (st)
                S_IDLE:      if (!empty && !enc_busy) begin rd_en <= 1'b1; st <= S_LATCH; end
                S_LATCH:     begin enc_event_id <= rd_data[79:64]; enc_data <= rd_data[63:0]; st <= S_START; end
                S_START:     begin enc_start <= 1'b1; st <= S_WAIT_BUSY; end
                S_WAIT_BUSY: if (enc_busy)  st <= S_WAIT_DONE;   // frame actually started
                S_WAIT_DONE: if (!enc_busy) st <= S_IDLE;        // frame finished -> ready for next
                default:     st <= S_IDLE;
            endcase
        end
    end
endmodule
