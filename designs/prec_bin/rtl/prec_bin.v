// SPDX-License-Identifier: Apache-2.0
// prec_bin -- a NEURAL NETWORK INFERENCE ENGINE in BINARY format: one neuron, 1-bit weights, binarised inputs,
// XNOR-popcount. Part of the precision study (model/precision_hw/spec.md): the same task and the same engine
// structure are built in seven number formats; only the format changes.
//
// THE NETWORK
//   task      : 3x3 image of 4-bit pixels (0..15); class 1 = vertical bar, 0 = horizontal bar.
//   inputs    : a[i] = pixel[3] (pixel >= 8), one bit per pixel, raster order i = 0..8.
//   weights   : s[i] = 1 if the trained fp32 weight is >= 0 else 0 (1 means +1, 0 means -1).
//   "multiply": XNOR(a[i], s[i]) = 1 when input and weight agree.
//   sum       : m = number of agreeing pixels (popcount, 0..9), a 4-bit unsigned count.
//   activation: class = (m >= T), T = 3 fitted on the binarised training set (a threshold step).
//
// WHY THIS IS AI
//   Weights and T are learned from labelled data (model/precision_hw/golden.py, then gen.py writes
//   prec_bin_rom.v). The RTL contains no rule about bars: regenerate the ROM from other labels and the same
//   hardware computes another decision.
//
// NETWORK <-> HARDWARE
//   weight memory -> prec_bin_rom: 9 weight bits + threshold, selected by the pixel index.
//   multiply      -> one XNOR gate.
//   accumulate    -> a 4-bit incrementer on the count register (count += match).
//   activation    -> a 4-bit unsigned comparator (acc >= T).
//   ONE MAC unit is reused serially, one pixel per clock, overlapping the arrival of the next beat.
//
// WHAT THIS FORMAT COSTS OR SAVES
//   Saves: 1 bit per weight, no multiplier and no adder, only a 4-bit counter. Parameters total 13 bits and
//   9 bits move through the MAC per inference. Costs: accuracy (88.95 % test, 90.3 % same decision as fp32): a
//   sign-only weight cannot say "this pixel hardly matters", and the 4-bit pixel is cut to its top bit.
//   Honest note: the ROM is constants, so synthesis folds it into a few gates on x_idx (not a real memory).
//   And the stream beat/frame registers (count, x_idx, x_pix, error, state) dominate the area, not the MAC.
//
// BLOCKS: IO (stream handshake), MEMORY (weight ROM), COMPUTE (the XNOR + incrementer, class compare),
//         CONTROL (state, beat counter, error, input register).
//
// Stream in : 9 beats, pixel in s_data[3:0], s_last on the 9th. s_data[7:4] must be 0, else the item is unused.
// Stream out: 2 beats. Beat 0 = {6'b0, error, class}; beat 1 = {4'b0, acc} (the match count), m_last on beat 1.
// Latency   : 2 (edge E takes the last beat into x_*; edge E+1 does its MAC and raises m_valid).
// error     : item with s_data[7:4] != 0 (unused: counts no match), a frame not exactly 9 beats (beats past 9
//             are accepted and ignored). Unused items count nothing (in bin this differs from pixel 0).
`timescale 1ns/1ps
`default_nettype none
module prec_bin (
    // ---- IO: clock, reset, and the valid/ready stream handshake ----
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
    // ---- CONTROL: constants ----
    localparam [1:0] ST_LOAD  = 2'd0;
    localparam [1:0] ST_DRAIN = 2'd1;
    localparam [1:0] ST_OUT0  = 2'd2;
    localparam [1:0] ST_OUT1  = 2'd3;
    localparam [3:0] N        = 4'd9;   // pixels per frame

    // ---- registers ----
    reg  [1:0] state;    // CONTROL: FSM LOAD / DRAIN / OUT0 / OUT1
    reg  [3:0] count;    // CONTROL: beats accepted this frame, saturates at 9; also the pixel index
    reg        error;    // CONTROL: sticky flag (bad item or wrong frame length), cleared after output
    reg        x_vld;    // CONTROL: x_* holds a used item to be MACed on the next edge
    reg        x_pix;    // MEMORY: input bit (pixel[3]) of that item
    reg  [3:0] x_idx;    // MEMORY: its pixel index (ROM address)
    reg  [3:0] acc;      // COMPUTE: match count 0..9, starts at 0

    // ---- IO: handshake strobes ----
    wire beat_in  = s_valid & s_ready;
    wire beat_out = m_valid & m_ready;
    wire bad_item = |s_data[7:4];
    wire in_frame = (count < N);

    // ---- MEMORY (weight ROM): weight bit and threshold, constants ----
    wire       weight;
    wire [3:0] threshold;
    prec_bin_rom rom (.addr(x_idx), .weight(weight), .threshold(threshold));

    // ---- COMPUTE: the one MAC: XNOR then incrementer ----
    wire       match    = ~(x_pix ^ weight);
    wire [3:0] acc_next = acc + {3'b0, match};
    wire       class_o  = (acc >= threshold);   // activation: unsigned compare

    // ---- IO: stream outputs ----
    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);
    assign m_last  = (state == ST_OUT1);
    assign m_data  = (state == ST_OUT1) ? {4'b0, acc} : {6'b0, error, class_o};

    // ---- CONTROL + COMPUTE: sequential update ----
    always @(posedge clk) begin
        if (rst) begin
            state <= ST_LOAD;
            count <= 4'd0;
            error <= 1'b0;
            x_vld <= 1'b0;
            x_pix <= 1'b0;
            x_idx <= 4'd0;
            acc   <= 4'd0;
        end else begin
            // MAC of the item latched on the previous edge (runs in every state)
            if (x_vld) acc <= acc_next;
            // input register: latch a used item
            x_vld <= beat_in & in_frame & ~bad_item;
            x_pix <= s_data[3];
            x_idx <= count;
            case (state)
                ST_LOAD: if (beat_in) begin
                    if (in_frame) count <= count + 4'd1;   // saturating
                    if (bad_item | ~in_frame | (s_last & (count != N - 4'd1))) error <= 1'b1;
                    if (s_last) state <= ST_DRAIN;
                end
                ST_DRAIN: state <= ST_OUT0;                // this edge MACs the last item
                ST_OUT0: if (beat_out) state <= ST_OUT1;
                default: if (beat_out) begin               // ST_OUT1
                    state <= ST_LOAD;
                    count <= 4'd0;
                    error <= 1'b0;
                    acc   <= 4'd0;
                end
            endcase
        end
    end
endmodule
`default_nettype wire
