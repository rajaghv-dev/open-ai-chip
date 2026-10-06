// SPDX-License-Identifier: Apache-2.0
// soc_kv_attn_n8 -- the KV-cache attention engine as a Caravel SoC macro: the generic Wishbone-to-stream adapter
// (shared/rtl/wb_stream_adapter.v) plus ONE unchanged stream engine, kv_attn_n8 (designs/kv_attn_n8, 8-slot KV cache,
// prefill and decode; protocol in model/kv_attention/spec.md). The port list is exactly tiny_ai_core's, so this drops
// into user_project_wrapper as mprj. Register map: wb_stream_adapter.v header (ID 0x5354_5201, base 0x3000_0000).
// Firmware (firmware/kv/): write a command frame (TXDATA..., TXLAST on its last beat: opcode 01 RESET_CACHE,
// 02 PREFILL tokens, 03 DECODE token), wait for RXSTATUS.DONE or irq[0], read 2 response beats (8 for a decode).
// Everything in this file is wiring; the attention (cache, projections, scan) lives in kv_attn_core.v.
`timescale 1ns/1ps
`default_nettype none
module soc_kv_attn_n8 (
`ifdef USE_POWER_PINS
    inout  wire         vccd1,
    inout  wire         vssd1,
`endif
    input  wire         wb_clk_i,
    input  wire         wb_rst_i,
    input  wire         wbs_stb_i,
    input  wire         wbs_cyc_i,
    input  wire         wbs_we_i,
    input  wire [3:0]   wbs_sel_i,
    input  wire [31:0]  wbs_dat_i,
    input  wire [31:0]  wbs_adr_i,
    output wire         wbs_ack_o,
    output wire [31:0]  wbs_dat_o,
    output wire [2:0]   irq
);
    wire        eng_rst;
    wire        s_valid, s_last, s_ready;
    wire [7:0]  s_data;
    wire        m_valid, m_last, m_ready;
    wire [7:0]  m_data;

    wb_stream_adapter #(.TX_DEPTH(16), .RX_DEPTH(16)) u_adapter (
        .wb_clk_i(wb_clk_i), .wb_rst_i(wb_rst_i),
        .wbs_stb_i(wbs_stb_i), .wbs_cyc_i(wbs_cyc_i), .wbs_we_i(wbs_we_i), .wbs_sel_i(wbs_sel_i),
        .wbs_dat_i(wbs_dat_i), .wbs_adr_i(wbs_adr_i), .wbs_ack_o(wbs_ack_o), .wbs_dat_o(wbs_dat_o), .irq(irq),
        .eng_rst(eng_rst), .s_valid(s_valid), .s_data(s_data), .s_last(s_last), .s_ready(s_ready),
        .m_valid(m_valid), .m_data(m_data), .m_last(m_last), .m_ready(m_ready));

    kv_attn_n8 u_engine (
        .clk(wb_clk_i), .rst(eng_rst),
        .s_valid(s_valid), .s_data(s_data), .s_last(s_last), .s_ready(s_ready),
        .m_valid(m_valid), .m_data(m_data), .m_last(m_last), .m_ready(m_ready));
endmodule
`default_nettype wire
