// SPDX-License-Identifier: Apache-2.0
// tiny_ai_core -- the three tiny AI engines behind one Caravel Wishbone register interface (SPEC.md).
//
// Engines (instantiated unchanged, each verified on its own): vision_all_lit (mode 0, 4 inputs),
// vision_block (mode 1, 9 inputs), text_sentiment (mode 2, 4 inputs). Exactly three compute nodes.
// Software pushes the inputs into a buffer through INPUT, then writes START. The controller streams the buffer into
// the selected engine (one item per clock), takes the engine's two result beats, and commits RESULT, DONE and CYCLES,
// with a one-clock pulse on irq[0].
//
// What is neural network here and what is not: the NETWORKS are only the three engines (a binary neuron, a 2x2
// convolution kernel reused at four positions with max-pooling, an embedding table with an accumulator). Their weights
// were learned by model/tiny_ai/train.py from labelled examples and live in the generated *_rom.v files; this module
// adds none. Everything in this file is ordinary system glue, the part every accelerator also has: a bus register
// block (how the CPU talks to the accelerator), an input buffer (where the data waits), a sequencer that streams the
// data into the selected network and collects its answer, and status and interrupt. Changing what the chip
// recognises means retraining and regenerating the ROMs; nothing in this file changes.
//
// Register map (base 0x3000_0000, 256-byte window; byte offsets):
//   0x00 ID      R   0x54414901
//   0x04 CTRL    RW  [1:0] mode (byte lane 0); W: bit 8 START, bit 9 CLEAR (byte lane 1, self-clearing, read 0)
//   0x08 STATUS  R   [0] BUSY [1] DONE [2] ERROR [5:4] active mode [11:8] input count
//   0x0C INPUT   W   [7:0] one input, pushed only while idle (byte lane 0)
//   0x10 RESULT  R   [0] class [15:8] signed debug score
//   0x14 CYCLES  R   clock edges from the edge that accepts START to the edge that commits the result (8 bits used)
//   0x18 CAPS    R   [2:0] supported modes 3'b111, [11:8] maximum inputs 9, [23:16] RTL version 1
//   0x1C DEBUG   R   [17:0] the input buffer, 2 bits per input, input 0 in bits [1:0]
// Bus rules: every transaction gets exactly one wbs_ack_o pulse. Writes act only on the byte lanes named above.
// Read data is masked by wbs_sel_i. Unmapped reads (any other offset, or an address outside the window) return 0;
// unmapped writes do nothing.
// Protocol rules (all set the sticky ERROR bit and otherwise have no effect):
//   START or INPUT while busy; START with mode 3; START when the input count is not exactly the mode's length;
//   an input out of range for the current mode (above 1 for modes 0 and 1, above 3 for mode 2, any input in mode 3);
//   an input when the buffer already holds 9; CLEAR while busy.
// CLEAR (idle) resets DONE, ERROR, input count, buffer, RESULT and CYCLES. START and CLEAR in one write: CLEAR wins
// and START is ignored. DONE stays set until CLEAR or the next valid START; ERROR stays set until CLEAR.
// irq[0]: one-clock pulse when a result is committed; irq[2:1] = 0.
// Simplified for learning (owner decision 2026-10-06): only the Wishbone bus and the interrupt leave the core; results
// are read over Wishbone and irq[0] signals completion. The GPIO and logic-analyser mirrors of SPEC.md External
// observability are not implemented.
`timescale 1ns/1ps
`default_nettype none
module tiny_ai_core (
`ifdef USE_POWER_PINS
    inout  wire         vccd1,
    inout  wire         vssd1,
`endif
    input  wire         wb_clk_i,
    input  wire         wb_rst_i,
    input  wire         wbs_stb_i,
    input  wire         wbs_cyc_i,
    input  wire         wbs_we_i,
    input  wire [3:0]   wbs_sel_i,
    input  wire [31:0]  wbs_dat_i,
    input  wire [31:0]  wbs_adr_i,
    output wire         wbs_ack_o,
    output wire [31:0]  wbs_dat_o,
    output wire [2:0]   irq
);
    localparam [31:0] ID_VALUE    = 32'h5441_4901;
    localparam [23:0] BASE_PAGE   = 24'h30_0000;          // wbs_adr_i[31:8] of the register window
    localparam [7:0]  RTL_VERSION = 8'd1;
    localparam [3:0]  MAX_INPUTS  = 4'd9;

    localparam [2:0]  ST_IDLE  = 3'd0;   // waiting for START
    localparam [2:0]  ST_FEED  = 3'd1;   // streaming the buffer into the engine
    localparam [2:0]  ST_BEAT0 = 3'd2;   // waiting for result beat 0 {error, class}
    localparam [2:0]  ST_BEAT1 = 3'd3;   // waiting for result beat 1 (score)

    wire clk = wb_clk_i;
    wire rst = wb_rst_i;

    // ---------------------------------------------------------------- registers (glue state, no learned values)
    reg  [2:0]  state;
    reg  [1:0]  mode;          // CTRL[1:0]
    reg  [1:0]  active;        // mode latched at START
    reg  [3:0]  count;         // inputs in the buffer
    reg  [17:0] buffer;        // 9 inputs x 2 bits
    reg  [3:0]  feed_idx;      // next input to stream
    reg         done;
    reg         error;
    reg         res_class;
    reg  [7:0]  res_score;
    reg  [7:0]  cycles;        // CYCLES (saturating; the longest mode needs 15)
    reg  [7:0]  run_cycles;    // edges counted since START
    reg         beat_class;    // beat 0 captured
    reg         irq_pulse;
    reg         ack;

    wire busy = (state != ST_IDLE);

    function [3:0] mode_len(input [1:0] m);
        mode_len = (m == 2'd1) ? 4'd9 : 4'd4;
    endfunction

    // ---------------------------------------------------------------- the three neural network engines
    // Exactly three compute nodes. Only the engine selected at START sees s_valid / m_ready; the others stay idle.
    // The buffer item for the current step goes to every engine's s_data; s_last marks the mode's final input.
    wire [3:0] n_active = mode_len(active);
    wire       feeding  = (state == ST_FEED);
    wire       taking   = (state == ST_BEAT0) | (state == ST_BEAT1);
    reg  [1:0] item;                                        // buffer[feed_idx]; 0 past the end (no X)
    always @(*) begin
        case (feed_idx)
            4'd0:    item = buffer[1:0];
            4'd1:    item = buffer[3:2];
            4'd2:    item = buffer[5:4];
            4'd3:    item = buffer[7:6];
            4'd4:    item = buffer[9:8];
            4'd5:    item = buffer[11:10];
            4'd6:    item = buffer[13:12];
            4'd7:    item = buffer[15:14];
            4'd8:    item = buffer[17:16];
            default: item = 2'd0;
        endcase
    end
    wire [7:0] s_data   = {6'b0, item};
    wire       s_last   = (feed_idx == n_active - 4'd1);

    wire       s_ready0, s_ready1, s_ready2;
    wire       m_valid0, m_valid1, m_valid2;
    wire [7:0] m_data0,  m_data1,  m_data2;
    wire       m_last0,  m_last1,  m_last2;

    vision_all_lit u_vision_all_lit (
        .clk(clk), .rst(rst),
        .s_valid(feeding & (active == 2'd0)), .s_data(s_data), .s_last(s_last), .s_ready(s_ready0),
        .m_valid(m_valid0), .m_data(m_data0), .m_last(m_last0), .m_ready(taking & (active == 2'd0)));
    vision_block u_vision_block (
        .clk(clk), .rst(rst),
        .s_valid(feeding & (active == 2'd1)), .s_data(s_data), .s_last(s_last), .s_ready(s_ready1),
        .m_valid(m_valid1), .m_data(m_data1), .m_last(m_last1), .m_ready(taking & (active == 2'd1)));
    text_sentiment u_text_sentiment (
        .clk(clk), .rst(rst),
        .s_valid(feeding & (active == 2'd2)), .s_data(s_data), .s_last(s_last), .s_ready(s_ready2),
        .m_valid(m_valid2), .m_data(m_data2), .m_last(m_last2), .m_ready(taking & (active == 2'd2)));

    wire       s_ready = (active == 2'd1) ? s_ready1 : (active == 2'd2) ? s_ready2 : s_ready0;
    wire       m_valid = (active == 2'd1) ? m_valid1 : (active == 2'd2) ? m_valid2 : m_valid0;
    wire [7:0] m_data  = (active == 2'd1) ? m_data1  : (active == 2'd2) ? m_data2  : m_data0;
    wire       m_last  = (active == 2'd1) ? m_last1  : (active == 2'd2) ? m_last2  : m_last0;

    // ---------------------------------------------------------------- Wishbone decode (bus glue)
    // A classic Wishbone slave: a request is cyc & stb; the core answers with a one-cycle ack on the next edge, so
    // `take` is true on exactly one edge per transaction and every register write acts once.
    wire        valid    = wbs_cyc_i & wbs_stb_i;
    wire        take     = valid & ~ack;                    // the one edge on which a transaction acts
    wire        in_win   = (wbs_adr_i[31:8] == BASE_PAGE);
    wire [5:0]  word     = wbs_adr_i[7:2];
    wire        wr       = take & wbs_we_i & in_win;
    wire        wr_ctrl  = wr & (word == 6'd1);
    wire        wr_input = wr & (word == 6'd3) & wbs_sel_i[0];
    wire        cmd_start = wr_ctrl & wbs_sel_i[1] & wbs_dat_i[8];
    wire        cmd_clear = wr_ctrl & wbs_sel_i[1] & wbs_dat_i[9];
    wire        set_mode  = wr_ctrl & wbs_sel_i[0];

    wire [7:0]  in_val    = wbs_dat_i[7:0];
    wire        in_range  = (mode == 2'd2) ? (in_val <= 8'd3) : (mode == 2'd3) ? 1'b0 : (in_val <= 8'd1);
    wire        start_ok  = ~busy & (mode != 2'd3) & (count == mode_len(mode));

    wire [31:0] status = {20'd0, count, 2'd0, active, 1'b0, error, done, busy};
    wire [31:0] result = {16'd0, res_score, 7'd0, res_class};

    reg  [31:0] rdata;
    always @(*) begin
        case (word)
            6'd0:    rdata = ID_VALUE;
            6'd1:    rdata = {30'd0, mode};
            6'd2:    rdata = status;
            6'd4:    rdata = result;
            6'd5:    rdata = {24'd0, cycles};
            6'd6:    rdata = {8'd0, RTL_VERSION, 4'd0, MAX_INPUTS, 5'd0, 3'b111};
            6'd7:    rdata = {14'd0, buffer};
            default: rdata = 32'd0;                          // INPUT is write-only; offsets 0x20..0xFC unmapped
        endcase
    end
    wire [31:0] sel_mask = {{8{wbs_sel_i[3]}}, {8{wbs_sel_i[2]}}, {8{wbs_sel_i[1]}}, {8{wbs_sel_i[0]}}};
    assign wbs_ack_o = ack;
    assign wbs_dat_o = (ack & ~wbs_we_i & in_win) ? (rdata & sel_mask) : 32'd0;

    // ---------------------------------------------------------------- control (sequencer)
    // IDLE: the CPU fills the buffer and writes START. FEED: one buffered input per clock into the selected network.
    // BEAT0 / BEAT1: take the network's two result beats ({error, class}, then score), commit RESULT and CYCLES, set
    // DONE and pulse irq[0]. Inference itself happens inside the engine between FEED and BEAT0.
    always @(posedge clk) begin
        if (rst) begin
            state      <= ST_IDLE;
            mode       <= 2'd0;
            active     <= 2'd0;
            count      <= 4'd0;
            buffer     <= 18'd0;
            feed_idx   <= 4'd0;
            done       <= 1'b0;
            error      <= 1'b0;
            res_class  <= 1'b0;
            res_score  <= 8'd0;
            cycles     <= 8'd0;
            run_cycles <= 8'd0;
            beat_class <= 1'b0;
            irq_pulse  <= 1'b0;
            ack        <= 1'b0;
        end else begin
            ack       <= take;
            irq_pulse <= 1'b0;
            if (set_mode) mode <= wbs_dat_i[1:0];

            // bus commands
            if (cmd_clear) begin
                if (busy) error <= 1'b1;
                else begin
                    done      <= 1'b0;
                    error     <= 1'b0;
                    count     <= 4'd0;
                    buffer    <= 18'd0;
                    res_class <= 1'b0;
                    res_score <= 8'd0;
                    cycles    <= 8'd0;
                end
            end else if (cmd_start) begin
                if (start_ok) begin
                    state      <= ST_FEED;
                    active     <= mode;
                    feed_idx   <= 4'd0;
                    done       <= 1'b0;
                    run_cycles <= 8'd0;
                end else error <= 1'b1;
            end
            if (wr_input) begin
                if (busy | ~in_range | (count == MAX_INPUTS)) error <= 1'b1;
                else begin
                    buffer[{count, 1'b0} +: 2] <= in_val[1:0];
                    count <= count + 4'd1;
                end
            end

            // the run
            if (busy && run_cycles != 8'hFF) run_cycles <= run_cycles + 8'd1;
            case (state)
                ST_FEED: if (s_ready) begin
                    feed_idx <= feed_idx + 4'd1;
                    if (s_last) state <= ST_BEAT0;
                end
                ST_BEAT0: if (m_valid) begin
                    beat_class <= m_data[0];
                    if (m_data[1]) error <= 1'b1;        // an engine-detected input error (not reachable: inputs are checked on entry)
                    state <= ST_BEAT1;
                end
                ST_BEAT1: if (m_valid & m_last) begin
                    res_class <= beat_class;
                    res_score <= m_data;
                    cycles    <= (run_cycles == 8'hFF) ? 8'hFF : run_cycles + 8'd1;
                    done      <= 1'b1;
                    irq_pulse <= 1'b1;
                    state     <= ST_IDLE;
                end
                default: ;
            endcase
        end
    end

    // ---------------------------------------------------------------- interrupt
    assign irq = {2'b00, irq_pulse};
endmodule
`default_nettype wire
