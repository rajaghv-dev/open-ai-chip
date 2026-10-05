// SPDX-License-Identifier: Apache-2.0
// vision_block -- one 2x2 binary convolution neuron reused at the four positions of a 3x3 one-bit image, with OR
// max-pooling (model/tiny_ai/spec.json).
//
// Stream in : 9 beats, pixel in s_data[0] (raster order), s_last on the 9th. Stored in a 9-bit frame register.
// Stream out: 2 beats. Beat 0 m_data = {6'b0, error, class}; beat 1 m_data = score (largest window match count,
//             0..4), m_last on beat 1.
// Compute   : ONE neuron (XNOR with the kernel, count, compare with the threshold) evaluates one window per cycle,
//             windows (0,0), (0,1), (1,0), (1,1); class = OR of the four results, score = max of the four counts.
//             Kernel and threshold come from vision_block_rom.v (generated). Result: 5 cycles after the last beat.
// error     : a beat with s_data > 1 (pixel left 0), or a frame that is not exactly 9 beats (beats past 9 are
//             ignored; missing pixels are 0).
`timescale 1ns/1ps
`default_nettype none
module vision_block (
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
    localparam [3:0] N       = 4'd9;
    localparam [1:0] ST_LOAD = 2'd0;
    localparam [1:0] ST_COMP = 2'd1;
    localparam [1:0] ST_OUT0 = 2'd2;
    localparam [1:0] ST_OUT1 = 2'd3;

    reg  [1:0] state;
    reg  [3:0] count;              // beats received in this frame, saturates at N
    reg  [8:0] frame;              // pixels, raster order
    reg  [1:0] win;                // window being evaluated
    reg        pooled;             // OR of the window results so far
    reg  [2:0] best;               // largest window match count so far
    reg        error;

    wire       beat_in  = s_valid & s_ready;
    wire       beat_out = m_valid & m_ready;
    wire       in_frame = (count < N);
    wire       bad_item = |s_data[7:1];

    // data movement: select the current 2x2 window (bit 0 TL, 1 TR, 2 BL, 3 BR)
    reg  [3:0] window;
    always @(*) begin
        case (win)
            2'd0:    window = {frame[4], frame[3], frame[1], frame[0]};
            2'd1:    window = {frame[5], frame[4], frame[2], frame[1]};
            2'd2:    window = {frame[7], frame[6], frame[4], frame[3]};
            default: window = {frame[8], frame[7], frame[5], frame[4]};
        endcase
    end

    // the one neuron
    wire [3:0] kernel;
    wire [2:0] threshold;
    vision_block_rom rom (.kernel(kernel), .threshold(threshold));
    wire [3:0] match = ~(window ^ kernel);
    wire [2:0] mcount = {2'b0, match[0]} + {2'b0, match[1]} + {2'b0, match[2]} + {2'b0, match[3]};
    wire       fire   = (mcount >= threshold);

    assign s_ready = (state == ST_LOAD);
    assign m_valid = (state == ST_OUT0) | (state == ST_OUT1);
    assign m_last  = (state == ST_OUT1);
    assign m_data  = (state == ST_OUT1) ? {5'b0, best} : {6'b0, error, pooled};

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
                ST_LOAD: if (beat_in) begin
                    if (in_frame)             count <= count + 4'd1;
                    if (in_frame & ~bad_item) frame[count] <= s_data[0];
                    if (bad_item | ~in_frame | (s_last & (count != N - 4'd1))) error <= 1'b1;
                    if (s_last) begin
                        state <= ST_COMP;
                        win   <= 2'd0;
                    end
                end
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
