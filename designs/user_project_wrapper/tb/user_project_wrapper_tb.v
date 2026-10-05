// SPDX-License-Identifier: Apache-2.0
// Testbench for Caravel's user_project_wrapper holding one tiny_ai_core (mprj). Same body and vectors as tiny_ai_core_tb
// (shared/tb/tiny_ai_wb_tb.vh, +VEC=designs/tiny_ai_core/tb/vectors.hex, -I shared/tb); ports only, so it also runs on netlists.
// Extra check: the wrapper must not drive analog_io (reads z while the testbench leaves it undriven).
`timescale 1ns/1ps
module user_project_wrapper_tb;
    reg          wb_clk_i = 1'b0;
    reg          wb_rst_i = 1'b1;
    reg          wbs_stb_i = 1'b0, wbs_cyc_i = 1'b0, wbs_we_i = 1'b0;
    reg  [3:0]   wbs_sel_i = 4'd0;
    reg  [31:0]  wbs_dat_i = 32'd0, wbs_adr_i = 32'd0;
    wire         wbs_ack_o;
    wire [31:0]  wbs_dat_o;
    reg  [127:0] la_data_in = 128'd0, la_oenb = {128{1'b1}};   // wrapper ports the design no longer uses
    reg  [37:0]  io_in = 38'd0;
    wire [127:0] la_data_out;
    wire [37:0]  io_out, io_oeb;
    wire [2:0]   irq;
    wire [`MPRJ_IO_PADS-10:0] analog_io;
    reg          user_clock2 = 1'b0;

    user_project_wrapper dut (
        .wb_clk_i(wb_clk_i), .wb_rst_i(wb_rst_i),
        .wbs_stb_i(wbs_stb_i), .wbs_cyc_i(wbs_cyc_i), .wbs_we_i(wbs_we_i), .wbs_sel_i(wbs_sel_i),
        .wbs_dat_i(wbs_dat_i), .wbs_adr_i(wbs_adr_i), .wbs_ack_o(wbs_ack_o), .wbs_dat_o(wbs_dat_o),
        .la_data_in(la_data_in), .la_data_out(la_data_out), .la_oenb(la_oenb),
        .io_in(io_in), .io_out(io_out), .io_oeb(io_oeb),
        .analog_io(analog_io), .user_clock2(user_clock2), .user_irq(irq));

    // analog_io is an inout the design must leave alone: every bit stays high impedance (RTL only; netlists may
    // contain no analog_io logic at all, which is also z, but a pulled/tied cell would not be a design error we can judge here)
    initial begin
        #1;
        if (analog_io !== {(`MPRJ_IO_PADS-9){1'bz}}) begin
            $display("FAIL analog_io is driven: %b", analog_io);
            $fatal(1, "FAIL analog_io not high impedance");
        end
    end

    // Decision documentation: the wrapper no longer drives la_data_out, io_out, io_oeb (the core has no such ports and
    // the wrapper has no glue logic), so they read z in RTL simulation. Compiled only with -DRTL_Z_CHECK (manual RTL run);
    // gate-level runs (scripts/flow/gl_sim.sh passes no such define) and the default make simulate skip it.
`ifdef RTL_Z_CHECK
    initial begin
        #1;
        if (la_data_out !== {128{1'bz}} || io_out !== {38{1'bz}} || io_oeb !== {38{1'bz}})
            $fatal(1, "FAIL undriven wrapper outputs are not z: la=%h io_out=%h io_oeb=%h", la_data_out, io_out, io_oeb);
    end
`endif

`define TB_NAME "user_project_wrapper_tb"
`include "tiny_ai_wb_tb.vh"
endmodule
