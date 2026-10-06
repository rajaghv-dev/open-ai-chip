`timescale 1ns/1ps
// Wishbone master around the gate-level user_project_wrapper (+tiny_ai_core), SDF back-annotated (run_sdf_wrapper.sh).
// Same register accesses and exact expected values as the Caravel firmware (tiny_ai_wb.c): ID, then vision_all_lit 1 1 1 1.
module tiny_ai_wrapper_sdf_tb;
  reg clk = 0, rst = 1;
  reg cyc = 0, stb = 0, we = 0; reg [31:0] adr = 0, dat = 0; reg [3:0] sel = 4'hF;
  wire ack; wire [31:0] rdat;
  wire [37:0] io_out, io_oeb; wire [127:0] la_o; wire [2:0] irq;
  supply1 vccd1, vccd2, vdda1, vdda2; supply0 vssd1, vssd2, vssa1, vssa2;
  wire [28:0] analog_io;
  `ifndef CLK_HALF
 `define CLK_HALF 12.5
`endif
  always #(`CLK_HALF) clk = ~clk;   // 40 MHz default; CLK_HALF override for the negative (too-fast clock) check
  user_project_wrapper uut (
    .vccd1(vccd1), .vccd2(vccd2), .vdda1(vdda1), .vdda2(vdda2), .vssa1(vssa1), .vssa2(vssa2), .vssd1(vssd1), .vssd2(vssd2),
    .analog_io(analog_io), .io_in(38'd0), .io_oeb(io_oeb), .io_out(io_out), .la_data_in(128'd0), .la_data_out(la_o),
    .la_oenb(128'hFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF), .user_clock2(1'b0), .user_irq(irq),
    .wb_clk_i(clk), .wb_rst_i(rst), .wbs_ack_o(ack), .wbs_adr_i(adr), .wbs_cyc_i(cyc), .wbs_dat_i(dat), .wbs_dat_o(rdat),
    .wbs_sel_i(sel), .wbs_stb_i(stb), .wbs_we_i(we));
  integer errors = 0; reg [31:0] r; integer guard;
  task wbw(input [31:0] a, input [31:0] d); begin
    @(posedge clk); #1 adr = a; dat = d; we = 1; cyc = 1; stb = 1;
    @(posedge clk); while (ack !== 1'b1) @(posedge clk);
    #1 cyc = 0; stb = 0; we = 0; end endtask
  task wbr(input [31:0] a, output [31:0] d); begin
    @(posedge clk); #1 adr = a; we = 0; cyc = 1; stb = 1;
    @(posedge clk); while (ack !== 1'b1) @(posedge clk);
    d = rdat; #1 cyc = 0; stb = 0; end endtask
  task chk(input [255:0] name, input [31:0] got, input [31:0] exp); begin
    if (got !== exp) begin errors = errors + 1; $display("MISMATCH %0s got %h expected %h", name, got, exp); end end endtask
  integer i;
  initial begin
    repeat (10) @(posedge clk); rst = 0; repeat (5) @(posedge clk);
    wbr(32'h30000000, r); chk("ID", r, 32'h54414901);
    wbw(32'h30000004, 32'h200);                 // CLEAR | mode 0
    for (i = 0; i < 4; i = i + 1) wbw(32'h3000000C, 1);
    wbw(32'h30000004, 32'h100);                 // START | mode 0
    guard = 0; r = 1;
    while (r[0] && guard < 10000) begin wbr(32'h30000008, r); guard = guard + 1; end
    chk("STATUS DONE&!ERR", r & 32'h7, 32'h2);
    wbr(32'h30000010, r); chk("RESULT", r, 32'h0401);
    wbr(32'h30000014, r); chk("CYCLES", r, 32'd6);
    if (errors == 0) $display("Monitor: tiny_ai_core wrapper GL+SDF PASS at %0t", $time);
    else $display("Monitor: tiny_ai_core wrapper GL+SDF FAIL (%0d mismatches)", errors);
    $finish;
  end
  initial begin #200000; $display("Monitor: tiny_ai_core wrapper GL+SDF FAIL (timeout)"); $finish; end
`ifdef ENABLE_SDF
  initial $sdf_annotate(`SDF_FILE, uut, , "sdf_wrapper_only.log");
`endif
endmodule
