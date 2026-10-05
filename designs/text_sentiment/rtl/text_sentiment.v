// SPDX-License-Identifier: Apache-2.0
// text_sentiment -- bag-of-words sentiment of a four-token sentence: signed embedding lookup and one accumulator
// (model/tiny_ai/spec.json).
//
// Stream in : 4 beats, token in s_data[1:0] (0 PAD, 1 GOOD, 2 FINE, 3 BAD), s_last on the 4th.
// Stream out: 2 beats. Beat 0 m_data = {6'b0, error, class}; beat 1 m_data = score (signed sum, sign-extended to
//             8 bits), m_last on beat 1.
// Compute   : one ROM lookup (text_sentiment_rom.v, generated) and one add per beat into a 5-bit signed accumulator
//             (range of four 3-bit signed embeddings: -16..12, no overflow); class = sum > 0, a sum of 0 is negative.
//             Tokens are consumed as they arrive. Result: 1 cycle after the last input beat.
// error     : a beat with s_data > 3 (not added), or a frame that is not exactly 4 beats (beats past 4 are ignored).
`timescale 1ns/1ps
`default_nettype none
module text_sentiment (
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

    reg               [1:0] state;
    reg               [2:0] count;     // beats received in this frame, saturates at N
    reg  signed       [4:0] acc;       // sentiment sum
    reg                     error;

    wire       beat_in  = s_valid & s_ready;
    wire       beat_out = m_valid & m_ready;
    wire       in_frame = (count < N);
    wire       bad_item = |s_data[7:2];
    wire signed [2:0] embedding;
    wire       cls      = (acc > 5'sd0);

    text_sentiment_rom rom (.token(s_data[1:0]), .embedding(embedding));

    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);
    assign m_last  = (state == ST_OUT1);
    assign m_data  = (state == ST_OUT1) ? {{3{acc[4]}}, acc} : {6'b0, error, cls};

    always @(posedge clk) begin
        if (rst) begin
            state <= ST_LOAD;
            count <= 3'd0;
            acc   <= 5'sd0;
            error <= 1'b0;
        end else begin
            case (state)
                ST_LOAD: if (beat_in) begin
                    if (in_frame)             count <= count + 3'd1;
                    if (in_frame & ~bad_item) acc <= acc + {{2{embedding[2]}}, embedding};
                    if (bad_item | ~in_frame | (s_last & (count != N - 3'd1))) error <= 1'b1;
                    if (s_last)               state <= ST_OUT0;
                end
                ST_OUT0: if (beat_out) state <= ST_OUT1;
                ST_OUT1: if (beat_out) begin
                    state <= ST_LOAD;
                    count <= 3'd0;
                    acc   <= 5'sd0;
                    error <= 1'b0;
                end
                default: state <= ST_LOAD;
            endcase
        end
    end
endmodule
`default_nettype wire
