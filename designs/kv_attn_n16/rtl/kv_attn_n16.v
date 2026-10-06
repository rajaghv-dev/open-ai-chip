// SPDX-License-Identifier: Apache-2.0
// kv_attn_n16 -- thin top: the shared kv_attn_core (shared/rtl/kv_attn_core.v) with N = 16, KV bits = 8, ring = 0,
// and this design's parameter ROM. Behaviour and cycle schedule: model/kv_attention/spec.md.
`timescale 1ns/1ps
`default_nettype none
module kv_attn_n16 (
    input  wire       clk,
    input  wire       rst,        // synchronous, active high
    input  wire       s_valid,
    input  wire [7:0] s_data,
    input  wire       s_last,
    output wire       s_ready,
    output wire       m_valid,
    output wire [7:0] m_data,
    output wire       m_last,
    input  wire       m_ready
);
    wire [3:0]   token;
    wire [31:0]  emb, bq, bk, bv;
    wire [127:0] wq, wk, wv;

    kv_attn_n16_rom rom (.token(token), .emb(emb), .wq(wq), .wk(wk), .wv(wv), .bq(bq), .bk(bk), .bv(bv));

    kv_attn_core #(.N(16), .KVB(8), .RING(0)) core (
        .clk(clk), .rst(rst), .s_valid(s_valid), .s_data(s_data), .s_last(s_last), .s_ready(s_ready),
        .m_valid(m_valid), .m_data(m_data), .m_last(m_last), .m_ready(m_ready),
        .rom_token(token), .rom_emb(emb), .rom_wq(wq), .rom_wk(wk), .rom_wv(wv),
        .rom_bq(bq), .rom_bk(bk), .rom_bv(bv)
    );
endmodule
`default_nettype wire
