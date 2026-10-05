// SPDX-License-Identifier: Apache-2.0
// Self-checking testbench for designs/image_text_match: every case of tb/vectors.hex (model/image_text_match/gen_rom.py).
// The body is the shared stream testbench (shared/tb/stream_tb.vh) in a copy with a larger case limit
// (tb/stream_tb_big.vh: 2,079 cases do not fit its 1,023 limit). Run with +VEC=designs/image_text_match/tb/vectors.hex
// from the repository root (the include path below is relative to it, as the Makefile runs).
`timescale 1ns/1ps
`define DUT image_text_match
module image_text_match_tb;
`include "designs/image_text_match/tb/stream_tb_big.vh"
endmodule
