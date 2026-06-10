// ------------------------------------------------------------
// LFSR80.v
// Translated from VHDL
// ------------------------------------------------------------

module LFSR80 (
    input  wire        CLK,
    input  wire        RESETn,
    input  wire        ADV,
    input  wire        LOAD,
    input  wire [79:0] D,
    output wire [79:0] Q
);

    reg [79:0] Q_int;

    // --------------------------------------------------------
    // LFSR process
    // --------------------------------------------------------

    always @(posedge CLK or negedge RESETn) begin
        if (!RESETn) begin
            Q_int <= 80'h00000000000000000000;

        end else begin
            if (LOAD) begin
                Q_int <= D;

            end else if (ADV) begin
                // Shift
                Q_int[79:1] <= Q_int[78:0];

                // Feedback bit
                if (Q_int == 80'hFFFFFFFFFFFFFFFFFFFF) begin
                    Q_int[0] <= 1'b0;
                end else begin
                    Q_int[0] <= ~(Q_int[79] ^ Q_int[78] ^ Q_int[42] ^ Q_int[41]);
                end

            end else begin
                Q_int <= Q_int; // explicit hold (as in VHDL)
            end
        end
    end

    // --------------------------------------------------------
    // Output
    // --------------------------------------------------------

    assign Q = Q_int;

endmodule

