module CRC8_CALC (

    input  wire        CLK,

    input  wire        RESETn,

    input  wire        CALC,

    input  wire [87:0] DATA,

    output reg  [7:0]  CRC,

    output reg         CRC_VALID

);



    // Internal signals

    reg [87:0] data_reg;

    reg        calc0, calc1;

    reg [7:0]  CRC_int;

    reg [7:0]  crc_calc;



    integer i;



    // Register input data

    always @(posedge CLK or negedge RESETn) begin

        if (!RESETn) begin

            data_reg <= 88'h0;

        end else if (CALC) begin

            data_reg <= DATA;

        end

    end



    // CRC calculation combinational
    reg [7:0] result;

    always @(*) begin


        result = ~data_reg[87:80];  // Start with inverted MSB byte



        for (i = 87; i >= 8; i = i - 1) begin

            if (result[7]) begin

                result = {result[6:0], data_reg[i-8]} ^ 8'h2F;

            end else begin

                result = {result[6:0], data_reg[i-8]};

            end

        end



        crc_calc = result;

    end



    // Shift CALC through two registers to generate CRC_VALID

    always @(posedge CLK or negedge RESETn) begin

        if (!RESETn) begin

            calc0 <= 1'b0;

            calc1 <= 1'b0;

        end else begin

            calc0 <= CALC;

            calc1 <= calc0;

        end

    end



    // Register the CRC output

    always @(posedge CLK or negedge RESETn) begin

        if (!RESETn) begin

            CRC_int <= 8'h00;

        end else if (calc0) begin

            CRC_int <= crc_calc;

        end

    end



    // Connect outputs

    always @(posedge CLK or negedge RESETn) begin

        if (!RESETn) begin

            CRC <= 8'h00;

            CRC_VALID <= 1'b0;

        end else begin

            CRC <= CRC_int;

            CRC_VALID <= calc1;

        end

    end



endmodule