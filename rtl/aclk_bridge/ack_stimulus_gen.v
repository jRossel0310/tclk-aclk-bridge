module ACLK_STIMULUS_GEN (
    input  wire        CLK1,
    input  wire        RESETn,
    input  wire        enable,
    
    // BRAM Port B interface - ALL signals
    output wire        bram_clkb,       // Clock
    output wire        bram_enb,        // Enable (always on)
    output wire        bram_rstb,       // Reset (tie low)
    output reg  [9:0]  bram_addrb,      // Address
    input  wire [17:0] bram_doutb,      // Data out from BRAM
    
    // If you have output register in BRAM config:
    output wire        bram_regceb,     // Output register enable
    
    // Output to ACLK_RCV
    output wire [15:0] DATA_TO_XCVR,
    output wire [1:0]  K_TO_XCVR
);

    // Connect BRAM control signals
    assign bram_clkb   = CLK1;          // Use same clock
    assign bram_enb    = 1'b1;          // Always enabled
    assign bram_rstb   = 1'b0;          // No reset (or use !RESETn)
    assign bram_regceb = 1'b1;          // Output register enabled
    
    // Pass through BRAM data
    assign DATA_TO_XCVR = bram_doutb[15:0];
    assign K_TO_XCVR    = bram_doutb[17:16];
    
    // Address counter
    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            bram_addrb <= 10'd0;
        else if (enable)
            bram_addrb <= bram_addrb + 1'b1;
    end

endmodule