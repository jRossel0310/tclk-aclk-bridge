module BitEncoder(
  input wire clk, // This is the 2x clock
  input wire reset, 
  input wire valid, 
  input wire data_in, 
  output reg encoded_data
);

  reg clk_div;
  reg prev_bit;
  reg data_in_sig;
  reg current_bit;

  always @(posedge clk or posedge reset) begin
    if (reset) begin
      clk_div   <= 1'b0;
    end else begin
      clk_div <= ~clk_div;
    end
  end

  always @(*) begin
    if (valid == 1'b1) begin
      current_bit = data_in_sig;
    end
    else begin
      current_bit = 1'b1;
    end  
  end

  always @(posedge clk or posedge reset) begin
    if (reset) begin
      encoded_data <= 1'b0;
      data_in_sig <= 1'b1;
		  prev_bit  <= 1'b0;
    end else begin
      data_in_sig <= data_in;
      //Obtained through K-map
      encoded_data <= (~prev_bit & current_bit) || (~current_bit & ~(prev_bit^clk_div));
      prev_bit    <= (~prev_bit & current_bit)  || (~current_bit & ~(prev_bit^clk_div));
    end
  end
endmodule