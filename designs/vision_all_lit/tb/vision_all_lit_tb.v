// SPDX-License-Identifier: Apache-2.0
// Self-checking testbench for designs/vision_all_lit: every case of tb/vectors.hex (model/tiny_ai/gen_rom.py).
// The body is shared by the tiny AI engines: shared/tb/stream_tb.vh. Run with +VEC=designs/vision_all_lit/tb/vectors.hex
// and -I shared/tb.
`timescale 1ns/1ps
`define DUT vision_all_lit
module vision_all_lit_tb;
`include "stream_tb.vh"
endmodule
