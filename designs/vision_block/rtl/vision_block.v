// SPDX-License-Identifier: Apache-2.0
// vision_block -- a NEURAL NETWORK INFERENCE ENGINE: a 2x2 binary convolution (one neuron reused at four window
// positions) followed by max-pooling, over a 3x3 one-bit image. (Spec: model/tiny_ai/spec.json; docs/WHY_AI.md.)
//
// THE NETWORK (convolution + pooling)
//   inputs      : 9 pixels, 1 bit each, raster order, held in a frame register.
//   kernel      : 4 one-bit weights (2x2), the SAME four weights applied at every window position.
//   weighted sum: for each window, mcount = number of pixels equal to their kernel weight (0..4).
//   activation  : threshold step, fire = (mcount >= threshold), per window.
//   pooling     : class = OR of the four fire results; score = max of the four mcounts (max-pooling).
//   output      : class (1 bit) and score (best window match count).
//
// THE WEIGHTS ARE LEARNED, NOT WRITTEN
//   model/tiny_ai/train.py fits the kernel and threshold from labelled examples (all 512 images, each tagged with
//   the right answer) and emits vision_block_rom.v (generated, do not edit). Training found kernel 1,1,1,1 and
//   threshold 4, i.e. "a fully lit 2x2 block exists". The RTL has no such rule: it is a generic "slide a neuron,
//   count matches, threshold, pool" engine. Regenerate the ROM from other labels and the SAME RTL computes a
//   different rule, e.g. a kernel with other 0/1 weights detects a different 2x2 pattern, or a lower threshold
//   accepts partial matches (docs/WHY_AI.md section 4 lists such ROM-only changes for the dense neuron).
//   Unlike a hand-coded rule (a chain of ANDs or `if` tests over fixed pixel positions), the decision comes from
//   data, and the structure (one neuron + window selector + pooling) is generic.
//
// NEURAL NETWORK <-> HARDWARE
//   weight memory   -> vision_block_rom (kernel + threshold, constant: no address, since the kernel is shared)
//   weight reuse    -> ONE neuron plus a window selector, evaluated serially, one window per cycle (4 cycles),
//                      instead of four copies of the neuron (the area-versus-speed trade of docs/WHY_AI.md 3.2)
//   multiply (1 bit)-> XNOR: ~(window ^ kernel)
//   accumulate      -> adder of the four match bits (mcount)
//   activation      -> comparator (mcount >= threshold)            [fire]
//   pooling         -> OR across windows ("pooled") and max across windows ("best")
//
// BLOCKS (the four constructs of ../open-ai-silicon/docs/ARCH_STUDY_PLAN.md section 2)
//   IO / stream handshake : s_* input beats, m_* two output beats.
//   MEMORY                : kernel + threshold ROM; 9-bit frame register (the image).
//   COMPUTE               : the one neuron (match, mcount, fire) and the pooling registers.
//   CONTROL               : FSM (LOAD, COMP, OUT0, OUT1), beat counter, window counter, error flag.
//
// Stream in : 9 beats, pixel in s_data[0] (raster order), s_last on the 9th. Stored in a 9-bit frame register.
// Stream out: 2 beats. Beat 0 m_data = {6'b0, error, class}; beat 1 m_data = score (largest window match count,
//             0..4), m_last on beat 1.
// Latency   : 5 cycles after the last beat (4 compute cycles, one per window, then the output).
//             Windows in order: (0,0), (0,1), (1,0), (1,1).
// error     : a beat with s_data > 1 (pixel left 0), or a frame that is not exactly 9 beats (beats past 9 are
//             ignored; missing pixels are 0).
`timescale 1ns/1ps
`default_nettype none
module vision_block (
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
    localparam [3:0] N       = 4'd9;     // pixels per frame
    localparam [1:0] ST_LOAD = 2'd0;
    localparam [1:0] ST_COMP = 2'd1;
    localparam [1:0] ST_OUT0 = 2'd2;
    localparam [1:0] ST_OUT1 = 2'd3;

    // ---- registers: CONTROL (state, count, win, error), MEMORY (frame), COMPUTE (pooled, best) ----
    reg  [1:0] state;              // FSM: LOAD (take pixels), COMP (evaluate 4 windows), OUT0, OUT1
    reg  [3:0] count;              // beats received in this frame, saturates at N; also the frame write index
    reg  [8:0] frame;              // frame buffer: the 3x3 image, bit i = pixel i in raster order
    reg  [1:0] win;                // window being evaluated 0..3: the convolution position counter
    reg        pooled;             // max-pool of the 1-bit fire results (OR over windows so far) = class
    reg  [2:0] best;               // max-pool of the match counts (largest window score so far) = score
    reg        error;              // sticky flag: bad pixel value or wrong frame length; cleared after output

    // ---- IO: handshake strobes ----
    wire       beat_in  = s_valid & s_ready;
    wire       beat_out = m_valid & m_ready;
    // ---- CONTROL: validity checks ----
    wire       in_frame = (count < N);
    wire       bad_item = |s_data[7:1];

    // ---- COMPUTE (data movement): window selector, the "slide" of the convolution ----
    // select the current 2x2 window of the 3x3 frame (bit 0 TL, 1 TR, 2 BL, 3 BR); frame bit = row*3 + col,
    // so window (0,0) takes frame[0], [1], [3], [4], and each later window shifts by one column or row
    reg  [3:0] window;
    always @(*) begin
        case (win)
            2'd0:    window = {frame[4], frame[3], frame[1], frame[0]};
            2'd1:    window = {frame[5], frame[4], frame[2], frame[1]};
            2'd2:    window = {frame[7], frame[6], frame[4], frame[3]};
            default: window = {frame[8], frame[7], frame[5], frame[4]};
        endcase
    end

    // ---- MEMORY + COMPUTE: the one neuron, shared by all four windows ----
    // kernel / threshold: learned (train.py), constant ROM outputs
    wire [3:0] kernel;
    wire [2:0] threshold;
    vision_block_rom rom (.kernel(kernel), .threshold(threshold));
    // 1-bit multiply per tap: ~(a ^ w) is 1 when window pixel and kernel weight agree
    wire [3:0] match = ~(window ^ kernel);
    // accumulate: add the four match bits (zero-extended to 3 bits, 0..4)
    wire [2:0] mcount = {2'b0, match[0]} + {2'b0, match[1]} + {2'b0, match[2]} + {2'b0, match[3]};
    // activation: threshold step
    wire       fire   = (mcount >= threshold);

    // ---- IO: stream outputs. Beat 0 = {error, class (pooled)}; beat 1 = score (best) ----
    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);
    assign m_last  = (state == ST_OUT1);
    assign m_data  = (state == ST_OUT1) ? {5'b0, best} : {6'b0, error, pooled};

    // ---- CONTROL + COMPUTE: sequential update ----
    always @(posedge clk) begin
        if (rst) begin
            state  <= ST_LOAD;
            count  <= 4'd0;
            frame  <= 9'd0;
            win    <= 2'd0;
            pooled <= 1'b0;
            best   <= 3'd0;
            error  <= 1'b0;
        end else begin
            case (state)
                // LOAD: store pixels into the frame buffer
                ST_LOAD: if (beat_in) begin
                    // saturating count: stop at N so extra beats cannot wrap or overwrite the frame
                    if (in_frame)             count <= count + 4'd1;
                    if (in_frame & ~bad_item) frame[count] <= s_data[0];
                    // error rules: pixel > 1, beat past N, or s_last arriving before the 9th beat
                    if (bad_item | ~in_frame | (s_last & (count != N - 4'd1))) error <= 1'b1;
                    if (s_last) begin
                        state <= ST_COMP;
                        win   <= 2'd0;
                    end
                end
                // COMP: one window per cycle through the one neuron; pool results as they come
                ST_COMP: begin
                    pooled <= pooled | fire;
                    if (mcount > best) best <= mcount;
                    win <= win + 2'd1;
                    if (win == 2'd3) state <= ST_OUT0;
                end
                ST_OUT0: if (beat_out) state <= ST_OUT1;
                default: if (beat_out) begin       // ST_OUT1
                    state  <= ST_LOAD;
                    count  <= 4'd0;
                    frame  <= 9'd0;
                    pooled <= 1'b0;
                    best   <= 3'd0;
                    error  <= 1'b0;
                end
            endcase
        end
    end
endmodule
`default_nettype wire
