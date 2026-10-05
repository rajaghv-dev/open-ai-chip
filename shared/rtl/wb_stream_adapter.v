// SPDX-License-Identifier: Apache-2.0
// wb_stream_adapter -- a generic Caravel Wishbone slave that drives ONE stream engine's 24 ports unchanged
// (docs/SOC_PLAN.md section 2, option (a)). The engine is outside this module: the adapter exports the stream ports
// (s_valid/s_data/s_last/s_ready in, m_valid/m_data/m_last/m_ready out, plus eng_rst) and a thin top wires them to the
// engine. Works for frame engines (N input beats with s_last on the last, then 2 result beats) and for streaming
// engines (one result per sample after warm-up) alike: software pushes beats, drains results.
//
//   CPU --Wishbone--> [TX FIFO] --s_valid/s_data/s_last--> engine --m_valid/m_data/m_last--> [RX FIFO] --Wishbone--> CPU
//
// Register map (base 0x3000_0000, 256-byte window; byte offsets; word = adr[7:2]):
//   0x00 ID       R   0x5354_5201
//   0x04 CTRL     RW  [0] irq enable (byte lane 0, resets to 0); W: bit 8 CLEAR (byte lane 1, self-clearing, reads 0)
//   0x08 STATUS   R   [0] TX_FULL [1] TX_EMPTY [2] RX_EMPTY [3] BUSY [4] DONE [5] ERR_OVF [6] ERR_UF
//                     [15:8] TX level [23:16] RX level
//   0x0C TXDATA   W   [7:0] pushed as s_data with s_last = 0 (byte lane 0)
//   0x10 TXLAST   W   [7:0] pushed as s_data with s_last = 1 (byte lane 0)
//   0x14 RXDATA   R   [7:0] m_data [8] m_last [9] valid; the read POPS the RX FIFO (byte lane 0 or 1 selected).
//                     Reading while empty returns 0 (valid = 0), pops nothing and sets ERR_UF.
//   0x18 RXSTATUS R   [0] EMPTY [1] HEAD_LAST (the next beat to pop has m_last) [2] DONE [15:8] RX level (no side effect)
//   0x1C CYCLES   R   cycles of the last (or running) run, from the edge that accepts its first TX beat to the edge that
//                     captures its m_last result beat (16 bits, saturating; same counting as tiny_ai_core CYCLES)
//   0x20 CAPS     R   [7:0] RTL version 1, [15:8] TX depth, [23:16] RX depth
// BUSY = a run is in progress (first TX beat accepted, m_last result beat not yet captured). DONE = an m_last result
// beat was captured since the run started; DONE and BUSY clear on CLEAR and when the next run's first TX beat arrives.
// ERR_OVF (sticky): a TXDATA/TXLAST write while the TX FIFO was full (the beat is dropped). ERR_UF (sticky): an RXDATA
// read while the RX FIFO was empty. Both clear on CLEAR. The RX FIFO cannot overflow: m_ready = ~RX full, so a full RX
// FIFO stalls the engine (back-pressure) instead of losing beats.
// CLEAR: resets both FIFOs, the engine (eng_rst, two clocks), BUSY, DONE, errors and CYCLES; irq enable is kept.
// irq[0]: one-clock pulse when a beat with m_last is captured into the RX FIFO, if CTRL[0] is set. irq[2:1] = 0.
//
// Bus rules (same as tiny_ai_core): base 0x3000_0000; every transaction gets exactly one wbs_ack_o pulse (take =
// valid & ~ack is the one edge a transaction acts on); read data is masked by wbs_sel_i; unmapped offsets read 0 and
// writes to them do nothing; out-of-window addresses read 0 and ack. Read data is captured on the take edge, so the
// RXDATA pop does not disturb the data returned. Parameters TX_DEPTH / RX_DEPTH must be powers of two, 2..64.
`timescale 1ns/1ps
`default_nettype none

// Small synchronous FIFO, combinational read of the head. Push while full and pop while empty are ignored (the caller
// reports them). `clr` empties it. The storage has no reset (it is never read while empty: dout is forced to 0).
module wb_stream_fifo #(
    parameter integer DEPTH = 16,
    parameter integer WIDTH = 9,
    parameter integer AW    = 4               // log2(DEPTH)
) (
    input  wire             clk,
    input  wire             clr,
    input  wire             push,
    input  wire [WIDTH-1:0] din,
    input  wire             pop,
    output wire [WIDTH-1:0] dout,
    output wire [AW:0]      count,
    output wire             full,
    output wire             empty
);
    reg [WIDTH-1:0] mem [0:DEPTH-1];
    reg [AW-1:0]    wp;
    reg [AW-1:0]    rp;
    reg [AW:0]      cnt;
    assign count = cnt;
    assign full  = (cnt == DEPTH[AW:0]);
    assign empty = (cnt == {(AW+1){1'b0}});
    assign dout  = empty ? {WIDTH{1'b0}} : mem[rp];
    wire do_push = push & ~full;
    wire do_pop  = pop & ~empty;
    always @(posedge clk) begin
        if (clr) begin
            wp  <= {AW{1'b0}};
            rp  <= {AW{1'b0}};
            cnt <= {(AW+1){1'b0}};
        end else begin
            if (do_push) begin
                mem[wp] <= din;
                wp      <= wp + 1'b1;
            end
            if (do_pop) rp <= rp + 1'b1;
            cnt <= cnt + {{AW{1'b0}}, do_push} - {{AW{1'b0}}, do_pop};
        end
    end
endmodule

module wb_stream_adapter #(
    parameter integer TX_DEPTH = 16,
    parameter integer RX_DEPTH = 16
) (
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
    output wire [2:0]   irq,
    // stream side: wire these to ONE engine, unchanged
    output wire         eng_rst,      // engine reset: wb_rst_i or two clocks after a CLEAR
    output wire         s_valid,
    output wire [7:0]   s_data,
    output wire         s_last,
    input  wire         s_ready,
    input  wire         m_valid,
    input  wire [7:0]   m_data,
    input  wire         m_last,
    output wire         m_ready
);
    localparam [31:0] ID_VALUE    = 32'h5354_5201;
    localparam [23:0] BASE_PAGE   = 24'h30_0000;          // wbs_adr_i[31:8] of the register window
    localparam [7:0]  RTL_VERSION = 8'd1;
    localparam integer TAW = $clog2(TX_DEPTH);
    localparam integer RAW = $clog2(RX_DEPTH);

    wire clk = wb_clk_i;
    wire rst = wb_rst_i;

    // ---------------------------------------------------------------- Wishbone decode
    reg         ack;
    reg         rd_ack;                                   // the pending ack answers a read
    reg  [31:0] rdata_q;                                  // read data captured on the take edge
    wire        valid   = wbs_cyc_i & wbs_stb_i;
    wire        take    = valid & ~ack;
    wire        in_win  = (wbs_adr_i[31:8] == BASE_PAGE);
    wire [5:0]  word    = wbs_adr_i[7:2];
    wire        wr      = take & wbs_we_i & in_win;
    wire        rd      = take & ~wbs_we_i & in_win;
    wire        wr_ctrl = wr & (word == 6'd1);
    wire        wr_tx   = wr & ((word == 6'd3) | (word == 6'd4)) & wbs_sel_i[0];
    wire        tx_last = (word == 6'd4);
    wire        cmd_clear = wr_ctrl & wbs_sel_i[1] & wbs_dat_i[8];
    wire        rd_rx   = rd & (word == 6'd5) & (wbs_sel_i[0] | wbs_sel_i[1]);

    // ---------------------------------------------------------------- state
    reg         irq_en;
    reg         clear_q;                                  // CLEAR was taken on the previous edge: engine reset cycle
    reg         running;
    reg         done;
    reg         err_ovf;
    reg         err_uf;
    reg  [15:0] cycles;
    reg         irq_pulse;

    wire clr = rst | cmd_clear;                           // empties FIFOs and run state
    assign eng_rst = rst | clear_q;

    // ---------------------------------------------------------------- FIFOs
    wire [8:0]     tx_head;
    wire [TAW:0]   tx_count;
    wire           tx_full, tx_empty;
    wire [8:0]     rx_head;
    wire [RAW:0]   rx_count;
    wire           rx_full, rx_empty;

    assign s_valid = ~tx_empty & ~clear_q;
    assign s_data  = tx_head[7:0];
    assign s_last  = tx_head[8];
    wire   tx_pop  = s_valid & s_ready;
    wire   tx_push = wr_tx;                               // pushes while full are ignored by the FIFO and flagged below

    assign m_ready = ~rx_full & ~clear_q;
    wire   rx_push = m_valid & m_ready;                   // taken beat; the FIFO is never full here
    wire   rx_pop  = rd_rx & ~rx_empty;

    wb_stream_fifo #(.DEPTH(TX_DEPTH), .WIDTH(9), .AW(TAW)) u_tx (
        .clk(clk), .clr(clr), .push(tx_push), .din({tx_last, wbs_dat_i[7:0]}), .pop(tx_pop),
        .dout(tx_head), .count(tx_count), .full(tx_full), .empty(tx_empty));
    wb_stream_fifo #(.DEPTH(RX_DEPTH), .WIDTH(9), .AW(RAW)) u_rx (
        .clk(clk), .clr(clr), .push(rx_push), .din({m_last, m_data}), .pop(rx_pop),
        .dout(rx_head), .count(rx_count), .full(rx_full), .empty(rx_empty));

    // ---------------------------------------------------------------- read data
    wire [31:0] status   = {8'd0, {(8-RAW-1){1'b0}}, rx_count, {(8-TAW-1){1'b0}}, tx_count,
                            1'b0, err_uf, err_ovf, done, running, rx_empty, tx_empty, tx_full};
    wire [31:0] rxstatus = {16'd0, {(8-RAW-1){1'b0}}, rx_count, 5'd0, done, rx_head[8] & ~rx_empty, rx_empty};
    reg  [31:0] rdata;
    always @(*) begin
        case (word)
            6'd0:    rdata = ID_VALUE;
            6'd1:    rdata = {31'd0, irq_en};
            6'd2:    rdata = status;
            6'd5:    rdata = {22'd0, ~rx_empty, rx_head[8:0]};
            6'd6:    rdata = rxstatus;
            6'd7:    rdata = {16'd0, cycles};
            6'd8:    rdata = {8'd0, RX_DEPTH[7:0], TX_DEPTH[7:0], RTL_VERSION};
            default: rdata = 32'd0;                       // TXDATA/TXLAST write-only; other offsets unmapped
        endcase
    end
    wire [31:0] sel_mask = {{8{wbs_sel_i[3]}}, {8{wbs_sel_i[2]}}, {8{wbs_sel_i[1]}}, {8{wbs_sel_i[0]}}};
    assign wbs_ack_o = ack;
    assign wbs_dat_o = rd_ack ? rdata_q : 32'd0;

    // ---------------------------------------------------------------- control, errors, run timer, irq
    wire rx_last_taken = rx_push & m_last;
    always @(posedge clk) begin
        if (rst) begin
            ack       <= 1'b0;
            rd_ack    <= 1'b0;
            rdata_q   <= 32'd0;
            irq_en    <= 1'b0;
            clear_q   <= 1'b0;
            running   <= 1'b0;
            done      <= 1'b0;
            err_ovf   <= 1'b0;
            err_uf    <= 1'b0;
            cycles    <= 16'd0;
            irq_pulse <= 1'b0;
        end else begin
            ack       <= take;
            rd_ack    <= rd | (take & ~wbs_we_i);         // out-of-window reads ack with data 0
            if (take) rdata_q <= (in_win & ~wbs_we_i) ? (rdata & sel_mask) : 32'd0;
            clear_q   <= cmd_clear;
            irq_pulse <= rx_last_taken & irq_en;
            if (wr_ctrl & wbs_sel_i[0]) irq_en <= wbs_dat_i[0];

            if (cmd_clear) begin
                running <= 1'b0;
                done    <= 1'b0;
                err_ovf <= 1'b0;
                err_uf  <= 1'b0;
                cycles  <= 16'd0;
            end else begin
                if (tx_push & tx_full) err_ovf <= 1'b1;
                if (rd_rx & rx_empty)  err_uf  <= 1'b1;
                // run timer: starts at the edge that accepts the first TX beat of a run, stops at the m_last capture
                if (tx_push & ~tx_full & ~running) begin
                    running <= 1'b1;
                    done    <= 1'b0;
                    cycles  <= 16'd0;
                end else if (running) begin
                    if (cycles != 16'hFFFF) cycles <= cycles + 16'd1;
                    if (rx_last_taken) begin
                        running <= 1'b0;
                        done    <= 1'b1;
                    end
                end
            end
        end
    end

    assign irq = {2'b00, irq_pulse};
endmodule
`default_nettype wire
