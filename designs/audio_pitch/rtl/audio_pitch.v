// SPDX-License-Identifier: Apache-2.0
// audio_pitch -- a STREAMING NEURAL NETWORK INFERENCE ENGINE: is the last W = 8 one-bit audio samples a high tone?
// (Spec: model/audio_pitch/spec.json. Background: docs/WHY_AI.md section 6 Audio.)
//
// THE NETWORK (over TIME, not over space)
//   input      : one sample per beat, s_data[0] = sign bit of the waveform (s_data[7:1] must be 0, else error).
//   kernel     : [+1, -1] slid along time. On a one-bit signal |s[t] - s[t-1]| = s[t] XOR s[t-1] = 1 exactly at a
//                sign change, so the convolution output is a "change" bit per sample.
//   pooling    : the number of changes among the last W samples (W-1 change bits): a running count.
//   activation : threshold step, class = (count >= threshold). A high tone changes sign more often than a low one.
//   output     : {error, class, count[5:0]} per input beat (count shown so a reader sees how close the call was).
//
// THE THRESHOLD IS LEARNED, NOT WRITTEN
//   model/audio_pitch/train.py fits it from generated labelled data (square waves of short and long period at every
//   phase, plus single-bit noise) and emits audio_pitch_rom.v (generated, do not edit). It found threshold 4. At
//   W = 8 that is NOT a perfect rule (training accuracy 85.5%): a high tone of half-period 3 shows only 2 or 3
//   changes in 8 samples, which a noisy low tone can match. A longer window separates them better, at the price of
//   a longer delay line. The RTL holds no such number: it is a generic "count changes, compare with ROM value".
//
// WHY STREAMING STATE INSTEAD OF A FRAME BUFFER (compare vision_block)
//   vision_block gets a frame, stores it, computes, answers once, and starts over. Audio never stops, so there is
//   no frame. The engine keeps only what the model needs from the past: the last sample, the last W-1 change bits
//   (the delay line) and the count. Memory is set by the WINDOW, not by the stream length. One result comes out for
//   every input after the warm-up, by an incremental update: count_new = count + change entering - change leaving.
//
// NEURAL NETWORK <-> HARDWARE
//   kernel [+1,-1]        -> XOR of the new sample and the previous sample          [chg]
//   window of W-1 changes -> 7-bit shift register (delay line)                      [line]
//   running count         -> 3-bit register, + entering change - leaving change     [count]
//   learned threshold     -> audio_pitch_rom                                        [threshold]
//   activation            -> comparator on the registered count (output side)       [cls]
//
// BLOCKS (the four constructs of ../open-ai-silicon/docs/ARCH_STUDY_PLAN.md section 2)
//   IO      : s_* sample beats in, m_* result beats out, one result register, error flag.
//   MEMORY  : prev (last sample) and line (delay line of the last W-1 changes), plus the ROM threshold.
//   COMPUTE : change detector, running count, threshold compare.
//   CONTROL : warm-up counter `seen`, s_last clears the history; the handshake FSM is the single bit m_valid_r.
//
// Stream in : one sample per beat, any gaps. s_last marks the last sample of a recording and starts a new one
//             after it (history cleared).
// Stream out: after the first W-1 = 7 samples of a recording (warm-up, no output), ONE result beat per input beat:
//             m_data = {error, class, count[5:0]}; m_last = s_last of that input beat. A recording shorter than W
//             produces no result beat. The result is registered: m_valid rises on the edge that accepts the sample.
// error     : a sample with s_data[7:1] != 0 (its bit 0 is still used). The flag rides on the next result beat (this
//             beat if warm) and then clears; if the recording ends during warm-up it is dropped with the history.
`timescale 1ns/1ps
`default_nettype none
module audio_pitch (
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
    localparam [2:0] WARM = 3'd7;     // W - 1: samples that must be seen before the first result (W = 8)

    // ---- MEMORY: stream history (21 flip-flops in total with the registers below) ----
    reg        prev;                  // the previous sample of this recording (1 bit)
    reg  [6:0] line;                  // delay line: the last W-1 = 7 change bits; line[0] newest, line[6] oldest
    // ---- COMPUTE: running count ----
    reg  [2:0] count;                 // sign changes among the last W samples = sum of line (0..7)
    // ---- CONTROL: warm-up and error ----
    reg  [2:0] seen;                  // samples of this recording seen, saturates at WARM (counts the warm-up)
    reg        err_pend;              // a bad sample was seen since the last result beat
    // ---- IO: result register (one beat) ----
    reg        m_valid_r;             // the result register holds a beat nobody has taken yet
    reg        m_last_r;              // its m_last
    reg        m_err_r;               // its error flag
    reg  [2:0] m_cnt_r;               // its change count (the class is derived from it on the way out)

    // ---- IO: handshake strobes. s_ready needs no combinational path from s_valid. ----
    assign s_ready = ~m_valid_r | m_ready;
    wire       beat_in  = s_valid & s_ready;
    wire       beat_out = m_valid_r & m_ready;

    // ---- MEMORY: ROM threshold (learned) ----
    wire [3:0] threshold;
    audio_pitch_rom rom (.threshold(threshold));

    // ---- COMPUTE: change detector, the [+1,-1] kernel on one-bit samples ----
    wire       bad_item  = |s_data[7:1];
    wire       sample    = s_data[0];
    wire       chg       = (seen != 3'd0) & (sample ^ prev);      // no previous sample at the start of a recording
    // ---- COMPUTE: running count: add the change entering, subtract the change leaving (line[6]) ----
    wire [3:0] count_nxt = {1'b0, count} + {3'b000, chg} - {3'b000, line[6]};
    // ---- CONTROL: a result beat is produced when this is at least the W-th sample of the recording ----
    wire       warm      = (seen == WARM);
    wire       emit      = beat_in & warm;
    wire       err_now   = err_pend | bad_item;
    // ---- COMPUTE: activation on the registered count ----
    wire       cls       = ({1'b0, m_cnt_r} >= threshold);

    // ---- IO: stream outputs ----
    assign m_valid = m_valid_r;
    assign m_last  = m_last_r;
    assign m_data  = {m_err_r, cls, 3'b000, m_cnt_r};

    // ---- CONTROL + MEMORY + COMPUTE: sequential update ----
    always @(posedge clk) begin
        if (rst) begin
            prev      <= 1'b0;
            line      <= 7'd0;
            count     <= 3'd0;
            seen      <= 3'd0;
            err_pend  <= 1'b0;
            m_valid_r <= 1'b0;
            m_last_r  <= 1'b0;
            m_err_r   <= 1'b0;
            m_cnt_r   <= 3'd0;
        end else begin
            if (beat_out) m_valid_r <= 1'b0;
            if (emit) begin                    // result for the window ending at this sample
                m_valid_r <= 1'b1;
                m_last_r  <= s_last;
                m_err_r   <= err_now;
                m_cnt_r   <= count_nxt[2:0];
            end
            if (beat_in) begin
                if (s_last) begin              // end of recording: forget the history
                    prev     <= 1'b0;
                    line     <= 7'd0;
                    count    <= 3'd0;
                    seen     <= 3'd0;
                    err_pend <= 1'b0;
                end else begin
                    prev     <= sample;
                    line     <= {line[5:0], chg};
                    count    <= count_nxt[2:0];
                    if (!warm) seen <= seen + 3'd1;
                    err_pend <= err_now & ~emit;
                end
            end
        end
    end
endmodule
`default_nettype wire
