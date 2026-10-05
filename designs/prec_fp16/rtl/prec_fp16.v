// SPDX-License-Identifier: Apache-2.0
// prec_fp16 -- a NEURAL NETWORK INFERENCE ENGINE in IEEE binary16 (half precision): one neuron
//   sum = bias + w0*x0 + ... + w8*x8,  class = (sum >= 0),
// evaluated on a 3x3 image by ONE floating-point multiply-accumulate (MAC) unit reused serially, one input per clock.
// Spec: model/precision_hw/spec.md (the contract); bit-exact reference: model/precision_hw/golden.py.
//
// WHY THIS IS AI
//   The ten constants in prec_fp16_rom are LEARNED, not written: logistic regression on labelled 3x3 images (is the
//   bright bar vertical or horizontal?) gave fp32 weights, here rounded to fp16. The RTL is a generic "weighted sum
//   then threshold" neuron. Change the ROM and the same hardware computes a different decision. This design is one
//   of seven (bin, tern, int4, int8, fp8, fp16, bf16) that differ ONLY in number format: it measures what a
//   16-bit float with a 5-bit exponent and 11-bit significand costs in gates.
//
// NEURAL NETWORK <-> HARDWARE
//   weights, bias     -> prec_fp16_rom (constants, 16 bits each; folded into logic by synthesis, no memory)
//   inputs            -> 4-bit pixels 0..15 (exact in fp16), one per beat, raster order
//   multiply  w*x     -> prec_fp16_fmul (MAC stage 1): 11-bit significand x 4-bit pixel multiplier, exponent add,
//                        normalise, round-to-nearest-even (RNE) to fp16
//   accumulate        -> prec_fp16_fadd (MAC stage 2, after the product register): fp16 adder (align, add/subtract, leading-zero count, normalise, RNE)
//   activation        -> sign bit of the accumulator: class = ~acc[15]  (acc >= 0)
//   weight reuse      -> ONE MAC, 9 cycles, instead of 9 multipliers + an adder tree (area vs speed, docs/WHY_AI.md)
//
// FLOAT RULES (spec.md 4.4, all implemented below, nothing more)
//   * Every product is rounded to fp16 (RNE), then every sum is rounded to fp16 (RNE): unfused, two roundings/step.
//   * Flush to zero: exponent field 0 is zero on input; a result below 2^-14 becomes +0. No subnormal logic.
//   * No Inf / NaN / overflow logic: proved impossible for |parameter| <= 256 (spec.md 4.6).
//   * -0 never occurs: exact cancellation gives +0.
//
// BLOCKS
//   IO      : s_* input beats, m_* two output beats.
//   MEMORY  : prec_fp16_rom (weights/bias); x_* input stage register; acc (the running sum).
//   COMPUTE : prec_fp16_fmul / prec_fp16_fadd (the float MAC in two stages), product register p_*, result bytes.
//   CONTROL : FSM LOAD, DRAIN1, DRAIN2, OUT0, OUT1; beat counter; error flag.
//
// Stream in : 9 beats, pixel in s_data[3:0] (s_data[7:4] must be 0), raster order, s_last on the 9th.
// Stream out: beat 0 = {6'b0, error, class}; beat 1 = acc[15:8] ^ acc[7:0] (every accumulator bit reaches the pins),
//             m_last on beat 1.
// Latency   : 3 cycles. Edge E0 accepts the last beat into the input stage; E1 registers the rounded product;
//             E2 adds it into acc; m_valid rises after E2. (A single-cycle MAC did not meet 25 ns at the slow
//             corner, measured on the sibling prec_fp8, so the MAC is cut in two: spec.md section 6 fallback.)
// error     : a beat with s_data[7:4] != 0 (that item is skipped), or a frame that is not exactly 9 beats (extra
//             beats are ignored; missing items are skipped). The result is still produced.
`timescale 1ns/1ps
`default_nettype none

// ============================================================================================================
// prec_fp16_lzc -- leading-zero count of a W-bit vector (priority encoder). Used to re-normalise after the
// multiply (at most 4 places) and after the subtraction (up to W places when nearly equal numbers cancel).
// ============================================================================================================
module prec_fp16_lzc #(parameter W = 8, parameter LW = 4) (
    input  wire [W-1:0]  v,
    output reg  [LW-1:0] n      // number of zeros above the highest set bit (0 if v == 0; callers test v == 0)
);
    integer i;
    always @(*) begin
        n = {LW{1'b0}};
        for (i = 0; i < W; i = i + 1)
            if (v[i]) n = W - 1 - i;      // later (higher) i overrides: the highest set bit wins
    end
endmodule

// ============================================================================================================
// prec_fp16_fmul -- MAC stage 1: pbits = RNE( w * x ), the product already rounded to the format.
//   Purely combinational; its output is registered by the engine (p_bits), which is the pipeline cut.
//   EW exponent bits, MW stored mantissa bits (fp16: 5 and 10). Significand = {1, mantissa}, SIG = MW+1 bits.
//   Costs by format: the multiplier is SIG x 4 (fp16: 11x4; bf16: 8x4); the exponent path is EW wide.
//   Preconditions (guaranteed by the ROM, spec 4.6): w is normal or +0, never Inf/NaN/subnormal.
//   p_zero (weight is zero or pixel is zero) means "no step": the engine then does not load the product register's
//   valid bit, so the adder never sees it (spec rule 6: acc unchanged).
// ============================================================================================================
module prec_fp16_fmul #(parameter EW = 5, parameter MW = 10) (
    input  wire [EW+MW:0] w,      // weight
    input  wire [3:0]     x,      // pixel 0..15, unsigned integer
    output wire           p_zero, // zero product: skip the accumulate
    output wire [EW+MW:0] pbits   // rounded product
);
    localparam SIG = MW + 1;          // significand bits including the hidden 1
    localparam XW  = EW + 3;          // internal signed exponent width (headroom for +4 and underflow)
    localparam PW  = SIG + 4;         // product width: SIG x 4 bits
    localparam LW  = 5;               // leading-zero count width (enough for SW <= 17)

    // ---------------------------------------------------------------------------------------------------
    // STAGE 1a  unpack the weight (FTZ: exponent field 0 means zero, mantissa ignored)
    // ---------------------------------------------------------------------------------------------------
    wire            ws   = w[EW+MW];
    wire [EW-1:0]   we   = w[EW+MW-1:MW];
    wire [MW-1:0]   wm   = w[MW-1:0];
    assign          p_zero = (we == {EW{1'b0}}) | (x == 4'd0);   // zero product: acc unchanged (spec rule 6)
    wire [SIG-1:0]  sw   = {1'b1, wm};                           // weight significand with the hidden bit

    // STAGE 1b  significand multiply: SIG-bit significand times the 4-bit integer pixel, exact (PW bits).
    //           Because the pixel is an integer 1..15 it needs no float conversion (rule 1).
    wire [PW-1:0]   prod = {{4{1'b0}}, sw} * {{SIG{1'b0}}, x};

    // STAGE 1c  normalise: prod = 1.xxx * 2^q with q in [MW, MW+4]; shift the leading one up to bit PW-1.
    wire [LW-1:0]   plz;
    prec_fp16_lzc #(.W(PW), .LW(LW)) u_plz (.v(prod), .n(plz));
    wire [PW-1:0]   pn   = prod << plz;
    // value = prod * 2^(we - bias - MW)  =>  biased exponent of the product = we + (q - MW) = we + 4 - plz
    wire [XW-1:0]   pexp0 = {{(XW-EW){1'b0}}, we} + 4 - plz;

    // STAGE 1d  round to nearest even: keep the top SIG bits, guard = next bit, sticky = OR of the rest.
    wire [SIG-1:0]  pm_keep = pn[PW-1:PW-SIG];
    wire            pg      = pn[PW-SIG-1];
    wire            ps      = |pn[PW-SIG-2:0];
    wire            pup     = pg & (ps | pm_keep[0]);
    wire [SIG:0]    pm_rnd  = {1'b0, pm_keep} + pup;             // may carry out (all ones rounds up to 2.0)
    wire [XW-1:0]   pexp    = pexp0 + pm_rnd[SIG];               // carry: mantissa becomes 0, exponent + 1
    // pack the rounded product (the stored mantissa is the low MW bits; after a carry they are all zero).
    // The product is never below the smallest normal (|w| >= 2^emin, x >= 1), so no flush is needed here.
    assign          pbits  = {ws, pexp[EW-1:0], pm_rnd[MW-1:0]};

endmodule

// ============================================================================================================
// prec_fp16_fadd -- MAC stage 2: res = RNE( acc + pbits ), the adder. Purely combinational.
//   pbits comes from the product register, so this path starts at a flip-flop and holds only: compare, align,
//   add/subtract, leading-zero count, normalise, round, flush.
//   Preconditions: acc is normal or +0; pbits is a rounded normal product (never zero: zero products are skipped).
// ============================================================================================================
module prec_fp16_fadd #(parameter EW = 5, parameter MW = 10) (
    input  wire [EW+MW:0] acc,    // running sum
    input  wire [EW+MW:0] pbits,  // rounded product from the product register
    output wire [EW+MW:0] res     // new accumulator
);
    localparam SIG = MW + 1;
    localparam XW  = EW + 3;          // internal signed exponent width (headroom for +1 and underflow)
    localparam AW  = SIG + 3;         // adder operand: significand + guard, round, sticky
    localparam SW  = AW + 1;          // adder sum: one carry bit more
    localparam LW  = 5;               // leading-zero count width (enough for SW <= 17)

    // ---------------------------------------------------------------------------------------------------
    // STAGE 2a  unpack the two adder operands a = acc, b = product (both normal, or a == 0)
    // ---------------------------------------------------------------------------------------------------
    wire            a_zero = (acc[EW+MW-1:MW] == {EW{1'b0}});
    // order by magnitude so the subtraction is never negative: uppr gets the larger {exponent, mantissa}
    wire            a_big  = (acc[EW+MW-1:0] >= pbits[EW+MW-1:0]);
    wire [EW+MW:0]  uppr    = a_big ? acc   : pbits;
    wire [EW+MW:0]  lowr  = a_big ? pbits : acc;
    wire            sbig   = uppr[EW+MW];
    wire            ssmall = lowr[EW+MW];
    wire [EW-1:0]   ebig   = uppr[EW+MW-1:MW];
    wire [EW-1:0]   esmall = lowr[EW+MW-1:MW];
    wire            sub    = sbig ^ ssmall;                      // different signs: magnitude subtraction

    // STAGE 2b  align: shift the smaller significand right by the exponent difference. Operands carry three
    //           extra low bits (guard, round, sticky). Bits shifted out below them are OR-ed into the sticky bit.
    wire [EW-1:0]   dexp   = ebig - esmall;
    wire [AW-1:0]   sig_b  = {1'b1, uppr[MW-1:0],   3'b000};
    wire [AW-1:0]   sig_s  = {1'b1, lowr[MW-1:0], 3'b000};
    wire [2*AW-1:0] sh     = {sig_s, {AW{1'b0}}} >> dexp;        // upper half: aligned, lower half: lost bits
    wire [AW-1:0]   al_hi  = sh[2*AW-1:AW];
    wire            al_st  = |sh[AW-1:0];
    wire [AW-1:0]   sig_a  = {al_hi[AW-1:1], al_hi[0] | al_st};  // aligned lowr operand with sticky folded in

    // STAGE 2c  add or subtract the significands (SW bits, bit SW-1 is the carry out)
    wire [SW-1:0]   sum    = sub ? ({1'b0, sig_b} - {1'b0, sig_a}) : ({1'b0, sig_b} + {1'b0, sig_a});
    wire            sum_z  = (sum == {SW{1'b0}});                // exact cancellation: result +0

    // STAGE 2d  leading-zero count and normalise (left shift so the leading one sits at bit SW-1)
    wire [LW-1:0]   slz;
    prec_fp16_lzc #(.W(SW), .LW(LW)) u_slz (.v(sum), .n(slz));
    wire [SW-1:0]   sn     = sum << slz;

    // STAGE 2e  round to nearest even (keep SIG bits, guard, sticky of the remaining low bits)
    wire [SIG-1:0]  sm_keep = sn[SW-1:SW-SIG];
    wire            sg      = sn[SW-SIG-1];
    wire            ss      = |sn[SW-SIG-2:0];
    wire            sup     = sg & (ss | sm_keep[0]);
    wire [SIG:0]    sm_rnd  = {1'b0, sm_keep} + sup;
    // exponent: carry bit has weight 2^(ebig+1) => exponent = ebig + 1 - slz (+1 if rounding carried out)
    wire signed [XW-1:0] sexp = $signed({{(XW-EW){1'b0}}, ebig}) + 1 - $signed({{(XW-LW){1'b0}}, slz})
                                + $signed({{(XW-1){1'b0}}, sm_rnd[SIG]});
    // FTZ: a result below the smallest normal (biased exponent < 1) is +0 (spec rule 5); exact zero is +0
    wire            s_flush = sum_z | (sexp < 1);
    wire [EW+MW:0]  sbits   = s_flush ? {(EW+MW+1){1'b0}} : {sbig, sexp[EW-1:0], sm_rnd[MW-1:0]};

    // result select: zero acc takes the (already rounded) product exactly
    assign res = a_zero ? pbits : sbits;
endmodule

// ============================================================================================================
// prec_fp16 -- the engine: stream interface, schedule and the one MAC (spec.md section 6).
// ============================================================================================================
module prec_fp16 (
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
    localparam [3:0] N        = 4'd9;     // inputs per frame
    localparam [2:0] ST_LOAD   = 3'd0;    // accept input beats
    localparam [2:0] ST_DRAIN1 = 3'd1;    // the last item's product is registered on the edge that leaves this state
    localparam [2:0] ST_DRAIN2 = 3'd2;    // that product is added into acc on the edge that leaves this state
    localparam [2:0] ST_OUT0   = 3'd3;    // offer beat 0
    localparam [2:0] ST_OUT1   = 3'd4;    // offer beat 1

    // ---- registers: CONTROL ----
    reg  [2:0]  state;     // FSM state
    reg  [3:0]  count;     // beats accepted in this frame, saturates at N; also the index of the next item
    reg         error;     // sticky: bad item or wrong frame length; cleared after the result is taken
    // ---- registers: MEMORY (input stage between the stream and the MAC) ----
    reg         x_vld;     // the input stage holds a valid item to multiply-accumulate on the next edge
    reg  [3:0]  x_pix;     // that item's pixel value
    reg  [3:0]  x_idx;     // that item's raster index = ROM address
    // ---- registers: COMPUTE ----
    // Pipeline register between MAC stage 1 (multiply + round) and stage 2 (add + round). Without it the whole
    // ROM -> multiply -> round -> add -> round chain is one path and misses 25 ns at the slow corner. One beat per
    // clock is kept: while item k is added, item k+1 is multiplied. p_vld is that stage's valid bit; it is 0 for a
    // skipped item and for a zero product, so the adder then leaves acc alone.
    reg         p_vld;     // p_bits holds a nonzero product to add on the next edge
    reg  [15:0] p_bits;    // the product, already rounded to the format
    reg  [15:0] acc;       // running sum, fp16 bits; starts at the bias, class = ~acc[15]

    // ---- IO: handshake strobes ----
    wire        beat_in  = s_valid & s_ready;
    wire        beat_out = m_valid & m_ready;
    wire        bad_item = |s_data[7:4];  // pixel is a 4-bit value: anything above bit 3 is an error

    // ---- MEMORY: learned parameters (generated, constant) ----
    wire [15:0] weight, bias;
    prec_fp16_rom rom (.addr(x_idx), .weight(weight), .bias(bias));

    // ---- COMPUTE: the ONE float MAC, two pipeline stages, 1 step per clock ----
    wire        p_zero;
    wire [15:0] pbits;
    prec_fp16_fmul #(.EW(5), .MW(10)) mul (.w(weight), .x(x_pix), .p_zero(p_zero), .pbits(pbits));
    wire [15:0] acc_next;
    prec_fp16_fadd #(.EW(5), .MW(10)) add (.acc(acc), .pbits(p_bits), .res(acc_next));

    // ---- IO: stream outputs ----
    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);   // rises 3 cycles after the last beat
    assign m_last  = (state == ST_OUT1);
    // beat 0 = {error, class}; class = acc >= 0 = ~sign. beat 1 folds all 16 accumulator bits into a byte.
    assign m_data  = (state == ST_OUT1) ? (acc[15:8] ^ acc[7:0]) : {6'b0, error, ~acc[15]};

    // ---- CONTROL + COMPUTE: sequential update ----
    always @(posedge clk) begin
        if (rst) begin
            state <= ST_LOAD;
            count <= 4'd0;
            error <= 1'b0;
            x_vld <= 1'b0;
            x_pix <= 4'd0;
            x_idx <= 4'd0;
            p_vld <= 1'b0;
            p_bits <= 16'd0;
            acc   <= bias;               // bias first: costs no cycle
        end else begin
            // stage 2: add the product registered on the previous edge (overlaps the multiply of the next item)
            if (p_vld) acc <= acc_next;
            // stage 1: multiply + round the item in the input stage; a zero product is dropped here (no step)
            p_vld  <= x_vld & ~p_zero;
            p_bits <= pbits;
            // input stage: items past the 9th or with a bad value are not multiplied
            x_vld <= beat_in & (count < N) & ~bad_item;
            x_pix <= s_data[3:0];
            x_idx <= count;
            case (state)
                ST_LOAD: if (beat_in) begin
                    if (count < N) count <= count + 4'd1;        // saturating beat count
                    // error: bad item, beat past the 9th, or s_last before the 9th beat
                    if (bad_item | (count >= N) | (s_last & (count != N - 4'd1))) error <= 1'b1;
                    if (s_last) state <= ST_DRAIN1;
                end
                ST_DRAIN1: state <= ST_DRAIN2;
                ST_DRAIN2: state <= ST_OUT0;
                ST_OUT0:  if (beat_out) state <= ST_OUT1;
                default:  if (beat_out) begin                    // ST_OUT1: result taken, re-arm
                    state <= ST_LOAD;
                    count <= 4'd0;
                    error <= 1'b0;
                    acc   <= bias;
                end
            endcase
        end
    end
endmodule
`default_nettype wire
