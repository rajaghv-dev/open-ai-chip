// SPDX-License-Identifier: Apache-2.0
// <eng> -- a NEURAL NETWORK INFERENCE ENGINE: <one line: what the network is>. (Spec: model/<eng>/spec.json.)
//
// THE NETWORK
//   inputs / weights / weighted sum / activation / pooling / output : <one line each>
//
// THE WEIGHTS ARE LEARNED, NOT WRITTEN
//   model/<eng>/train.py fits them from labelled examples and gen_rom.py emits <eng>_rom.v (generated, do not edit).
//
// NEURAL NETWORK <-> HARDWARE
//   weight memory   -> <eng>_rom          multiply -> XNOR / multiplier       accumulate -> adder
//   activation      -> comparator          pooling  -> OR / max
//
// BLOCKS
//   IO      : s_* input beats, m_* two output beats.
//   MEMORY  : ROM constants; frame / history registers.
//   COMPUTE : the neuron (match, count, compare) and any pooling registers.
//   CONTROL : FSM (LOAD, COMP, OUT0, OUT1), beat counter, error flag.
//
// Stream in : N beats, item in s_data, s_last on the Nth.   Stream out: 2 beats, m_last on beat 1.
//             beat 0 = {6'b0, error, class}; beat 1 = score.
// Latency   : <k> cycles after the last beat (edge accepting it .. edge raising m_valid, both counted).
// error     : item out of range, or frame not exactly N beats (extra beats ignored; the result is still produced).
`timescale 1ns/1ps
`default_nettype none
module <eng> (
    // ---- IO: clock, reset, and the valid/ready stream handshake (24 pins) ----
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
    // ---- CONTROL: constants, state ----
    localparam [1:0] ST_LOAD = 2'd0, ST_COMP = 2'd1, ST_OUT0 = 2'd2, ST_OUT1 = 2'd3;
    reg [1:0] state;
    // ---- IO: handshake strobes ----
    wire beat_in  = s_valid & s_ready;
    wire beat_out = m_valid & m_ready;
    // ---- MEMORY + COMPUTE: ROM and the neuron ----
    wire [2:0] threshold;
    wire       weight;
    <eng>_rom rom (.addr(2'd0), .weight(weight), .threshold(threshold));
    // ---- IO: outputs ----
    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);
    assign m_last  = (state == ST_OUT1);
    // ... always @(posedge clk): reset to ST_LOAD; LOAD stores items and sets error; COMP evaluates;
    //     OUT0/OUT1 hold m_data until beat_out; after OUT1 clear every register (see designs/vision_block/rtl/vision_block.v).
endmodule
`default_nettype wire
