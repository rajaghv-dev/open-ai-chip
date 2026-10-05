// SPDX-License-Identifier: Apache-2.0
// vision_all_lit -- one binary threshold neuron over a 2x2 one-bit image (model/tiny_ai/spec.json).
//
// Stream in : 4 beats, pixel in s_data[0] (raster order), s_last on the 4th.
// Stream out: 2 beats. Beat 0 m_data = {6'b0, error, class}; beat 1 m_data = score (unsigned match count, 0..4),
//             m_last on beat 1.
// Neuron    : score = number of pixels equal to their 1-bit weight (XNOR + count); class = score >= threshold.
//             Weights and threshold come from vision_all_lit_rom.v (generated).
// Pixels are consumed as they arrive, so there is no pixel storage. Result: 1 cycle after the last input beat.
// error     : a beat with s_data > 1 (not scored), or a frame that is not exactly 4 beats (beats past 4 are ignored).
`timescale 1ns/1ps
`default_nettype none
module vision_all_lit (
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
    localparam [2:0] N       = 3'd4;
    localparam [1:0] ST_LOAD = 2'd0;
    localparam [1:0] ST_OUT0 = 2'd1;
    localparam [1:0] ST_OUT1 = 2'd2;

    reg  [1:0] state;
    reg  [2:0] count;              // beats received in this frame, saturates at N
    reg  [2:0] score;              // matching pixels so far, 0..N
    reg        error;

    wire       beat_in  = s_valid & s_ready;
    wire       beat_out = m_valid & m_ready;
    wire       in_frame = (count < N);
    wire       bad_item = |s_data[7:1];
    wire       weight;
    wire [2:0] threshold;
    wire       match    = ~(s_data[0] ^ weight);
    wire       cls      = (score >= threshold);

    vision_all_lit_rom rom (.addr(count[1:0]), .weight(weight), .threshold(threshold));

    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);
    assign m_last  = (state == ST_OUT1);
    assign m_data  = (state == ST_OUT1) ? {5'b0, score} : {6'b0, error, cls};

    always @(posedge clk) begin
        if (rst) begin
            state <= ST_LOAD;
            count <= 3'd0;
            score <= 3'd0;
            error <= 1'b0;
        end else begin
            case (state)
                ST_LOAD: if (beat_in) begin
                    if (in_frame)                      count <= count + 3'd1;
                    if (in_frame & ~bad_item & match)  score <= score + 3'd1;
                    if (bad_item | ~in_frame | (s_last & (count != N - 3'd1))) error <= 1'b1;
                    if (s_last)                        state <= ST_OUT0;
                end
                ST_OUT0: if (beat_out) state <= ST_OUT1;
                ST_OUT1: if (beat_out) begin
                    state <= ST_LOAD;
                    count <= 3'd0;
                    score <= 3'd0;
                    error <= 1'b0;
                end
                default: state <= ST_LOAD;
            endcase
        end
    end
endmodule
`default_nettype wire
