// SPDX-License-Identifier: Apache-2.0
// Self-checking testbench for designs/<eng>: every case of tb/vectors.hex (model/<eng>/gen_rom.py).
// The body is shared by the stream engines: shared/tb/stream_tb.vh (at most 1023 cases). Run with
// +VEC=designs/<eng>/tb/vectors.hex and -I shared/tb.
`timescale 1ns/1ps
`define DUT <eng>
module <eng>_tb;
`include "stream_tb.vh"
endmodule
