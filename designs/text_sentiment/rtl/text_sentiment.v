// SPDX-License-Identifier: Apache-2.0
// text_sentiment -- a NEURAL NETWORK INFERENCE ENGINE: a bag-of-words sentiment model. Each token is mapped to a
// learned signed number (an EMBEDDING) and the numbers are summed. (Spec: model/tiny_ai/spec.json; docs/WHY_AI.md.)
//
// THE NETWORK (embedding table + one accumulator + threshold)
//   inputs      : 4 tokens of 2 bits: 0 PAD, 1 GOOD, 2 FINE, 3 BAD, one per beat.
//   weights     : an embedding table, one learned 3-bit signed number per token.
//   weighted sum: score = sum of the four looked-up embeddings (5-bit signed accumulator).
//   activation  : threshold step, class = (score > 0); a score of 0 is negative.
//   output      : class (1 bit) and the signed score.
//
// THE WEIGHTS ARE LEARNED, NOT WRITTEN
//   model/tiny_ai/train.py fits the embeddings from labelled examples (all 256 four-token sentences, each tagged
//   positive or not) and emits text_sentiment_rom.v (generated, do not edit). Training found PAD = 0, GOOD = +1,
//   FINE = 0, BAD = -1. Nobody told it that GOOD is good: it discovered that from the labels. This RTL contains
//   no word meanings or "count GOOD minus BAD" rule: it is a generic "look up, add, test sign" engine. Regenerate
//   the ROM from other labels and the SAME RTL computes a different rule (docs/WHY_AI.md section 4): if FINE
//   also counts as positive, FINE becomes +1; if one BAD outweighs two GOODs, BAD becomes -2.
//   Unlike a hand-coded rule (`if (good_count > bad_count)` over hard-wired token codes), the decision comes
//   from data and the structure is generic. (Adding ignores word order, so "GOOD right before BAD" cannot be
//   learned here; that is why sequence models and attention exist, section 5.)
//
// NEURAL NETWORK <-> HARDWARE
//   embedding table -> text_sentiment_rom (token in, signed 3-bit embedding out)
//   multiply        -> none needed: the embedding IS the token's contribution (weight times a one-hot input)
//   accumulate      -> "acc" register: add the sign-extended embedding each beat
//   activation      -> comparator (acc > 0)                           [cls]
//
// BLOCKS (the four constructs of ../open-ai-silicon/docs/ARCH_STUDY_PLAN.md section 2)
//   IO / stream handshake : s_* input beats, m_* two output beats.
//   MEMORY                : embedding ROM (no frame buffer: tokens are consumed as they arrive).
//   COMPUTE               : embedding lookup, sign-extend, add into acc, compare.
//   CONTROL               : FSM (LOAD, OUT0, OUT1), beat counter, error flag.
//
// Stream in : 4 beats, token in s_data[1:0] (0 PAD, 1 GOOD, 2 FINE, 3 BAD), s_last on the 4th.
// Stream out: 2 beats. Beat 0 m_data = {6'b0, error, class}; beat 1 m_data = score (signed sum, sign-extended to
//             8 bits), m_last on beat 1.
// Range     : acc is 5 bits signed because four 3-bit signed embeddings (-4..3) sum to -16..12: no overflow.
// Latency   : 1 cycle after the last input beat.
// error     : a beat with s_data > 3 (not added), or a frame that is not exactly 4 beats (beats past 4 are ignored).
`timescale 1ns/1ps
`default_nettype none
module text_sentiment (
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
    localparam [2:0] N       = 3'd4;     // tokens per frame
    localparam [1:0] ST_LOAD = 2'd0;
    localparam [1:0] ST_OUT0 = 2'd1;
    localparam [1:0] ST_OUT1 = 2'd2;

    // ---- registers: CONTROL (state, count, error), COMPUTE (acc) ----
    reg               [1:0] state;     // FSM: LOAD (take tokens), OUT0 (send class), OUT1 (send score)
    reg               [2:0] count;     // beats received in this frame, saturates at N (stops at 4)
    reg  signed       [4:0] acc;       // accumulator: running sum of embeddings = the sentiment score
    reg                     error;     // sticky flag: bad token or wrong frame length; cleared after output

    // ---- IO: handshake strobes ----
    wire       beat_in  = s_valid & s_ready;
    wire       beat_out = m_valid & m_ready;
    // ---- CONTROL: validity checks ----
    wire       in_frame = (count < N);
    wire       bad_item = |s_data[7:2];
    // ---- MEMORY + COMPUTE ----
    wire signed [2:0] embedding;   // learned signed 3-bit number for the current token (from the ROM)
    // activation: threshold step on the sign (strictly greater than 0, so a tie is negative)
    wire       cls      = (acc > 5'sd0);

    // embedding table: generated from train.py; the token itself is the address (combinational lookup)
    text_sentiment_rom rom (.token(s_data[1:0]), .embedding(embedding));

    // ---- IO: stream outputs. Beat 0 = {error, class}; beat 1 = signed score ----
    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);
    assign m_last  = (state == ST_OUT1);
    // sign-extension: replicate the sign bit acc[4] into the top 3 bits to widen 5-bit signed to 8 bits
    assign m_data  = (state == ST_OUT1) ? {{3{acc[4]}}, acc} : {6'b0, error, cls};

    // ---- CONTROL + COMPUTE: sequential update ----
    always @(posedge clk) begin
        if (rst) begin
            state <= ST_LOAD;
            count <= 3'd0;
            acc   <= 5'sd0;
            error <= 1'b0;
        end else begin
            case (state)
                // LOAD: look up and add each valid token on the fly
                ST_LOAD: if (beat_in) begin
                    // saturating count: stop at N so extra beats cannot wrap the counter or be added
                    if (in_frame)             count <= count + 3'd1;
                    // accumulate: sign-extend the 3-bit embedding to 5 bits (copy its sign bit twice) and add;
                    // bad tokens are not added
                    if (in_frame & ~bad_item) acc <= acc + {{2{embedding[2]}}, embedding};
                    // error rules: token > 3, beat past N, or s_last arriving before the 4th beat
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
