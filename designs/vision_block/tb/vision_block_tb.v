// SPDX-License-Identifier: Apache-2.0
// Self-checking testbench for designs/vision_block: every case of tb/vectors.hex (model/tiny_ai/gen_rom.py).
// The body is shared by the tiny AI engines: shared/tb/stream_tb.vh. Run with +VEC=designs/vision_block/tb/vectors.hex
// and -I shared/tb.
`timescale 1ns/1ps
`define DUT vision_block
module vision_block_tb;
`include "stream_tb.vh"
endmodule
