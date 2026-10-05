// SPDX-License-Identifier: Apache-2.0
// Self-checking testbench for designs/prec_int8: every case of tb/vectors.hex (model/precision_hw/gen.py), 931 cases.
// Body shared with the tiny AI engines: shared/tb/stream_tb.vh. Run with +VEC=designs/prec_int8/tb/vectors.hex and -I shared/tb.
`timescale 1ns/1ps
`define DUT prec_int8
module prec_int8_tb;
`include "stream_tb.vh"
endmodule
