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
    wire [2:0]   irq;

    tiny_ai_core dut (
        .wb_clk_i(wb_clk_i), .wb_rst_i(wb_rst_i),
        .wbs_stb_i(wbs_stb_i), .wbs_cyc_i(wbs_cyc_i), .wbs_we_i(wbs_we_i), .wbs_sel_i(wbs_sel_i),
        .wbs_dat_i(wbs_dat_i), .wbs_adr_i(wbs_adr_i), .wbs_ack_o(wbs_ack_o), .wbs_dat_o(wbs_dat_o),
        .irq(irq));

`define TB_NAME "tiny_ai_core_tb"
`include "tiny_ai_wb_tb.vh"
endmodule
