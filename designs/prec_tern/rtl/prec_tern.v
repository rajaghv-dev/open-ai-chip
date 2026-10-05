// SPDX-License-Identifier: Apache-2.0
// prec_tern -- a NEURAL NETWORK INFERENCE ENGINE with TERNARY weights {-1, 0, +1}: one neuron, no multiplier.
// Part of the precision study (model/precision_hw/spec.md): same task and engine structure in seven formats.
//
// THE NETWORK
//   task      : 3x3 image of 4-bit pixels (0..15); class 1 = vertical bar, 0 = horizontal bar.
//   weights   : q[i] in {-1, 0, +1} (TWN rule), stored as 2-bit two's complement (01 = +1, 11 = -1, 00 = 0).
//   bias      : qb = -1 (9-bit two's complement), in the same units (real value = code * 0.2924).
//   sum       : acc = qb + sum q[i] * pixel[i], exact in a 10-bit signed accumulator (any ROM content fits).
//   activation: class = (acc >= 0), i.e. the sign bit inverted.
//
// WHY THIS IS AI
//   The weights and bias are learned (logistic regression, then ternarised) in model/precision_hw/golden.py and
//   emitted as prec_tern_rom.v by gen.py. The RTL has no rule about bars: new ROM, new decision.
//
// NETWORK <-> HARDWARE
//   weight memory -> prec_tern_rom (9 x 2-bit weights + 9-bit bias, selected by pixel index).
//   multiply      -> none: weight +1 adds the pixel, -1 subtracts it, 0 skips (acc unchanged).
//   accumulate    -> one 10-bit adder/subtractor (XOR + carry-in for the subtract).
//   activation    -> the accumulator sign bit.
//   ONE MAC unit is reused serially, one pixel per clock, overlapping the arrival of the next beat.
//
// WHAT THIS FORMAT COSTS OR SAVES
//   Saves: the multiplier (a ternary product is a conditional negate), 2 bits per weight (27 parameter bits).
//   Costs: 2 bits per weight instead of 1, a 10-bit accumulator instead of 4, and quantisation: 94.15 % test
//   accuracy and 98.8 % same decision as fp32, far better than binary because 0 lets a pixel not vote.
//   Honest note: the ROM is constants, so synthesis folds it into logic on x_idx (and the five zero weights
//   remove five of the nine adder steps in effect, but the hardware stays generic: it is data in the ROM, and
//   the adder is still there for any other trained weights). The stream control registers cost as much as the MAC.
//
// BLOCKS: IO (stream handshake), MEMORY (weight ROM), COMPUTE (add/sub/skip MAC, sign compare),
//         CONTROL (state, beat counter, error, input register).
//
// Stream in : 9 beats, pixel in s_data[3:0], s_last on the 9th. s_data[7:4] must be 0, else the item is unused.
// Stream out: 2 beats. Beat 0 = {6'b0, error, class}; beat 1 = acc[7:0] ^ {6'b0, acc[9:8]}, m_last on beat 1.
// Latency   : 2 (edge E takes the last beat into x_*; edge E+1 does its MAC and raises m_valid).
// error     : item with s_data[7:4] != 0 (unused: no MAC step), a frame not exactly 9 beats (beats past 9 are
//             accepted and ignored; missing items are unused). Unused items leave acc unchanged.
`timescale 1ns/1ps
`default_nettype none
module prec_tern (
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
    reg  [3:0] x_pix;    // MEMORY: the 4-bit unsigned pixel of that item
    reg  [3:0] x_idx;    // MEMORY: its pixel index (ROM address)
    reg signed [9:0] acc; // COMPUTE: signed accumulator, starts at the bias

    // ---- IO: handshake strobes ----
    wire beat_in  = s_valid & s_ready;
    wire beat_out = m_valid & m_ready;
    wire bad_item = |s_data[7:4];
    wire in_frame = (count < N);

    // ---- MEMORY (weight ROM): ternary weight and bias, constants ----
    wire signed [1:0] weight;
    wire signed [8:0] bias;
    prec_tern_rom rom (.addr(x_idx), .weight(weight), .bias(bias));

    // ---- COMPUTE: the one MAC: add / subtract / skip the unsigned pixel ----
    wire signed [9:0] pix_ext  = {6'b0, x_pix};        // zero-extend: the pixel is unsigned 0..15
    wire signed [9:0] bias_ext = {bias[8], bias};      // sign-extend the 9-bit bias to 10 bits
    reg  signed [9:0] acc_next;
    always @(*) begin
        case (weight)
            2'sb01:  acc_next = acc + pix_ext;         // +1
            2'sb11:  acc_next = acc - pix_ext;         // -1
            default: acc_next = acc;                   // 0 (2'b10 is never stored)
        endcase
    end
    wire class_o = ~acc[9];                            // acc >= 0

    // ---- IO: stream outputs ----
    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);
    assign m_last  = (state == ST_OUT1);
    assign m_data  = (state == ST_OUT1) ? (acc[7:0] ^ {6'b0, acc[9:8]}) : {6'b0, error, class_o};

    // ---- CONTROL + COMPUTE: sequential update ----
    always @(posedge clk) begin
        if (rst) begin
            state <= ST_LOAD;
            count <= 4'd0;
            error <= 1'b0;
            x_vld <= 1'b0;
            x_pix <= 4'd0;
            x_idx <= 4'd0;
            acc   <= bias_ext;
        end else begin
            // MAC of the item latched on the previous edge (runs in every state)
            if (x_vld) acc <= acc_next;
            // input register: latch a used item
            x_vld <= beat_in & in_frame & ~bad_item;
            x_pix <= s_data[3:0];
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
                    acc   <= bias_ext;
                end
            endcase
        end
    end
endmodule
`default_nettype wire
