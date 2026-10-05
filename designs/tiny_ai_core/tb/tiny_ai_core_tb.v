// SPDX-License-Identifier: Apache-2.0
// Testbench for tiny_ai_core (RTL and gate level). Body shared with the wrapper: shared/tb/tiny_ai_wb_tb.vh (run with -I shared/tb).
`timescale 1ns/1ps
module tiny_ai_core_tb;
    reg          wb_clk_i = 1'b0;
    reg          wb_rst_i = 1'b1;
    reg          wbs_stb_i = 1'b0, wbs_cyc_i = 1'b0, wbs_we_i = 1'b0;
    reg  [3:0]   wbs_sel_i = 4'd0;
    reg  [31:0]  wbs_dat_i = 32'd0, wbs_adr_i = 32'd0;
    wire         wbs_ack_o;
    wire [31:0]  wbs_dat_o;
    reg  [127:0] la_data_in = 128'h0123_4567_89AB_CDEF_FEDC_BA98_7654_3210;
    reg  [127:0] la_oenb    = 128'hFFFF_0000_FFFF_0000_0000_FFFF_0000_FFFF;
    wire [127:0] la_data_out;
    reg  [37:0]  io_in = 38'h2A_5A5A_5A5A;
    wire [37:0]  io_out, io_oeb;
    wire [2:0]   irq;

    tiny_ai_core dut (
        .wb_clk_i(wb_clk_i), .wb_rst_i(wb_rst_i),
        .wbs_stb_i(wbs_stb_i), .wbs_cyc_i(wbs_cyc_i), .wbs_we_i(wbs_we_i), .wbs_sel_i(wbs_sel_i),
        .wbs_dat_i(wbs_dat_i), .wbs_adr_i(wbs_adr_i), .wbs_ack_o(wbs_ack_o), .wbs_dat_o(wbs_dat_o),
        .la_data_in(la_data_in), .la_data_out(la_data_out), .la_oenb(la_oenb),
        .io_in(io_in), .io_out(io_out), .io_oeb(io_oeb), .irq(irq));

`define TB_NAME "tiny_ai_core_tb"
`include "tiny_ai_wb_tb.vh"
endmodule
