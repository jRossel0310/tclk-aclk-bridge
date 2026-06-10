// ------------------------------------------------------------
// GEARBOX_16_TO_96.v
// Translated from VHDL
// ------------------------------------------------------------

module GEARBOX_16_TO_96 (
    input  wire         CLK1,
    input  wire         RESETn,

    input  wire [15:0]  DATA16,
    input  wire [1:0]   K_IN,

    output wire [95:0]  DATA96,
    output wire [11:0]  K_OUT,
    output wire         DATA96_VALID,
    output wire         SEQ_CTR_3
);

    // --------------------------------------------------------
    // Internal registers
    // --------------------------------------------------------

    reg [15:0] DATA16_reg0, DATA16_reg1;
    reg [1:0]  K_IN_reg0, K_IN_reg1;

    reg [95:0] DATA96_int;
    reg [11:0] K_OUT_int;
    reg        DATA96_VALID_int;

    reg [3:0]  seq_ctr;

    reg [95:0] DATA96_a;
    reg [11:0] k_a;

    // --------------------------------------------------------
    // Sequence counter
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            seq_ctr <= 4'h0;
        end else begin
            if (K_IN[1] && (DATA16[15:8] == 8'hBC)) begin
                seq_ctr <= 4'h8;
            end else if (K_IN[0] && (DATA16[7:0] == 8'hBC)) begin
                seq_ctr <= 4'h0;
            end else begin
                seq_ctr <= seq_ctr + 4'd1;
            end
        end
    end

    assign SEQ_CTR_3 = seq_ctr[3];

    // --------------------------------------------------------
    // Input pipelining
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            DATA16_reg0 <= 16'h0000;
            DATA16_reg1 <= 16'h0000;
            K_IN_reg0   <= 2'b00;
            K_IN_reg1   <= 2'b00;
        end else begin
            DATA16_reg0 <= DATA16;
            DATA16_reg1 <= DATA16_reg0;
            K_IN_reg0   <= K_IN;
            K_IN_reg1   <= K_IN_reg0;
        end
    end

    // --------------------------------------------------------
    // Gearbox data / K assembly
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            DATA96_a <= 96'b0;
            // NOTE: k_a is intentionally NOT reset (matches VHDL)
        end else begin
            case (seq_ctr)

                4'h0: begin
                    DATA96_a <= {DATA16_reg0[7:0], DATA16_reg0[15:8], DATA96_a[79:0]};
                    k_a      <= {K_IN_reg0[0], K_IN_reg0[1], k_a[9:0]};
                end

                4'h1: begin
                    DATA96_a <= {DATA96_a[95:80], DATA16_reg0[7:0], DATA16_reg0[15:8], DATA96_a[63:0]};
                    k_a      <= {k_a[11:10], K_IN_reg0[0], K_IN_reg0[1], k_a[7:0]};
                end

                4'h2: begin
                    DATA96_a <= {DATA96_a[95:64], DATA16_reg0[7:0], DATA16_reg0[15:8], DATA96_a[47:0]};
                    k_a      <= {k_a[11:8], K_IN_reg0[0], K_IN_reg0[1], k_a[5:0]};
                end

                4'h3: begin
                    DATA96_a <= {DATA96_a[95:48], DATA16_reg0[7:0], DATA16_reg0[15:8], DATA96_a[31:0]};
                    k_a      <= {k_a[11:6], K_IN_reg0[0], K_IN_reg0[1], k_a[3:0]};
                end

                4'h4: begin
                    DATA96_a <= {DATA96_a[95:32], DATA16_reg0[7:0], DATA16_reg0[15:8], DATA96_a[15:0]};
                    k_a      <= {k_a[11:4], K_IN_reg0[0], K_IN_reg0[1], k_a[1:0]};
                end

                4'h5: begin
                    DATA96_a <= {DATA96_a[95:16], DATA16_reg0[7:0], DATA16_reg0[15:8]};
                    k_a      <= {k_a[11:2], K_IN_reg0[0], K_IN_reg0[1]};
                end

                4'h8: begin
                    DATA96_a <= {DATA16_reg0[15:8], DATA16[7:0], DATA96_a[79:0]};
                    k_a      <= {K_IN_reg0[1], K_IN[0], k_a[9:0]};
                end

                4'h9: begin
                    DATA96_a <= {DATA96_a[95:80], DATA16_reg0[15:8], DATA16[7:0], DATA96_a[63:0]};
                    k_a      <= {k_a[11:10], K_IN_reg0[1], K_IN[0], k_a[7:0]};
                end

                4'hA: begin
                    DATA96_a <= {DATA96_a[95:64], DATA16_reg0[15:8], DATA16[7:0], DATA96_a[47:0]};
                    k_a      <= {k_a[11:8], K_IN_reg0[1], K_IN[0], k_a[5:0]};
                end

                4'hB: begin
                    DATA96_a <= {DATA96_a[95:48], DATA16_reg0[15:8], DATA16[7:0], DATA96_a[31:0]};
                    k_a      <= {k_a[11:6], K_IN_reg0[1], K_IN[0], k_a[3:0]};
                end

                4'hC: begin
                    DATA96_a <= {DATA96_a[95:32], DATA16_reg0[15:8], DATA16[7:0], DATA96_a[15:0]};
                    k_a      <= {k_a[11:4], K_IN_reg0[1], K_IN[0], k_a[1:0]};
                end

                4'hD: begin
                    DATA96_a <= {DATA96_a[95:16], DATA16_reg0[15:8], DATA16[7:0]};
                    k_a      <= {k_a[11:2], K_IN_reg0[1], K_IN[0]};
                end

                default: begin
                    DATA96_a <= DATA96_a;
                    k_a      <= k_a;
                end
            endcase
        end
    end

    // --------------------------------------------------------
    // Output capture & valid flag
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            DATA96_int       <= 96'b0;
            K_OUT_int        <= 12'b0;
            DATA96_VALID_int <= 1'b0;
        end else begin
            if ((seq_ctr == 4'h0) || (seq_ctr == 4'h8)) begin
                DATA96_int       <= DATA96_a;
                K_OUT_int        <= k_a;
                DATA96_VALID_int <= 1'b1;
            end else begin
                DATA96_VALID_int <= 1'b0;
            end
        end
    end

    // --------------------------------------------------------
    // Outputs
    // --------------------------------------------------------

    assign DATA96       = DATA96_int;
    assign K_OUT        = K_OUT_int;
    assign DATA96_VALID = DATA96_VALID_int;

endmodule
