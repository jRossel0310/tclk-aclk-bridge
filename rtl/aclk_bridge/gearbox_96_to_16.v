module GEARBOX_96_TO_16 (
    input  wire        CLK1,
    input  wire        RESETn,
    input  wire [95:0] DATA96,
    input  wire [11:0] K_IN,
    input  wire        DATA96_VALID,
    output wire [15:0] DATA16,
    output wire [1:0]  K_OUT
);

    // Internal Signals
    reg [95:0] DATA96_regA;
    reg [11:0] K_IN_regA;
    reg [15:0] dataQ;
    reg [1:0]  k_Q;
    reg [3:0]  counter;

    // DATA96 and K_IN Input Latch
    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            DATA96_regA <= 96'b0;
            K_IN_regA   <= 12'b0;
        end else begin
            if (DATA96_VALID) begin
                DATA96_regA <= DATA96;
                K_IN_regA   <= K_IN;
            end
            // Implicit hold if DATA96_VALID is low
        end
    end

    // Sequence Counter
    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            counter <= 4'h0;
        end else begin
            if (DATA96_VALID) begin
                counter <= 4'h0;
            end else begin
                counter <= counter + 1'b1;
            end
        end
    end

    // Multiplexer Logic (96:16 Gearbox)
    // Note: The byte-swapping {low_byte, high_byte} is preserved from the VHDL code
    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            dataQ <= 16'h0000;
            k_Q   <= 2'b00;
        end else begin
            case (counter)
                4'h0: begin
                    dataQ <= {DATA96_regA[87:80], DATA96_regA[95:88]};
                    k_Q   <= {K_IN_regA[10], K_IN_regA[11]};
                end
                4'h1: begin
                    dataQ <= {DATA96_regA[71:64], DATA96_regA[79:72]};
                    k_Q   <= {K_IN_regA[8], K_IN_regA[9]};
                end
                4'h2: begin
                    dataQ <= {DATA96_regA[55:48], DATA96_regA[63:56]};
                    k_Q   <= {K_IN_regA[6], K_IN_regA[7]};
                end
                4'h3: begin
                    dataQ <= {DATA96_regA[39:32], DATA96_regA[47:40]};
                    k_Q   <= {K_IN_regA[4], K_IN_regA[5]};
                end
                4'h4: begin
                    dataQ <= {DATA96_regA[23:16], DATA96_regA[31:24]};
                    k_Q   <= {K_IN_regA[2], K_IN_regA[3]};
                end
                4'h5: begin
                    dataQ <= {DATA96_regA[7:0], DATA96_regA[15:8]};
                    k_Q   <= {K_IN_regA[0], K_IN_regA[1]};
                end
                default: begin
                    dataQ <= 16'h0000;
                    k_Q   <= 2'b00;
                end
            endcase
        end
    end

    // Output assignments
    assign DATA16 = dataQ;
    assign K_OUT  = k_Q;

endmodule
