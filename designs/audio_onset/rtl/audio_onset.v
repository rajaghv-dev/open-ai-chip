// SPDX-License-Identifier: Apache-2.0
// audio_onset -- a STREAMING NEURAL NETWORK ENGINE: one neuron over a sliding window of the last four 4-bit window
// energies of an audio signal; it says, after every new sample, "did the sound just get louder?" (an onset).
// (Spec: model/audio_onset/spec.json; docs/WHY_AI.md section 6; ../open-ai-silicon/docs/ARCH_STUDY_PLAN.md 4.4.)
//
// THE NETWORK (one neuron, four weights, a threshold)
//   inputs      : e[n-3] (oldest), e[n-2], e[n-1], e[n] (newest): four unsigned 4-bit energies, one new one per beat.
//   weights     : w0..w3, signed 3 bit, from audio_onset_rom.v.
//   weighted sum: sum = w0*e[n-3] + w1*e[n-2] + w2*e[n-1] + w3*e[n]   (6-bit two's complement, no overflow)
//   activation  : threshold step, class = (sum >= threshold)
//   output      : {error, class, sum} for every sample once four have been seen.
//
// WHY THIS IS AI, NOT A HAND-WRITTEN RULE
//   model/audio_onset/train.py searched all 625 integer weight vectors in -2..2 and every threshold on labelled
//   windows (label: newest two minus oldest two >= 4, with 5% of the labels flipped) and found, unprompted,
//   w = [-1, -1, +1, +1] and threshold 4: "recent energy minus older energy". The ROM generated from that result
//   is the only place those numbers live. This RTL is a generic "window, weighted sum, threshold" engine: train
//   other labels (a falling-edge detector, a slow trend) and the SAME RTL computes a different function.
//
// NEURAL NETWORK <-> HARDWARE
//   input window  -> d0, d1, d2 (3 x 4-bit delay line) plus the live s_data[3:0]
//   weights       -> audio_onset_rom (w0..w3, threshold; constants)
//   multiply      -> none: each product w*e is (w[0]?e:0) + (w[1]?2e:0) - (w[2]?4e:0), shifts and adds of the
//                    two's-complement bits of w. The weights are constants, so synthesis folds the selects away
//                    and leaves (for [-1,-1,+1,+1]) just a four-input add/subtract tree.
//   accumulate    -> one 6-bit sum of the four products
//   activation    -> signed comparator sum >= threshold
//
// BIT-WIDTH LESSON (vs audio_pitch, 1-bit samples; model/examples/audio.py)
//   audio_pitch stores a 1-bit sample: 3 bits of history and a 2-bit count. audio_onset stores 4-bit energies: 12
//   bits of history (4x), and the sum needs 6 signed bits instead of 2 unsigned. Same window length, same one
//   neuron; precision alone multiplies both storage and arithmetic. Hence the sample width is the first knob to
//   shrink in a real audio chip.
//
// SUM RANGE (why 6 bits are enough): energies 0..15, weights [-1,-1,+1,+1] give 15*(-2) .. 15*(+2) = -30..30,
//   inside -32..31. Two's complement arithmetic is modular, so every intermediate value (including a lone product
//   such as 15*(-4) = -60) may wrap and the 6-bit result is still exact as long as the final sum fits. gen_rom.py
//   computes the exact range for the trained weights and refuses to emit a ROM whose range does not fit 6 bits.
//
// BLOCKS (the four constructs of ARCH_STUDY_PLAN.md section 2)
//   IO      : s_* input beats, m_* output beats, one output register (valid/ready, full throughput).
//   MEMORY  : d0..d2 delay line (12 flops) and the weight/threshold ROM (constants).
//   COMPUTE : shift-add weighted sum, comparator.
//   CONTROL : warm-up counter, sticky error flag, output handshake.
//
// STREAM CONTRACT
//   Stream in : one beat per sample, energy in s_data[3:0], s_data[7:4] must be 0; s_last marks the final sample of a
//               stream and clears the history (the next stream warms up again).
//   Stream out: after a warm-up of 3 samples, one beat per input beat, 1 cycle later:
//               m_data = {error[7], class[6], sum[5:0]}, m_last = s_last of the input beat that produced it.
//               A stream shorter than 4 samples produces no output (and so cannot report an error).
//   error     : s_data[7:4] != 0 in this or an earlier beat of the stream (sticky until s_last). The low nibble of such
//               a beat is still used as the energy.
//   Handshake : s_ready = !m_valid | m_ready; a waiting output is held (m_valid, m_data, m_last stable) until taken.
`timescale 1ns/1ps
`default_nettype none
module audio_onset (
    // ---- IO: clock, reset, and the valid/ready stream handshake ----
    input  wire       clk,
    input  wire       rst,        // synchronous, active high
    input  wire       s_valid,
    input  wire [7:0] s_data,     // [3:0] window energy, [7:4] must be 0
    input  wire       s_last,
    output wire       s_ready,
    output wire       m_valid,
    output wire [7:0] m_data,     // {error, class, sum[5:0]}
    output wire       m_last,
    input  wire       m_ready
);
    // ---- CONTROL: constant ----
    localparam [1:0] WARM = 2'd3;   // samples needed before a window of four exists

    // ---- MEMORY: registers ----
    reg        [3:0] d0;          // energy 3 samples ago (oldest of the window); no reset: gated by warm-up
    reg        [3:0] d1;          // energy 2 samples ago; no reset: gated by warm-up
    reg        [3:0] d2;          // energy 1 sample ago; no reset: gated by warm-up
    // ---- CONTROL: registers ----
    reg        [1:0] count;       // samples seen since reset or s_last, saturates at 3 (= warmed up)
    reg              err_q;       // sticky error flag of the current stream
    // ---- IO: output register (one result waiting for the consumer) ----
    reg              out_valid;   // a result is waiting
    reg              out_err;     // result bit 7: error
    reg              out_cls;     // result bit 6: class
    reg        [5:0] out_sum;     // result bits 5:0: signed sum
    reg              out_last;    // result m_last; out_* data need no reset: only looked at while out_valid

    // ---- IO: handshake strobes ----
    assign s_ready = ~out_valid | m_ready;
    wire   beat_in = s_valid & s_ready;
    assign m_valid = out_valid;
    assign m_data  = {out_err, out_cls, out_sum};
    assign m_last  = out_last;

    // ---- MEMORY: the learned parameters (constants from the generated ROM) ----
    wire signed [2:0] w0, w1, w2, w3;
    wire signed [5:0] threshold;
    audio_onset_rom rom (.w0(w0), .w1(w1), .w2(w2), .w3(w3), .threshold(threshold));

    // ---- COMPUTE: weight * energy without a multiplier ----
    // w = -4*w[2] + 2*w[1] + w[0] (two's complement), so w*e = (w[0]?e:0) + (w[1]?e<<1:0) - (w[2]?e<<2:0).
    // All arithmetic is modulo 64: exact for the final sum, see SUM RANGE above.
    function automatic [5:0] mul_w(input [3:0] e, input [2:0] w);
        reg [5:0] x;
        begin
            x     = {2'b00, e};
            mul_w = (w[0] ? x : 6'd0) + (w[1] ? (x << 1) : 6'd0) - (w[2] ? (x << 2) : 6'd0);
        end
    endfunction

    wire [3:0] e_new   = s_data[3:0];                 // newest energy of the window
    wire       bad_in  = |s_data[7:4];                // input contract violated
    wire [5:0] sum     = mul_w(d0, w0) + mul_w(d1, w1) + mul_w(d2, w2) + mul_w(e_new, w3);
    wire       cls     = ($signed(sum) >= threshold); // activation: threshold step on the signed sum
    wire       err_now = err_q | bad_in;              // error including this beat
    wire       warmed  = (count == WARM);             // three earlier samples exist: this beat completes a window

    // ---- CONTROL + MEMORY + IO: sequential update ----
    always @(posedge clk) begin
        if (rst) begin
            count     <= 2'd0;
            err_q     <= 1'b0;
            out_valid <= 1'b0;
        end else begin
            if (m_valid & m_ready) out_valid <= 1'b0;       // consumer took the waiting result
            if (beat_in) begin
                if (s_last) begin
                    count <= 2'd0;                          // end of stream: warm up again (history is ignored)
                    err_q <= 1'b0;
                end else begin
                    if (~warmed) count <= count + 2'd1;     // saturates at 3
                    err_q <= err_now;
                end
                if (warmed) out_valid <= 1'b1;              // wins over the clear above: new result for this beat
            end
        end
        if (beat_in & ~rst) begin
            d0 <= d1; d1 <= d2; d2 <= e_new;                // shift the delay line
            if (warmed) begin
                out_err  <= err_now;
                out_cls  <= cls;
                out_sum  <= sum;
                out_last <= s_last;
            end
        end
    end
endmodule
`default_nettype wire
