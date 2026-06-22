// rtl/aclk_lite/clk_byte_framer.sv
//
// Length-aware byte framer for the unified ACLK/TCLK decoder. Consumes the recovered
// NRZ bit stream from serdec4_9MHz (SCLK strobe + SDATA cell value) and assembles
// real-ISD-framed events: each byte = start(0) + 8 data (MSB first) + even parity,
// bytes back-to-back, a frame ends when the cell after a byte's parity is idle (1).
// Dispatches by accumulated byte count: 1 = TCLK event {0x00,b0}; 2 = ACLK event
// {b0,b1}; 12 = full packet event {b0,b1} + data {b2..b9} (bytes 10/11 = CRC/control,
// captured but ignored). A per-byte parity failure or any other byte count -> a
// one-cycle parity_error with no event. Output interface matches aclk_lite_decoder.
// See docs/aclk-lite-framing.md.

`timescale 1ns / 1ps

module clk_byte_framer (
    input  logic        clk,            // 40 MHz framer clock
    input  logic        rstn,           // async, active-low
    input  logic        sclk,           // recovered bit clock from serdec (one pulse/cell)
    input  logic        sdata,          // recovered NRZ data from serdec
    output logic        event_valid,
    output logic [15:0] event_id,
    output logic        data_valid,
    output logic [63:0] data,
    output logic        parity_error,
    output logic        is_tclk
);
    // Synchronize sclk + sdata into clk and detect sclk rising edge (one pulse/cell).
    logic sclk_cap, sclk_smpl, sclk_edge, sdata_cap, sdata_smpl;
    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            sclk_cap <= 1'b0; sclk_smpl <= 1'b0; sclk_edge <= 1'b0;
            sdata_cap <= 1'b1; sdata_smpl <= 1'b1;
        end else begin
            sclk_cap <= sclk; sclk_smpl <= sclk_cap; sclk_edge <= sclk_smpl;
            sdata_cap <= sdata; sdata_smpl <= sdata_cap;
        end
    end
    wire sclk_pe = sclk_smpl & ~sclk_edge;   // one clk pulse per recovered cell
    wire nrz_bit = sdata_smpl;              // the recovered NRZ bit value at sclk_pe

    localparam logic [1:0] ST_IDLE = 2'd0, ST_DATA = 2'd1, ST_PARITY = 2'd2, ST_PEEK = 2'd3;
    logic [1:0]  state;
    logic [2:0]  data_cnt;     // data bits seen in the current byte (0..7)
    logic [7:0]  cur_byte;     // assembling byte (MSB first)
    logic        par_acc;      // running XOR of the current byte's data bits
    logic [3:0]  byte_cnt;     // bytes completed this frame
    logic        frame_bad;    // a per-byte parity error occurred this frame
    logic [95:0] frame_buf;    // up to 12 bytes, first byte ends up in the high slot

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            state <= ST_IDLE;
            data_cnt <= 3'd0; cur_byte <= 8'd0; par_acc <= 1'b0;
            byte_cnt <= 4'd0; frame_bad <= 1'b0; frame_buf <= 96'd0;
            event_valid <= 1'b0; event_id <= 16'd0; data_valid <= 1'b0; data <= 64'd0;
            parity_error <= 1'b0; is_tclk <= 1'b0;
        end else begin
            event_valid  <= 1'b0;          // outputs are 1-cycle strobes
            data_valid   <= 1'b0;
            parity_error <= 1'b0;
            is_tclk      <= 1'b0;
            if (sclk_pe) begin
                case (state)
                    ST_IDLE: begin
                        if (nrz_bit == 1'b0) begin         // start cell of byte 0
                            data_cnt  <= 3'd0;
                            par_acc   <= 1'b0;
                            cur_byte  <= 8'd0;
                            byte_cnt  <= 4'd0;
                            frame_bad <= 1'b0;
                            state     <= ST_DATA;
                        end
                    end
                    ST_DATA: begin
                        cur_byte <= {cur_byte[6:0], nrz_bit};  // MSB first
                        par_acc  <= par_acc ^ nrz_bit;
                        if (data_cnt == 3'd7) state <= ST_PARITY;
                        data_cnt <= data_cnt + 3'd1;
                    end
                    ST_PARITY: begin
                        if (par_acc != nrz_bit) frame_bad <= 1'b1;   // even parity: XOR(data)==parity
                        frame_buf <= {frame_buf[87:0], cur_byte};     // newest byte into low slot
                        byte_cnt  <= byte_cnt + 4'd1;
                        state     <= ST_PEEK;
                    end
                    ST_PEEK: begin
                        if (nrz_bit == 1'b0) begin         // start of the next byte
                            data_cnt <= 3'd0;
                            par_acc  <= 1'b0;
                            cur_byte <= 8'd0;
                            state    <= ST_DATA;
                        end else begin                     // idle cell -> frame ended
                            state <= ST_IDLE;
                            if (frame_bad) begin
                                parity_error <= 1'b1;
                            end else begin
                                case (byte_cnt)
                                    4'd1: begin
                                        event_id    <= {8'h00, frame_buf[7:0]};
                                        is_tclk     <= 1'b1;
                                        event_valid <= 1'b1;
                                    end
                                    4'd2: begin
                                        event_id    <= frame_buf[15:0];
                                        event_valid <= 1'b1;
                                    end
                                    4'd12: begin
                                        event_id    <= frame_buf[95:80];   // bytes 0,1
                                        data        <= frame_buf[79:16];   // bytes 2..9
                                        event_valid <= 1'b1;
                                        data_valid  <= 1'b1;
                                    end
                                    default: parity_error <= 1'b1;   // malformed length
                                endcase
                            end
                        end
                    end
                    default: state <= ST_IDLE;
                endcase
            end
        end
    end
endmodule
