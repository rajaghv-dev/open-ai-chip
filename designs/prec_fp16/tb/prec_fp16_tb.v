// SPDX-License-Identifier: Apache-2.0
// Self-checking testbench for designs/prec_fp16: every case of tb/vectors.hex (model/precision_hw/gen.py).
// The body is shared by the tiny AI engines: shared/tb/stream_tb.vh. Run with +VEC=designs/prec_fp16/tb/vectors.hex
// and -I shared/tb.
`timescale 1ns/1ps
`define DUT prec_fp16
module prec_fp16_tb;
`include "stream_tb.vh"
endmodule
