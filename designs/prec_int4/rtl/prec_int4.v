// SPDX-License-Identifier: Apache-2.0
// prec_int4 -- a NEURAL NETWORK INFERENCE ENGINE: one neuron with 4-bit signed integer weights, one of the seven
// number-format engines of the precision study (model/precision_hw/spec.md; golden model: model/precision_hw/golden.py).
//
// THE NETWORK
//   inputs   : 9 pixels, 4-bit unsigned (0..15), raster order, streamed one per beat.
//   neuron   : sum = bias + w[0]*x[0] + ... + w[8]*x[8],   class = (sum >= 0)   (1 = vertical bar, 0 = horizontal bar).
//   weights  : trained by logistic regression, then post-training quantised to symmetric 4-bit integers
//              (scale = max|w| / 7): w = 0,+6,+1,-7,0,-6,+1,+7,0, bias = -4 (same integer units, so no scale multiply is needed).
//   Nothing in the RTL knows these numbers: they come from the generated ROM. Other labels -> other ROM -> same RTL.
//
// WHY THIS IS AI
//   The decision comes from learned parameters, not from hand-written pixel tests: a generic "multiply, accumulate,
//   threshold" engine. Changing the data changes the function; the structure stays a single neuron.
//
// NETWORK <-> HARDWARE
//   weight memory  -> prec_int4_rom (9 weights + bias, constants; synthesis folds them into logic, so the "memory"
//                     is really a small combinational lookup, not an SRAM)
//   multiply       -> ONE signed 4 x 5 bit multiplier (signed weight x pixel zero-extended to 5 bits, so 15 stays +15)
//   accumulate     -> ONE 12-bit signed adder + the accumulator register acc, reused serially over the 9 inputs
//   activation     -> the sign bit: class = ~acc[11]
//   weight reuse   -> one MAC used 9 times, one step per clock, overlapped with the arrival of the next pixel
//
// WHAT int4 COSTS (versus the other formats)
//   multiplier : 4 x 4 (unsigned pixel) significant bits; accumulator: 12 bits signed (sized for ANY ROM code:
//   |sum| <= 1891 for bias and products at their limits); bias 11 bits. A narrower weight means a smaller multiplier
//   and adder; a wider one buys fidelity to fp32 (see spec.md section 9). Honest caveat: because the weights are
//   constants, synthesis folds them into the multiplier logic, so the real area differs from the nominal width.
//
// BLOCKS: IO (stream handshake) / MEMORY (weight ROM, input stage register) / COMPUTE (the MAC, acc) / CONTROL (FSM).
// Stream in : 9 beats, pixel in s_data[3:0], s_last on the 9th. s_data[7:4] != 0 -> error, item unused.
// Stream out: 2 beats; beat 0 = {6'b0, error, class}; beat 1 = XOR-fold of the raw accumulator bytes (m_last).
// Latency   : 2 (spec.md section 6). Frame errors: wrong beat count, bad pixel; result is still produced.
`timescale 1ns/1ps
`default_nettype none
module prec_int4 (
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
    localparam [3:0] N        = 4'd9;     // pixels per frame
    localparam [1:0] ST_LOAD  = 2'd0;
    localparam [1:0] ST_DRAIN = 2'd1;     // the edge that performs the MAC of the last item
    localparam [1:0] ST_OUT0  = 2'd2;
    localparam [1:0] ST_OUT1  = 2'd3;

    // ---- registers ----
    reg  [1:0]  state;                    // CONTROL: FSM LOAD / DRAIN / OUT0 / OUT1
    reg  [3:0]  count;                    // CONTROL: beats accepted in this frame, saturates at 9; also the pixel index
    reg         error;                    // CONTROL: sticky flag (bad item or wrong frame length), cleared after output
    reg         x_vld;                    // MEMORY: stage register valid (a used item waits for its MAC step)
    reg  [3:0]  x_pix;                    // MEMORY: stage register, the pixel (unsigned 0..15)
    reg  [3:0]  x_idx;                    // MEMORY: stage register, the pixel index = ROM address
    reg  signed [11:0] acc;                // COMPUTE: the accumulator, 12-bit two's complement

    // ---- IO: handshake strobes ----
    wire beat_in  = s_valid & s_ready;
    wire beat_out = m_valid & m_ready;
    // ---- CONTROL: validity checks ----
    wire in_frame = (count < N);
    wire bad_item = |s_data[7:4];

    // ---- MEMORY: weight ROM (generated; weight selected by the staged index) ----
    wire signed [3:0]  weight;
    wire signed [10:0] bias;
    prec_int4_rom rom (.addr(x_idx), .weight(weight), .bias(bias));

    // ---- COMPUTE: the one MAC. Pixel is zero-extended to 5 bits BEFORE the signed multiply ($signed(4'hF) would be -1).
    wire signed [4:0]       pix_s   = {1'b0, x_pix};
    wire signed [8:0]  product = weight * pix_s;            // exact, 9 bits signed
    wire signed [11:0]  acc_nxt = acc + {{3{product[8]}}, product};   // sign-extended product, exact sum
    // bias pre-loaded into acc at reset / after output, so "bias first" costs no cycle (the ROM bias is address-independent)
    wire signed [11:0]  bias_x  = {{1{bias[10]}}, bias};

    // ---- IO: outputs ----
    wire       cls   = ~acc[11];                              // sum >= 0
    wire [7:0] fold8 = acc[7:0] ^ {4'b0, acc[11:8]};
    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);
    assign m_last  = (state == ST_OUT1);
    assign m_data  = (state == ST_OUT1) ? fold8 : {6'b0, error, cls};

    // ---- CONTROL + COMPUTE: sequential update ----
    always @(posedge clk) begin
        if (rst) begin
            state <= ST_LOAD;
            count <= 4'd0;
            error <= 1'b0;
            x_vld <= 1'b0;
            x_pix <= 4'd0;
            x_idx <= 4'd0;
            acc   <= bias_x;
        end else begin
            // COMPUTE: one MAC step per clock when a used item is staged
            if (x_vld) acc <= acc_nxt;
            // stage the incoming item (ROM address = its position in the frame)
            x_vld <= beat_in & in_frame & ~bad_item;
            x_pix <= s_data[3:0];
            x_idx <= count;
            case (state)
                ST_LOAD: if (beat_in) begin
                    if (in_frame) count <= count + 4'd1;     // saturating
                    if (bad_item | ~in_frame | (s_last & (count != N - 4'd1))) error <= 1'b1;
                    if (s_last) state <= ST_DRAIN;
                end
                ST_DRAIN: state <= ST_OUT0;
                ST_OUT0: if (beat_out) state <= ST_OUT1;
                default: if (beat_out) begin                 // ST_OUT1
                    state <= ST_LOAD;
                    count <= 4'd0;
                    error <= 1'b0;
                    acc   <= bias_x;
                end
            endcase
        end
    end
endmodule
`default_nettype wire
