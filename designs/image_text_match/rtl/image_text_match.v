// SPDX-License-Identifier: Apache-2.0
// image_text_match -- a tiny MULTIMODAL AI engine: does a one-word caption describe a 3x3 one-bit image?
// A CLIP-like idea at the smallest possible scale. (Spec: model/image_text_match/spec.json; docs/WHY_AI.md.)
//
// THE NETWORK (two encoders, one shared embedding space, a similarity)
//   image encoder : two 3-tap binary neurons, A and B, each with a learned 3-bit kernel and threshold. They are
//                   applied to the 8 LINES of the image (3 columns, 3 rows, 2 diagonals; a line is 3 pixels).
//                   neuron: match = number of line pixels equal to their kernel weight (0..3); fire = match >= thr.
//                   Fires are SUM-pooled per line group (columns / rows / diagonals), so the image embedding is a
//                   6-vector of small counts: (A cols, A rows, A diags, B cols, B rows, B diags), each 0..3.
//   text encoder  : an embedding table (ROM): caption token (0 EMPTY, 1 VERT, 2 HORIZ, 3 DIAG) -> a 6-vector of
//                   signed 3-bit integers in the SAME space as the image embedding.
//   similarity    : dot product of the two 6-vectors (signed 7-bit); class = (similarity >= threshold).
//
// WHY IT IS AI, NOT A HAND-CODED RULE
//   The kernels, the thresholds, the whole embedding table and the similarity threshold are LEARNED by
//   model/image_text_match/train.py from the 2,048 labelled (image, caption) pairs and stored in
//   image_text_match_rom.v (generated). Nothing in this file says "a vertical line is a lit column": the RTL is a
//   generic "scan two neurons over lines, count, look up a text vector, dot, threshold" engine. Training found
//   kernel A = 000, B = 111 (both threshold 3: a dark line, a lit line), caption VERT -> 3 x (B cols), HORIZ ->
//   3 x (B rows), DIAG -> 3 x (B diags), EMPTY -> 1 x (A rows) (all three rows dark), similarity threshold 3.
//   Change the labels, re-train, regenerate the ROM and the SAME RTL describes different captions.
//
// WHY IT IS MULTIMODAL
//   Two different modalities enter: a picture (nine pixels) and language (a caption token). Each has its OWN
//   encoder, but both encoders emit vectors in ONE shared 6-dimensional space, and the answer is how well the two
//   vectors agree (dot product). That is the CLIP recipe (contrastive image-text matching) reduced to a 2,048-pair
//   toy: neither modality is classified alone; the answer exists only in the comparison.
//
// NEURAL NETWORK <-> HARDWARE
//   kernels, thresholds, embedding table -> image_text_match_rom (constants; the table is addressed by `token`)
//   weight reuse (convolution)    -> ONE copy of each neuron, one line per cycle for 8 cycles (area vs speed, 3.2)
//   multiply (1 bit)              -> XNOR: ~(line ^ kernel)
//   accumulate / activation       -> 3-bit match counter, comparator (>= thr)             [fire_a, fire_b]
//   pooling                       -> six 2-bit counters, the one selected by (neuron, line group) adds the fire
//   embedding lookup              -> ROM read indexed by the caption token
//   similarity                    -> six small multipliers and a 6-term adder (dot product), registered in `score`
//   decision                      -> comparator (score >= sim_thr)
//
// BLOCKS
//   IO / stream handshake : s_* input beats, m_* two output beats.
//   MEMORY                : 9-bit frame register (image), 2-bit token register, parameter ROM.
//   COMPUTE               : line mux, neurons A and B, pooling counters, text lookup, dot product, threshold.
//   CONTROL               : FSM (LOAD, COMP, SIM, OUT0, OUT1), beat counter, line counter, error flag.
//
// Stream in : 10 beats, s_last on the 10th. Beats 0..8 = pixels in s_data[0], raster order (pixel i = row*3+col);
//             beat 9 = caption token in s_data[1:0].
// Stream out: 2 beats. Beat 0 m_data = {6'b0, error, class}; beat 1 = similarity score (signed 8-bit, here
//             -63..63 by width), m_last on beat 1.
// Latency   : 10 cycles after the last beat (1 accept + 8 lines + 1 similarity), counted from the edge that accepts
//             the last input beat to the edge that raises m_valid, both included.
//             Line order: columns 0,1,2, rows 0,1,2, diagonals (0,4,8) and (2,4,6); tap i of a line = kernel bit i.
// error     : a pixel beat > 1 or a token beat > 3 (item not used: pixel stays 0, token stays 0 = EMPTY), a beat
//             past the 10th (ignored), or a frame that is not exactly 10 beats (missing items are 0).
`timescale 1ns/1ps
`default_nettype none
module image_text_match (
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
    localparam [3:0] N       = 4'd10;    // beats per frame (9 pixels + 1 caption token)
    localparam [3:0] NPIX    = 4'd9;     // pixel beats; beat index 9 is the token
    localparam [2:0] ST_LOAD = 3'd0;
    localparam [2:0] ST_COMP = 3'd1;
    localparam [2:0] ST_SIM  = 3'd2;
    localparam [2:0] ST_OUT0 = 3'd3;
    localparam [2:0] ST_OUT1 = 3'd4;

    // ---- registers ----
    reg  [2:0] state;              // CONTROL: FSM LOAD (take beats), COMP (8 lines), SIM (dot product), OUT0, OUT1
    reg  [3:0] count;              // CONTROL: beats received in this frame, saturates at N; also the write index
    reg  [8:0] frame;              // MEMORY: the 3x3 image, bit i = pixel i in raster order
    reg  [1:0] token;              // MEMORY: caption token (0 EMPTY, 1 VERT, 2 HORIZ, 3 DIAG)
    reg  [2:0] line;               // CONTROL: line being scanned 0..7 (the convolution position counter)
    reg  [1:0] a_col, a_row, a_dia; // COMPUTE: neuron A fire counts over columns / rows / diagonals (image embedding)
    reg  [1:0] b_col, b_row, b_dia; // COMPUTE: neuron B fire counts over columns / rows / diagonals (image embedding)
    reg signed [6:0] score;        // COMPUTE: similarity = dot(image embedding, text embedding), registered in SIM
    reg        error;              // CONTROL: sticky flag: bad item or wrong frame length; cleared after output

    // ---- IO: handshake strobes ----
    wire       beat_in  = s_valid & s_ready;
    wire       beat_out = m_valid & m_ready;
    // ---- CONTROL: validity checks ----
    wire       in_frame  = (count < N);
    wire       is_token  = (count == NPIX);
    wire       bad_item  = is_token ? (|s_data[7:2]) : (|s_data[7:1]);

    // ---- MEMORY: learned parameters ----
    wire        [2:0]  kernel_a, kernel_b;
    wire        [1:0]  thr_a, thr_b;
    wire signed [6:0]  sim_thr;
    wire        [17:0] emb;        // text embedding of `token`: {B_dia, B_row, B_col, A_dia, A_row, A_col}, signed 3 bits each
    image_text_match_rom rom (.token(token), .kernel_a(kernel_a), .thr_a(thr_a), .kernel_b(kernel_b), .thr_b(thr_b),
                              .sim_thr(sim_thr), .emb(emb));

    // ---- COMPUTE (data movement): line selector. Tap 0 is the first pixel of the line ----
    reg  [2:0] pix;                // the 3 pixels of the current line, bit i = tap i
    always @(*) begin
        case (line)
            3'd0:    pix = {frame[6], frame[3], frame[0]};   // column 0
            3'd1:    pix = {frame[7], frame[4], frame[1]};   // column 1
            3'd2:    pix = {frame[8], frame[5], frame[2]};   // column 2
            3'd3:    pix = {frame[2], frame[1], frame[0]};   // row 0
            3'd4:    pix = {frame[5], frame[4], frame[3]};   // row 1
            3'd5:    pix = {frame[8], frame[7], frame[6]};   // row 2
            3'd6:    pix = {frame[8], frame[4], frame[0]};   // main diagonal
            default: pix = {frame[6], frame[4], frame[2]};   // anti diagonal
        endcase
    end

    // ---- COMPUTE: image encoder, the two neurons shared by all 8 lines ----
    wire [2:0] match_a = ~(pix ^ kernel_a);      // 1-bit multiply per tap: 1 when pixel and kernel weight agree
    wire [2:0] match_b = ~(pix ^ kernel_b);
    wire [1:0] mcnt_a  = {1'b0, match_a[0]} + {1'b0, match_a[1]} + {1'b0, match_a[2]};   // accumulate, 0..3
    wire [1:0] mcnt_b  = {1'b0, match_b[0]} + {1'b0, match_b[1]} + {1'b0, match_b[2]};
    wire       fire_a  = (mcnt_a >= thr_a);       // activation: threshold step
    wire       fire_b  = (mcnt_b >= thr_b);
    wire       g_col   = (line < 3'd3);           // line group: columns 0..2, rows 3..5, diagonals 6..7
    wire       g_row   = (line >= 3'd3) & (line < 3'd6);
    wire       g_dia   = (line >= 3'd6);

    // ---- COMPUTE: similarity = dot product of the image embedding and the text embedding (signed 7-bit) ----
    wire signed [6:0] ia0 = {5'b0, a_col}, ia1 = {5'b0, a_row}, ia2 = {5'b0, a_dia};
    wire signed [6:0] ib0 = {5'b0, b_col}, ib1 = {5'b0, b_row}, ib2 = {5'b0, b_dia};
    wire signed [6:0] t0 = {{4{emb[2]}},  emb[2:0]};
    wire signed [6:0] t1 = {{4{emb[5]}},  emb[5:3]};
    wire signed [6:0] t2 = {{4{emb[8]}},  emb[8:6]};
    wire signed [6:0] t3 = {{4{emb[11]}}, emb[11:9]};
    wire signed [6:0] t4 = {{4{emb[14]}}, emb[14:12]};
    wire signed [6:0] t5 = {{4{emb[17]}}, emb[17:15]};
    wire signed [6:0] dot = ia0 * t0 + ia1 * t1 + ia2 * t2 + ib0 * t3 + ib1 * t4 + ib2 * t5;
    // decision: threshold step on the stored similarity
    wire       cls = (score >= sim_thr);

    // ---- IO: stream outputs. Beat 0 = {error, class}; beat 1 = similarity score sign-extended to 8 bits ----
    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);
    assign m_last  = (state == ST_OUT1);
    assign m_data  = (state == ST_OUT1) ? {score[6], score} : {6'b0, error, cls};

    // ---- CONTROL + COMPUTE: sequential update ----
    always @(posedge clk) begin
        if (rst) begin
            state <= ST_LOAD;
            count <= 4'd0;
            frame <= 9'd0;
            token <= 2'd0;
            line  <= 3'd0;
            {a_col, a_row, a_dia, b_col, b_row, b_dia} <= 12'd0;
            score <= 7'sd0;
            error <= 1'b0;
        end else begin
            case (state)
                // LOAD: store pixels in the frame register and the caption in the token register
                ST_LOAD: if (beat_in) begin
                    // saturating count: stop at N so extra beats cannot wrap or overwrite the frame
                    if (in_frame) count <= count + 4'd1;
                    if (in_frame & ~bad_item) begin
                        if (is_token) token <= s_data[1:0];
                        else          frame[count[3:0]] <= s_data[0];
                    end
                    // error rules: bad item, beat past N, or s_last arriving before the 10th beat
                    if (bad_item | ~in_frame | (s_last & (count != N - 4'd1))) error <= 1'b1;
                    if (s_last) begin
                        state <= ST_COMP;
                        line  <= 3'd0;
                    end
                end
                // COMP: one line per cycle through both neurons; sum-pool the fires into the group counts
                ST_COMP: begin
                    if (g_col) begin a_col <= a_col + {1'b0, fire_a}; b_col <= b_col + {1'b0, fire_b}; end
                    if (g_row) begin a_row <= a_row + {1'b0, fire_a}; b_row <= b_row + {1'b0, fire_b}; end
                    if (g_dia) begin a_dia <= a_dia + {1'b0, fire_a}; b_dia <= b_dia + {1'b0, fire_b}; end
                    line <= line + 3'd1;
                    if (line == 3'd7) state <= ST_SIM;
                end
                // SIM: dot product of the image embedding with the caption's text embedding
                ST_SIM: begin
                    score <= dot;
                    state <= ST_OUT0;
                end
                ST_OUT0: if (beat_out) state <= ST_OUT1;
                default: if (beat_out) begin       // ST_OUT1
                    state <= ST_LOAD;
                    count <= 4'd0;
                    frame <= 9'd0;
                    token <= 2'd0;
                    {a_col, a_row, a_dia, b_col, b_row, b_dia} <= 12'd0;
                    score <= 7'sd0;
                    error <= 1'b0;
                end
            endcase
        end
    end
endmodule
`default_nettype wire
