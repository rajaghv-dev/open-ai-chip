// SPDX-License-Identifier: Apache-2.0
// Self-checking testbench for designs/text_sentiment: every case of tb/vectors.hex (model/tiny_ai/gen_rom.py).
// The body is shared by the tiny AI engines: shared/tb/stream_tb.vh. Run with +VEC=designs/text_sentiment/tb/vectors.hex
// and -I shared/tb.
`timescale 1ns/1ps
`define DUT text_sentiment
module text_sentiment_tb;
`include "stream_tb.vh"
endmodule
