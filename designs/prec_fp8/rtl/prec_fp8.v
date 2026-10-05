// SPDX-License-Identifier: Apache-2.0
// prec_fp8 -- one neuron with OCP fp8 (E4M3) weights and an fp16 accumulator: a precision-study engine.
// (Contract: model/precision_hw/spec.md; bit-exact reference: model/precision_hw/golden.py.)
//
// THE NETWORK
//   inputs : 9 pixels, 4-bit unsigned (0..15), raster order, 3x3 image.
//   weights: 9 weights + 1 bias, learned by logistic regression in fp32, then rounded to E4M3 (8 bits each,
//            subnormals flushed to +0: two tiny weights become 0). The ROM is generated (prec_fp8_rom.v).
//   neuron : sum = bias + w0*x0 + ... + w8*x8 ; class = (sum >= 0)  (1 = vertical bar, 0 = horizontal bar).
//   Why this is AI: the decision comes from trained parameters in the ROM, not from a hand-written rule;
//   regenerate the ROM from other data and the same RTL computes another classifier.
//
// NEURAL NETWORK <-> HARDWARE
//   weight memory   -> prec_fp8_rom (constants, 1 byte per weight; synthesis folds them into logic)
//   weight reuse    -> ONE multiply-accumulate (MAC) unit, used serially: one input per clock
//   multiply        -> 4b x 4b significand multiplier + exponent add. The product is EXACT in fp16 (no rounding)
//   accumulate      -> fp16 adder with round-to-nearest-even (RNE) after every add
//   activation      -> sign bit of the accumulator: class = ~acc[15]
//
// WHAT A FLOAT MAC COSTS (the teaching point of this file)
//   An integer MAC is a multiplier and a plain adder. This one adds, on top of the multiplier:
//     (1) an exponent compare and a swap so the larger operand comes first,
//     (2) an alignment right-shifter with guard/round/sticky bits for the smaller operand,
//     (3) an add-or-subtract of the two significands,
//     (4) a leading-zero count (priority encoder) after cancellation,
//     (5) a normalising left shifter and exponent adjust,
//     (6) a round-to-nearest-even incrementer, with its carry-out fix-up,
//     (7) flush-to-zero and pack. Compare prec_int8: (1)-(7) do not exist there.
//   No Inf/NaN/overflow/subnormal logic is needed: spec.md section 4.6 proves none can occur.
//
// BLOCKS: IO (stream handshake), MEMORY (ROM, input register, accumulator), COMPUTE (the float MAC),
//         CONTROL (state machine, beat counter, error flag).
//
// Stream in : 9 beats, pixel in s_data[3:0] (s_data[7:4] must be 0), s_last on the 9th.
// Stream out: 2 beats. beat 0 = {6'b0, error, class}; beat 1 = acc[15:8] ^ acc[7:0] (the fp16 accumulator, folded).
// Latency   : 3 (edge that accepts the last beat -> edge that raises m_valid, both counted).
// PIPELINE  : two-stage MAC. Stage 1 = ROM + multiply + normalise -> product register. Stage 2 = fp16 add -> acc.
//             Why: the single-cycle MAC (ROM to acc) failed setup at 25 ns at the slow corner (max_ss_100C_1v60,
//             WNS -2.144 ns, 16 endpoints). The path was multiplier -> product normalise -> compare/swap -> align
//             shifter -> add -> LZC -> normalise shifter -> round; registering the exact product halves it.
//             Still one beat per cycle; results are bit-identical, only the drain is one cycle longer.
// error     : an item with s_data[7:4] != 0 (it is skipped), or a frame that is not exactly 9 beats.
`timescale 1ns/1ps
`default_nettype none
module prec_fp8 (
    // ---- IO: clock, reset and the valid/ready stream handshake ----
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
    localparam [3:0] N        = 4'd9;
    localparam [2:0] ST_LOAD   = 3'd0;
    localparam [2:0] ST_DRAIN1 = 3'd1;   // stage 1 of the last item (product register loads)
    localparam [2:0] ST_DRAIN2 = 3'd2;   // stage 2 of the last item (accumulator loads)
    localparam [2:0] ST_OUT0   = 3'd3;
    localparam [2:0] ST_OUT1   = 3'd4;

    // ---- registers ----
    reg  [2:0]  state;     // CONTROL: LOAD (take beats), DRAIN1/DRAIN2 (last item through both stages), OUT0, OUT1
    reg  [3:0]  count;     // CONTROL: beats accepted, saturates at 9; also the index of the item being accepted
    reg         error;     // CONTROL: sticky flag, cleared after the result is taken
    reg         x_vld;     // MEMORY: input register holds a valid item for the MAC
    reg  [3:0]  x_pix;     // MEMORY: that item's pixel value 0..15
    reg  [3:0]  x_idx;     // MEMORY: that item's index = ROM address
    reg  [15:0] acc;       // MEMORY: the fp16 accumulator (starts as the widened bias)
    reg         p_vld;     // MEMORY: pipeline register holds a nonzero product for the adder
    reg         p_sign;    // MEMORY: registered fp16 product, sign
    reg  [4:0]  p_exp;     // MEMORY: registered fp16 product, exponent
    reg  [6:0]  p_man;     // MEMORY: registered fp16 product, top 7 mantissa bits (low 3 are always 0)

    // ---- IO: handshake strobes and input checks ----
    wire beat_in  = s_valid & s_ready;
    wire beat_out = m_valid & m_ready;
    wire bad_item = |s_data[7:4];
    wire in_frame = (count < N);

    // ---- MEMORY: parameter ROM ----
    wire [7:0] w8;         // E4M3 weight of the item in x_idx
    wire [7:0] b8;         // E4M3 bias
    prec_fp8_rom rom (.addr(x_idx), .weight(w8), .bias(b8));

    // ---- COMPUTE stage A (cycle 1, ends at the product register): E4M3 x pixel -> exact fp16 product ----
    // E4M3 = S EEEE MMM, value = (-1)^S * 1.MMM * 2^(EEEE-7). EEEE = 0 means zero (FTZ on input: subnormal
    // weights are never stored). The pixel is an integer 1..15. The significand product is
    // (1.MMM as a 4-bit integer 8..15) * pixel = at most 8 bits, so it is exact; value = prod * 2^(EEEE-7-3).
    wire        w_sign = w8[7];
    wire [3:0]  w_exp  = w8[6:3];
    wire [3:0]  w_sig  = {1'b1, w8[2:0]};          // restore the hidden leading one
    wire        do_mac = x_vld & (w_exp != 4'd0) & (x_pix != 4'd0);   // zero product: acc unchanged
    wire [7:0]  sprod  = w_sig * x_pix;            // 4x4 multiplier, 8-bit exact result

    // normalise the 8-bit product: leading-one position pp (0..7), shift it up to bit 7
    reg  [2:0]  pp;
    always @(*) begin
        pp = 3'd0;
        if (sprod[7])      pp = 3'd7;
        else if (sprod[6]) pp = 3'd6;
        else if (sprod[5]) pp = 3'd5;
        else if (sprod[4]) pp = 3'd4;
        else if (sprod[3]) pp = 3'd3;
        else if (sprod[2]) pp = 3'd2;
        else if (sprod[1]) pp = 3'd1;
    end
    wire [7:0]  pnorm  = sprod << (3'd7 - pp);     // leading one now at bit 7
    // value = 1.xxxxxxx * 2^(pp + w_exp - 10)  ->  fp16 biased exponent = pp + w_exp - 10 + 15 = pp + w_exp + 5
    wire [4:0]  p_exp_n = {2'b0, pp} + {1'b0, w_exp} + 5'd5;   // 6..27: always a normal fp16 exponent
    wire [15:0] prod_n = {w_sign, p_exp_n, pnorm[6:0], 3'b000};   // stage-1 result, registered below

    // ---- COMPUTE stage B (cycle 2, product register -> acc): fp16 add, acc + prod, one RNE rounding (from the product register) ----
    wire [15:0] prod   = {p_sign, p_exp, p_man, 3'b000};
    // (1) Unpack and order by magnitude. acc is a normal fp16 or +0; prod is a normal fp16 (never zero here).
    wire        acc_zero = (acc[14:10] == 5'd0);
    wire        a_big    = (acc[14:0] >= prod[14:0]);          // magnitude compare of {exp, mant}: bigger goes first
    wire [15:0] big      = a_big ? acc  : prod;
    wire [15:0] sml      = a_big ? prod : acc;
    wire [4:0]  e_big    = big[14:10];
    wire [4:0]  e_sml    = sml[14:10];
    wire        s_big    = big[15];
    wire        eff_sub  = big[15] ^ sml[15];                  // different signs: magnitudes subtract
    wire [10:0] m_big    = {1'b1, big[9:0]};                   // 11-bit significands with the hidden one
    wire [10:0] m_sml    = {1'b1, sml[9:0]};

    // (2) Align the smaller operand: shift right by the exponent difference. Three extra low bits (guard, round,
    //     sticky) are kept; everything shifted further out is OR-ed into the sticky bit. A shift of 15 or more
    //     moves the whole significand into the sticky region, so the amount is capped at 15.
    wire [4:0]  dexp     = e_big - e_sml;
    wire [3:0]  dsh      = (dexp > 5'd15) ? 4'd15 : dexp[3:0];
    wire [24:0] wide     = {m_sml, 14'b0} >> dsh;               // [24:11] aligned (11 sig + 3 extra), [10:0] lost
    wire [13:0] aligned  = {wide[24:12], wide[11] | (|wide[10:0])};  // fold the lost bits into the sticky bit (bit 0)

    // (3) Add or subtract. |big| >= |sml| so the difference is never negative; the sign is the big operand's.
    wire [14:0] a_op     = {1'b0, m_big, 3'b000};
    wire [14:0] b_op     = {1'b0, aligned};
    wire [14:0] sum      = eff_sub ? (a_op - b_op) : (a_op + b_op);   // bit 14 set only by an addition carry

    // (4) Leading-zero count: position of the highest set bit of sum (0..14).
    reg  [3:0]  lp;
    integer     k;
    always @(*) begin
        lp = 4'd0;
        for (k = 0; k < 15; k = k + 1)
            if (sum[k]) lp = k[3:0];
    end

    // (5) Normalise: shift left so the leading one is at bit 14. The new exponent follows the shift:
    //     bit 13 of sum has weight 2^e_big, so the leading one at lp has weight 2^(e_big + lp - 13).
    wire [14:0] norm     = sum << (4'd14 - lp);
    wire [5:0]  exp_n    = {1'b0, e_big} + {2'b0, lp} - 6'd13;    // 6-bit, may wrap below 1 (cancellation)

    // (6) Round to nearest, ties to even: mantissa = norm[13:4], guard = norm[3], sticky = |norm[2:0].
    wire        guard    = norm[3];
    wire        sticky   = |norm[2:0];
    wire        rnd_up   = guard & (sticky | norm[4]);
    wire [10:0] mant_r   = {1'b0, norm[13:4]} + {10'b0, rnd_up};  // bit 10 = rounding carried out
    wire [5:0]  exp_r    = exp_n + {5'b0, mant_r[10]};            // carry: mantissa is 0, exponent + 1

    // (7) Flush to zero and pack. Exact zero and any result below the smallest normal (exp_r < 1, which wraps
    //     to a negative 6-bit value or 0) become +0. No overflow check (spec.md 4.6).
    wire        ftz      = (sum == 15'd0) | exp_r[5] | (exp_r == 6'd0);
    wire [15:0] sum_f    = ftz ? 16'h0000 : {s_big, exp_r[4:0], mant_r[9:0]};
    // acc = +0: the adder is bypassed, the result is the product itself
    wire [15:0] acc_next = acc_zero ? prod : sum_f;

    // ---- COMPUTE: widen the E4M3 bias to fp16 (exact): exponent + 8, mantissa << 7, zero stays +0 ----
    wire [15:0] bias16 = (b8[6:3] == 4'd0) ? 16'h0000 : {b8[7], 1'b0, b8[6:3] + 4'd8, b8[2:0], 7'b0};

    // ---- IO: outputs. beat 0 = {error, class}; beat 1 = fold of the accumulator ----
    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);
    assign m_last  = (state == ST_OUT1);
    assign m_data  = (state == ST_OUT1) ? (acc[15:8] ^ acc[7:0]) : {6'b0, error, ~acc[15]};

    // ---- CONTROL + MEMORY + COMPUTE: sequential update (spec.md section 6) ----
    always @(posedge clk) begin
        if (rst) begin
            state <= ST_LOAD;
            count <= 4'd0;
            error <= 1'b0;
            x_vld <= 1'b0;
            x_pix <= 4'd0;
            x_idx <= 4'd0;
            p_vld <= 1'b0;
            p_sign <= 1'b0;
            p_exp <= 5'd0;
            p_man <= 7'd0;
            acc   <= bias16;
        end else begin
            // the one MAC, two stages: item k is added while item k+1 is multiplied and item k+2 arrives
            p_vld  <= do_mac;                     // stage 1: register the exact product
            p_sign <= prod_n[15];
            p_exp  <= prod_n[14:10];
            p_man  <= prod_n[9:3];
            if (p_vld) acc <= acc_next;           // stage 2: add into the accumulator
            x_vld <= beat_in & in_frame & ~bad_item;
            x_pix <= s_data[3:0];
            x_idx <= count;
            case (state)
                ST_LOAD: if (beat_in) begin
                    if (in_frame) count <= count + 4'd1;     // saturating at 9
                    if (bad_item | ~in_frame | (s_last & (count != N - 4'd1))) error <= 1'b1;
                    if (s_last) state <= ST_DRAIN1;
                end
                ST_DRAIN1: state <= ST_DRAIN2;               // this edge registers the product of the last item
                ST_DRAIN2: state <= ST_OUT0;                 // this edge adds it into acc
                ST_OUT0:  if (beat_out) state <= ST_OUT1;
                default:  if (beat_out) begin                // ST_OUT1
                    state <= ST_LOAD;
                    count <= 4'd0;
                    error <= 1'b0;
                    acc   <= bias16;
                end
            endcase
        end
    end
endmodule
`default_nettype wire
