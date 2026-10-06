// SPDX-License-Identifier: Apache-2.0
// kv_attn_core -- one attention head with a KV cache in registers: the two phases of LLM inference in miniature.
// Shared by the five kv_attn_<v> engines; each thin top instantiates this core with its own parameters and ROM.
// Contract: model/kv_attention/spec.md (bit- and cycle-exact against model/kv_attention/golden.py).
//
//   N     cache entries (4, 8, 16; a power of two)        KVB  bits per stored K/V component (8, or 4 = int4 cache)
//   RING  0: a full cache answers CACHE_FULL               1: the oldest entry is overwritten (sliding window)
//
// LLM MAPPING
//   prefill (opcode 02)  streaming cache writes: one prompt token per cycle is embedded, projected to k and v and
//                        written to slot wp. No scoring: the cost per token does not depend on the cache length.
//   decode  (opcode 03)  q = Wq x + bq for the new token, then a SERIAL scan over the cache: ONE dot-product unit
//                        (4 MACs) scores one slot per cycle, an argmax tracker keeps the best (strictly greater
//                        replaces: the lowest slot wins a tie). Cost = n + 3 cycles, growing with the cache length n.
//                        The best entry's V is the output (hard attention: winner takes all), then k, v are appended.
//   KV cache             N x 2 x 4 x KVB bits of flip-flops (kc, vc): the dominant state of the design.
//   position embedding   pos = tokens cached since reset (saturating at 31) is added to x[3]; it is not stored.
//
// BLOCKS
//   IO      : s_* beat register (din), response shift register (resp) with m_* handshake.
//   MEMORY  : kc / vc cache arrays, write pointer wp, entry count, position counter pos.
//   COMPUTE : embed + pos, q/k/v projections from the ROM constants (compute once per token), quantiser for int4,
//             the dot-product unit, the argmax tracker (best_s, best_i).
//   CONTROL : FSM S_RECV (beats in, validated one cycle after acceptance) / S_SCAN / S_RESP (response beats out).
//
// Timing (spec.md section 6; E1 = edge accepting the last input beat): every response except a successful DECODE is
// loaded at E2 (L = 2); a successful DECODE scans slot j at E(3+j) and loads its response at E(n+3) (L = n + 3).
// din is only overwritten by an accepted beat, so for a DECODE the token stays in din during the whole scan and
// the append at the end recomputes k and v from din and pos (no k/v registers).
`timescale 1ns/1ps
`default_nettype none
module kv_attn_core #(
    parameter integer N    = 8,
    parameter integer KVB  = 8,
    parameter integer RING = 0
) (
    // ---- IO: clock, reset, and the valid/ready stream handshake ----
    input  wire         clk,
    input  wire         rst,        // synchronous, active high
    input  wire         s_valid,
    input  wire [7:0]   s_data,
    input  wire         s_last,
    output wire         s_ready,
    output wire         m_valid,
    output wire [7:0]   m_data,
    output wire         m_last,
    input  wire         m_ready,
    // ---- parameter ROM (constants; kv_attn_<v>_rom) ----
    output wire [3:0]   rom_token,
    input  wire [31:0]  rom_emb,
    input  wire [127:0] rom_wq,
    input  wire [127:0] rom_wk,
    input  wire [127:0] rom_wv,
    input  wire [31:0]  rom_bq,
    input  wire [31:0]  rom_bk,
    input  wire [31:0]  rom_bv
);
    localparam integer IW = $clog2(N);              // slot index width
    localparam [1:0] S_RECV = 2'd0, S_SCAN = 2'd1, S_RESP = 2'd2;
    localparam [3:0] E_OK = 4'd0, E_OPCODE = 4'd1, E_TOKEN = 4'd2, E_FULL = 4'd3, E_FRAME = 4'd4;

    // ------------------------------------------------------------------ MEMORY
    reg [4*KVB-1:0] kc [0:N-1];                     // keys,   component i at [KVB*i +: KVB]
    reg [4*KVB-1:0] vc [0:N-1];                     // values
    reg [IW-1:0]    wp;                             // next slot to write
    reg [IW:0]      count;                          // valid entries 0..N
    reg [4:0]       pos;                            // tokens cached since reset, saturating at 31

    // ------------------------------------------------------------------ IO / CONTROL registers
    reg [1:0]  st;
    reg        busy;                                // set at E1, cleared by the last response beat taken
    reg [7:0]  din;                                 // last accepted beat
    reg        din_v, din_last;                     // din is new / is the last beat of its frame
    reg [1:0]  bcnt;                                // beats processed so far in this frame (saturates at 2)
    reg [7:0]  op;                                  // opcode (beat 0)
    reg [3:0]  err;                                 // PREFILL: first error of the frame
    reg        mv;                                  // m_valid
    reg [63:0] resp;                                // response beats, beat 0 in [7:0], shifted right as beats are taken
    reg [2:0]  rrem;                                // beats remaining after the current one
    reg [IW:0] jc;                                  // scan slot counter
    reg signed [7:0] best_s;                        // argmax tracker
    reg [IW-1:0]     best_i;
    reg [31:0] q_r;                                 // q of the DECODE token, registered at E2

    assign s_ready   = ~busy;
    assign m_valid   = mv;
    assign m_data    = resp[7:0];
    assign m_last    = (rrem == 3'd0);
    assign rom_token = din[3:0];

    // ------------------------------------------------------------------ COMPUTE: projections
    // x = emb + (0,0,0,pos); out_r = sum_c W[r][c] x[c] + b[r], int8 (the golden asserts the range). The ROM outputs
    // are constants, so synthesis folds the multiplies (entries 0, 1, 4) into wires and shifts.
    function [31:0] matvec(input [127:0] w, input [31:0] x, input [31:0] b);
        integer r, c;
        reg signed [15:0] acc, wv, xv;
        begin
            matvec = 32'd0;
            for (r = 0; r < 4; r = r + 1) begin
                acc = {{8{b[8*r+7]}}, b[8*r +: 8]};
                for (c = 0; c < 4; c = c + 1) begin
                    wv  = {{8{w[8*(4*r+c)+7]}}, w[8*(4*r+c) +: 8]};
                    xv  = {{8{x[8*c+7]}}, x[8*c +: 8]};
                    acc = acc + wv * xv;
                end
                matvec[8*r +: 8] = acc[7:0];
            end
        end
    endfunction

    // storage quantisers: int8 as is; int4 K = clamp((k + 2) >> 2, -8, 7) (scale 4), int4 V = clamp(v, -8, 7)
    function [KVB-1:0] quant_k(input [7:0] k);
        reg signed [9:0] t;
        begin
            if (KVB == 8) quant_k = k;
            else begin
                t = {{2{k[7]}}, k} + 10'sd2;
                t = t >>> 2;
                if (t > 10'sd7)       quant_k = 7;
                else if (t < -10'sd8) quant_k = -8;
                else                  quant_k = t[KVB-1:0];
            end
        end
    endfunction
    function [KVB-1:0] quant_v(input [7:0] v);
        reg signed [7:0] s;
        begin
            s = v;
            if (KVB == 8) quant_v = v;
            else if (s > 8'sd7)       quant_v = 7;
            else if (s < -8'sd8)      quant_v = -8;
            else                      quant_v = v[KVB-1:0];
        end
    endfunction

    wire [31:0] x_w = {rom_emb[31:24] + {3'b000, pos}, rom_emb[23:0]};
    wire [31:0] q_w = matvec(rom_wq, x_w, rom_bq);
    wire [31:0] k_w = matvec(rom_wk, x_w, rom_bk);
    wire [31:0] v_w = matvec(rom_wv, x_w, rom_bv);

    reg [4*KVB-1:0] kq_w, vq_w;                     // quantised k and v of din at pos
    integer qi;
    always @* begin
        kq_w = {4*KVB{1'b0}};
        vq_w = {4*KVB{1'b0}};
        for (qi = 0; qi < 4; qi = qi + 1) begin
            kq_w[KVB*qi +: KVB] = quant_k(k_w[8*qi +: 8]);
            vq_w[KVB*qi +: KVB] = quant_v(v_w[8*qi +: 8]);
        end
    end

    // ---- the single dot-product unit: q . khat[jc]; int4 keys are dequantised with a shift by 2
    wire [4*KVB-1:0] k_sel = kc[jc[IW-1:0]];
    reg  signed [15:0] dot;
    integer di;
    reg signed [15:0] qv, kv;
    always @* begin
        dot = 16'sd0;
        for (di = 0; di < 4; di = di + 1) begin
            qv = {{8{q_r[8*di+7]}}, q_r[8*di +: 8]};
            if (KVB == 8) kv = {{8{k_sel[KVB*di+KVB-1]}}, k_sel[KVB*di +: KVB]};
            else          kv = {{10{k_sel[KVB*di+KVB-1]}}, k_sel[KVB*di +: KVB], 2'b00};
            dot = dot + qv * kv;
        end
    end
    wire signed [7:0] score = dot[7:0];
    wire              upd   = (jc == {(IW+1){1'b0}}) || (score > best_s);

    // ------------------------------------------------------------------ CONTROL: frame decoding (one cycle after accept)
    wire        proc    = din_v;
    wire        first_b = (bcnt == 2'd0);
    wire [7:0]  op_e    = first_b ? din : op;
    wire        op_rst  = (op_e == 8'd1);
    wire        op_pre  = (op_e == 8'd2);
    wire        op_dec  = (op_e == 8'd3);
    wire        tok_ok  = (din[7:4] == 4'd0);
    wire        full    = (RING == 0) && (count == N[IW:0]);
    wire        pre_tok = proc && !first_b && op_pre && (err == E_OK);
    wire        pre_wr  = pre_tok && tok_ok && !full;
    wire [3:0]  err_n   = (pre_tok && !tok_ok) ? E_TOKEN : (pre_tok && full) ? E_FULL : err;
    wire        cnt_max = (count == N[IW:0]);
    wire [IW:0] cnt_inc = cnt_max ? count : count + 1'b1;
    wire        dec_ok  = proc && din_last && op_dec && (bcnt == 2'd1) && tok_ok && !full;
    wire        scan_done = (st == S_SCAN) && (jc == count);
    wire        we      = pre_wr || scan_done;
    wire [IW:0] count_n = we ? cnt_inc : count;
    wire        hit     = (count != {(IW+1){1'b0}}) && !best_s[7] && (best_s[6] || best_s[5]);
    wire [4*KVB-1:0] v_sel = vc[best_i];

    // response bytes of a successful DECODE
    reg [63:0] dec_resp;
    integer ri;
    always @* begin
        dec_resp = 64'd0;
        if (count != {(IW+1){1'b0}}) begin
            dec_resp[7:0]   = {3'b000, hit, E_OK};
            dec_resp[15:8]  = {{(7-IW){1'b0}}, count_n};
            dec_resp[23:16] = {{(8-IW){1'b0}}, best_i};
            dec_resp[31:24] = best_s;
            for (ri = 0; ri < 4; ri = ri + 1)
                dec_resp[32+8*ri +: 8] = {{(8-KVB){v_sel[KVB*ri+KVB-1]}}, v_sel[KVB*ri +: KVB]};
        end else begin
            dec_resp[7:0]   = {4'd0, E_OK};
            dec_resp[15:8]  = {{(7-IW){1'b0}}, count_n};
            dec_resp[23:16] = 8'hFF;
        end
    end

    // short response of the E2 edge: {count, status}; count after the command
    reg [3:0] code;
    always @* begin
        code = E_OK;
        if (!(op_rst || op_pre || op_dec))   code = E_OPCODE;
        else if (op_rst)                     code = first_b ? E_OK : E_FRAME;
        else if (op_pre)                     code = err_n;
        else if (first_b || bcnt == 2'd2)    code = E_FRAME;
        else if (!tok_ok)                    code = E_TOKEN;
        else if (full)                       code = E_FULL;
    end
    wire [IW:0] cnt_o = op_pre ? count_n : (op_rst && first_b) ? {(IW+1){1'b0}} : count;

    wire s_take = s_valid && !busy;
    wire m_take = mv && m_ready;

    always @(posedge clk) begin
        if (rst) begin
            st <= S_RECV; busy <= 1'b0; din_v <= 1'b0; din_last <= 1'b0; bcnt <= 2'd0; err <= E_OK;
            mv <= 1'b0; count <= {(IW+1){1'b0}}; wp <= {IW{1'b0}}; pos <= 5'd0; rrem <= 3'd0;
            jc <= {(IW+1){1'b0}};                   // not X after power-up: gate-level X-pessimism otherwise keeps stray bits unknown
        end else begin
            // ---- IO: accept a beat
            din_v <= s_take;
            if (s_take) begin
                din      <= s_data;
                din_last <= s_last;
                if (s_last) busy <= 1'b1;
            end
            // ---- response beats out
            if (m_take) begin
                resp <= {8'd0, resp[63:8]};
                if (rrem == 3'd0) begin
                    mv <= 1'b0; busy <= 1'b0; st <= S_RECV;
                end else rrem <= rrem - 3'd1;
            end
            // ---- frame processing, one cycle after the beat was accepted
            if (proc) begin
                if (first_b) op <= din;
                bcnt <= (bcnt == 2'd2) ? 2'd2 : bcnt + 2'd1;
                if (pre_tok) err <= err_n;
                if (din_last) begin
                    bcnt <= 2'd0; err <= E_OK;
                    if (dec_ok) begin
                        st <= S_SCAN; jc <= {(IW+1){1'b0}}; q_r <= q_w;
                    end else begin
                        st <= S_RESP; mv <= 1'b1; rrem <= 3'd1;
                        resp <= {48'd0, {(7-IW){1'b0}}, cnt_o, 4'd0, code};
                    end
                    if (op_rst && first_b) begin
                        count <= {(IW+1){1'b0}}; wp <= {IW{1'b0}}; pos <= 5'd0;
                    end
                end
            end
            // ---- the serial scan: slot jc at each edge, response when jc == n
            if (st == S_SCAN) begin
                if (jc != count) begin
                    jc <= jc + 1'b1;
                    if (upd) begin best_s <= score; best_i <= jc[IW-1:0]; end
                end else begin
                    st <= S_RESP; mv <= 1'b1; rrem <= 3'd7; resp <= dec_resp;
                end
            end
            // ---- cache write (prefill token, or the DECODE append) and counters
            if (we) begin
                kc[wp] <= kq_w;
                vc[wp] <= vq_w;
                wp     <= wp + 1'b1;
                count  <= cnt_inc;
                pos    <= (pos == 5'd31) ? pos : pos + 5'd1;
            end
        end
    end
endmodule
`default_nettype wire
