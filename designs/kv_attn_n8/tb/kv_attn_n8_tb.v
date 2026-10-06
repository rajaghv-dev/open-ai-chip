// SPDX-License-Identifier: Apache-2.0
// Self-checking testbench for designs/kv_attn_n8: every record of tb/vectors.hex (model/kv_attention/gen.py).
// The body is shared/tb/kv_attn_tb.vh. Run with +VEC=designs/kv_attn_n8/tb/vectors.hex from the repository root.
`timescale 1ns/1ps
`define DUT kv_attn_n8
`define EXP_N 8
`define EXP_BITS 8
`define EXP_RING 0
module kv_attn_n8_tb;
`include "kv_attn_tb.vh"
endmodule
