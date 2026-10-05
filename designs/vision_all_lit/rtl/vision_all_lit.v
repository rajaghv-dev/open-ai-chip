// SPDX-License-Identifier: Apache-2.0
// vision_all_lit -- a NEURAL NETWORK INFERENCE ENGINE: one binary threshold neuron over a 2x2 one-bit image.
// (Spec: model/tiny_ai/spec.json. Background: docs/WHY_AI.md.)
//
// THE NETWORK (a single "dense" neuron: every input has its own weight)
//   inputs      : 4 pixels p0..p3, 1 bit each, arriving one per beat in raster order.
//   weights     : w0..w3, 1 bit each. Multiply is "pixel XNOR weight" (1 when they agree).
//   weighted sum: score = number of pixels that equal their weight (0..4).
//   activation  : threshold step, class = (score >= threshold).
//   output      : class (1 bit) and the raw score, so a reader can see how close the call was.
//
// THE WEIGHTS ARE LEARNED, NOT WRITTEN
//   model/tiny_ai/train.py fits the weights and the threshold from labelled examples (all 16 images, each tagged
//   with the right answer) and emits vision_all_lit_rom.v (generated, do not edit). Training found weights
//   1,1,1,1 and threshold 4, which happens to behave like a 4-input AND. This RTL contains no such rule: it is
//   a generic "count matches, compare with threshold" engine. Regenerate the ROM from other labels and the SAME
//   RTL computes a different rule (docs/WHY_AI.md section 4): "at least three lit" -> threshold 3; "any pixel
//   lit" -> threshold 1; "top row lit" -> weights 1,1,0,0; "all dark" -> weights 0,0,0,0.
//   Compare a hand-coded rule (an AND gate or `if (p0 & p1 & p2 & p3)`): there the knowledge is in the wiring
//   and a new rule means a new circuit. Here the knowledge is in the data (ROM) and the structure is generic.
//   (A single neuron cannot learn "exactly two lit": that is XOR-like and needs a second layer; section 5.)
//
// NEURAL NETWORK <-> HARDWARE
//   weight memory         -> vision_all_lit_rom (indexed by the beat counter; also supplies the threshold)
//   multiply (1 bit)      -> XNOR: ~(pixel ^ weight)                 [match]
//   accumulate            -> 3-bit "score" register, +1 per match
//   activation            -> comparator (score >= threshold)         [cls]
//   no pooling, no layers -> single neuron, so output is just class + score
//
// BLOCKS (the four constructs of ../open-ai-silicon/docs/ARCH_STUDY_PLAN.md section 2)
//   IO / stream handshake : s_* input beats, m_* two output beats (below, with s_ready / m_valid / m_last).
//   MEMORY                : weights and threshold in the ROM. No pixel storage: pixels are consumed as they arrive.
//   COMPUTE               : match, score accumulate, cls compare.
//   CONTROL               : state FSM (LOAD, OUT0, OUT1), beat counter, error flag.
//
// Stream in : 4 beats, pixel in s_data[0] (raster order), s_last on the 4th.
// Stream out: 2 beats. Beat 0 m_data = {6'b0, error, class}; beat 1 m_data = score (unsigned match count, 0..4),
//             m_last on beat 1.
// Result: 1 cycle after the last input beat.
// error     : a beat with s_data > 1 (not scored), or a frame that is not exactly 4 beats (beats past 4 are ignored).
`timescale 1ns/1ps
`default_nettype none
module vision_all_lit (
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
    localparam [2:0] N       = 3'd4;     // number of inputs (pixels) per frame
    localparam [1:0] ST_LOAD = 2'd0;
    localparam [1:0] ST_OUT0 = 2'd1;
    localparam [1:0] ST_OUT1 = 2'd2;

    // ---- CONTROL: FSM and counters; COMPUTE: score ----
    reg  [1:0] state;              // FSM: LOAD (take pixels), OUT0 (send class), OUT1 (send score)
    reg  [2:0] count;              // beats received in this frame, saturates at N (stops at 4; extra beats = error);
                                   // also the ROM address: selects which weight goes with the current pixel
    reg  [2:0] score;              // the neuron's weighted sum: matching pixels so far, 0..N (accumulator)
    reg        error;              // sticky flag: bad pixel value or wrong frame length; cleared after output

    // ---- IO: handshake strobes ----
    wire       beat_in  = s_valid & s_ready;
    wire       beat_out = m_valid & m_ready;
    // ---- CONTROL: validity checks ----
    wire       in_frame = (count < N);
    wire       bad_item = |s_data[7:1];
    // ---- MEMORY + COMPUTE: the neuron ----
    wire       weight;             // learned 1-bit weight for the current pixel (from the ROM)
    wire [2:0] threshold;          // learned activation threshold (from the ROM)
    // 1-bit multiply: XOR is 1 when bits differ, so ~(a ^ w) is 1 when pixel and weight agree.
    // That is the binary-neural-network form of "input * weight", contributing 0 or 1 to the score.
    wire       match    = ~(s_data[0] ^ weight);
    // activation: threshold step on the accumulated score
    wire       cls      = (score >= threshold);

    // weight memory: generated from train.py; addressed by the beat count (only [1:0] needed for 4 weights)
    vision_all_lit_rom rom (.addr(count[1:0]), .weight(weight), .threshold(threshold));

    // ---- IO: stream outputs. Beat 0 = {error, class}; beat 1 = score ----
    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);
    assign m_last  = (state == ST_OUT1);
    assign m_data  = (state == ST_OUT1) ? {5'b0, score} : {6'b0, error, cls};

    // ---- CONTROL + COMPUTE: sequential update ----
    always @(posedge clk) begin
        if (rst) begin
            state <= ST_LOAD;
            count <= 3'd0;
            score <= 3'd0;
            error <= 1'b0;
        end else begin
            case (state)
                // LOAD: each valid pixel is scored on the fly (weight selected by count)
                ST_LOAD: if (beat_in) begin
                    // saturating count: stop at N so extra beats cannot wrap the counter or re-score
                    if (in_frame)                      count <= count + 3'd1;
                    // accumulate: add 1 to the sum when the pixel matches its weight (bad pixels are not scored)
                    if (in_frame & ~bad_item & match)  score <= score + 3'd1;
                    // error rules: pixel > 1, beat past N, or s_last arriving before the 4th beat
                    if (bad_item | ~in_frame | (s_last & (count != N - 3'd1))) error <= 1'b1;
                    if (s_last)                        state <= ST_OUT0;  // frame done; next cycle result is valid
                end
                ST_OUT0: if (beat_out) state <= ST_OUT1;
                ST_OUT1: if (beat_out) begin    // after the score beat is taken, clear for the next frame
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
