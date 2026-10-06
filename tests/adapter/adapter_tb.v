// SPDX-License-Identifier: Apache-2.0
// adapter_tb -- self-checking testbench for shared/rtl/wb_stream_adapter.v with one stream engine behind it.
// Not a hardened-run input (never in a config.json file list). PASS = the "PASS" line from the shared checker, vvp exit 0.
// Docs: tests/TEST_MATRIX.md, docs/SOC_PLAN.md, docs/ARCHITECTURE.md
// Compiled once per engine by tests/adapter/run.sh with -DENG=<engine module> -DFMT=<1|2|3> -DNAME="<name>":
//   FMT 1  frame engines (stream_tb.vh record layout: 16-byte records [0] n, [1..n] inputs, [13] beat 0, [14] beat 1)
//   FMT 2  audio_pitch word layout (gen_rom.py: header 4 words, one word per input beat)
//   FMT 3  audio_onset word layout (header 3 words, flags: bit 9 reset before the beat, bit 20 reset with a result waiting)
//   KV     kv_attn_* layout (header record + 40-word records: [0] type 01 COMMAND / 02 RST_IDLE / 03 RST_MID / 04 RST_PEND,
//          [1] n_in, [2] n_out, [3] latency, [4..11] expected beats, [12..39] input beats; model/kv_attention/spec.md):
//          COMMAND = its beats (s_last on the last) and n_out result beats; RST_IDLE = CLEAR before the next beat;
//          RST_MID = its beats without s_last, then CLEAR; RST_PEND = a whole frame whose response (n_out is 0 in the record,
//          the size is not known) is waited for with RXSTATUS.DONE and then discarded by CLEAR; the beats of such a frame
//          are pushed without popping results, and the results before it are drained first.
// The vectors are flattened into one input-beat sequence and the in-order sequence of expected result beats, then driven
// through the Wishbone bus only (TXDATA / TXLAST writes, RXSTATUS / RXDATA reads):
//   0. register checks: ID, CAPS, CTRL, STATUS, byte-lane masking, unmapped and out-of-window accesses, ignored writes;
//   1. phase 0, irq disabled: the first record / first beats, drained; STATUS, CYCLES (frames) and "no irq" checked;
//   2. burst: beats are pushed without reading results until the TX FIFO is full (engine stalled by a full RX FIFO),
//      one more write must set ERR_OVF and be dropped;
//   3. interleaved: every beat is written and the results that arrived are popped and compared, then drained; the
//      number of irq[0] pulses must equal the number of m_last result beats captured while irq was enabled;
//   4. ERR_UF, then CLEAR (FIFOs, engine, errors) and a replay of phase 0 that must give the same results.
// +VEC=<vectors.hex> required; +NLIM=<n> optional (limit on input beats; the run says so in its PASS line).
// Comparisons use !== so X never passes. First failure: $fatal(1, "FAIL ..."). Success: "PASS <name>: ...".
`timescale 1ns/1ps
`default_nettype none
`ifdef FRAME
  `define FRAMECHK
`endif
`ifdef KV
  `define FRAMECHK
`endif
module adapter_tb;
    reg          wb_clk_i = 1'b0;
    reg          wb_rst_i = 1'b1;
    reg          wbs_stb_i = 1'b0, wbs_cyc_i = 1'b0, wbs_we_i = 1'b0;
    reg  [3:0]   wbs_sel_i = 4'd0;
    reg  [31:0]  wbs_dat_i = 32'd0, wbs_adr_i = 32'd0;
    wire         wbs_ack_o;
    wire [31:0]  wbs_dat_o;
    wire [2:0]   irq;

    wire        eng_rst, s_valid, s_last, s_ready, m_valid, m_last, m_ready;
    wire [7:0]  s_data, m_data;

    wb_stream_adapter #(.TX_DEPTH(16), .RX_DEPTH(16)) dut (
        .wb_clk_i(wb_clk_i), .wb_rst_i(wb_rst_i),
        .wbs_stb_i(wbs_stb_i), .wbs_cyc_i(wbs_cyc_i), .wbs_we_i(wbs_we_i), .wbs_sel_i(wbs_sel_i),
        .wbs_dat_i(wbs_dat_i), .wbs_adr_i(wbs_adr_i), .wbs_ack_o(wbs_ack_o), .wbs_dat_o(wbs_dat_o), .irq(irq),
        .eng_rst(eng_rst), .s_valid(s_valid), .s_data(s_data), .s_last(s_last), .s_ready(s_ready),
        .m_valid(m_valid), .m_data(m_data), .m_last(m_last), .m_ready(m_ready));

    `ENG eng (.clk(wb_clk_i), .rst(eng_rst), .s_valid(s_valid), .s_data(s_data), .s_last(s_last), .s_ready(s_ready),
              .m_valid(m_valid), .m_data(m_data), .m_last(m_last), .m_ready(m_ready));

    always #5 wb_clk_i = ~wb_clk_i;

    localparam [31:0] A_ID = 32'h3000_0000, A_CTRL = 32'h3000_0004, A_STATUS = 32'h3000_0008, A_TXDATA = 32'h3000_000C,
                      A_TXLAST = 32'h3000_0010, A_RXDATA = 32'h3000_0014, A_RXSTAT = 32'h3000_0018,
                      A_CYCLES = 32'h3000_001C, A_CAPS = 32'h3000_0020;
    localparam integer MAXB = 131072;

    // ------------------------------------------------------------ vectors, flattened
    reg  [7:0]  vb [0:65535];                 // FMT 1 bytes
    reg  [31:0] vw [0:MAXB+7];                // FMT 2/3 words
    reg  [8:0]  in_b  [0:MAXB-1];             // {s_last, s_data}
    reg  [3:0]  pre_b [0:MAXB-1];             // bit 0 drain then CLEAR, bit 1 CLEAR while the last result is waiting (lost),
                                              // bit 2 drain only (first beat of a KV RST_PEND frame), bit 3 wait for DONE then CLEAR
                                              // (after a KV RST_PEND frame: its response is lost, none is expected)
    reg         skp_b [0:MAXB-1];             // 1: do not pop results after this beat (beats of a KV RST_PEND frame)
    integer     end_b [0:MAXB-1];             // number of expected result beats once input beat i has been pushed
    reg  [8:0]  exp_r [0:MAXB-1];             // {m_last, m_data}
    integer     npend = 0, carry, typ, nin, nout, nclr0, nb, nbu, nexp, ne, nlim, nvec, nrec_n, k, j, pfirst, i, e0, nexp0, irq_exp, b, t, filled;
    reg  [1023:0] vfile;
    reg  [31:0] w;
    integer     n_checks = 0, n_tx = 0;
    reg  [31:0] rdat;

    // ------------------------------------------------------------ monitors
    integer reqs = 0, acks = 0, irq_rises = 0, irq_hi = 0;
    reg     mon_en = 1'b0, prev_ack = 1'b0, prev_irq = 1'b0;
    always @(negedge wb_clk_i) begin
        #2;
        if (mon_en) begin
            if (wbs_ack_o !== 1'b0 && wbs_ack_o !== 1'b1) $fatal(1, "FAIL %s: wbs_ack_o is X (t=%0t)", `NAME, $time);
            if (wbs_ack_o === 1'b1) begin
                if (prev_ack) $fatal(1, "FAIL %s: wbs_ack_o wider than one clock (t=%0t)", `NAME, $time);
                acks = acks + 1;
                if (acks > reqs) $fatal(1, "FAIL %s: wbs_ack_o without a request (t=%0t)", `NAME, $time);
            end
            prev_ack = (wbs_ack_o === 1'b1);
            if (irq !== 3'b000 && irq !== 3'b001) $fatal(1, "FAIL %s: irq=%b (X or irq[2:1] != 0) (t=%0t)", `NAME, irq, $time);
            if (irq[0] === 1'b1) begin
                irq_hi = irq_hi + 1;
                if (!prev_irq) irq_rises = irq_rises + 1;
            end
            prev_irq = (irq[0] === 1'b1);
        end
    end

    initial begin
        #(64'd2000000000);
        $fatal(1, "FAIL %s: timeout: testbench did not finish", `NAME);
    end

    // ------------------------------------------------------------ bus tasks
    task chk(input [63:0] got, input [63:0] want, input [8*40-1:0] what);
        begin
            n_checks = n_checks + 1;
            if (got !== want)
                $fatal(1, "FAIL %s: %0s: got %h expected %h (beat %0d, popped %0d, t=%0t)", `NAME, what, got, want, i, ne, $time);
        end
    endtask

    task idle(input integer n);
        begin repeat (n) @(negedge wb_clk_i); end
    endtask

    // classic cycle, called on a falling edge; read data in rdat
    task wb(input we, input [3:0] sel, input [31:0] adr, input [31:0] dat);
        integer t;
        begin
            reqs = reqs + 1; n_tx = n_tx + 1;
            wbs_cyc_i = 1'b1; wbs_stb_i = 1'b1; wbs_we_i = we; wbs_sel_i = sel; wbs_adr_i = adr; wbs_dat_i = dat;
            t = 0;
            @(negedge wb_clk_i);
            while (wbs_ack_o !== 1'b1) begin
                if (wbs_ack_o !== 1'b0) $fatal(1, "FAIL %s: wbs_ack_o is X during a request", `NAME);
                t = t + 1;
                if (t > 8) $fatal(1, "FAIL %s: no wbs_ack_o within 8 cycles (adr %h)", `NAME, adr);
                @(negedge wb_clk_i);
            end
            rdat = wbs_dat_o;
            wbs_cyc_i = 1'b0; wbs_stb_i = 1'b0; wbs_we_i = 1'b0; wbs_sel_i = 4'd0; wbs_adr_i = 32'd0; wbs_dat_i = 32'd0;
            @(negedge wb_clk_i);
            n_checks = n_checks + 1;
            if (wbs_ack_o !== 1'b0) $fatal(1, "FAIL %s: wbs_ack_o not exactly one clock wide (adr %h)", `NAME, adr);
            if (wbs_dat_o !== 32'd0) $fatal(1, "FAIL %s: wbs_dat_o not 0 outside the ack cycle (adr %h)", `NAME, adr);
        end
    endtask
    task rd (input [31:0] adr);                  wb(1'b0, 4'hF, adr, 32'd0); endtask
    task wr (input [3:0] sel, input [31:0] adr, input [31:0] dat); wb(1'b1, sel, adr, dat); endtask
    integer nclr = 0;
    task do_clear;                               begin nclr = nclr + 1; wr(4'b0010, A_CTRL, 32'h0000_0100); idle(3); end endtask

    task push_beat(input integer b);
        begin wr(4'hF, in_b[b][8] ? A_TXLAST : A_TXDATA, {24'd0, in_b[b][7:0]}); end
    endtask

    // read one RX beat, compare with the next expected one (want = results due so far)
    task pop_one(input integer want);
        begin
            rd(A_RXDATA);
            if (ne >= want) $fatal(1, "FAIL %s: unexpected result beat %h (%0d popped, %0d due)", `NAME, rdat, ne, want);
            chk(rdat, {22'd0, 1'b1, exp_r[ne]}, "RXDATA {valid,last,data}");
            ne = ne + 1;
        end
    endtask

    // pop what has arrived
    task pop_avail(input integer want);
        integer c;
        begin
            rd(A_RXSTAT);
            c = rdat[15:8];
            chk(rdat[0], (c == 0), "RXSTATUS.EMPTY vs count");
            while (c > 0) begin pop_one(want); c = c - 1; end
        end
    endtask

    // wait until result beats up to `want` have all been popped, then check nothing else arrives
    task drain(input integer want);
        integer t;
        begin
            t = 0;
            while (ne < want) begin
                pop_avail(want);
                t = t + 1;
                if (t > 400) $fatal(1, "FAIL %s: result beat missing (%0d of %0d popped)", `NAME, ne, want);
            end
            idle(20);
            rd(A_RXSTAT);
            chk(rdat[15:8], 0, "RX level after drain");
            chk(rdat[0], 1, "RX empty after drain");
            rd(A_STATUS);
            chk(rdat[1], 1, "STATUS.TX_EMPTY after drain");
        end
    endtask

    // wait until the RX FIFO holds exactly n beats
    task wait_level(input integer n);
        integer t2;
        begin
            t2 = 0; rd(A_RXSTAT);
            while (rdat[15:8] != n) begin
                t2 = t2 + 1;
                if (t2 > 400) $fatal(1, "FAIL %s: RX level %0d, expected %0d", `NAME, rdat[15:8], n);
                rd(A_RXSTAT);
            end
        end
    endtask

    // process input beat b interleaved: optional reset first, then write, then pop what has arrived
    task do_beat(input integer bb);
        integer w0, tt;
        begin
            w0 = (bb > 0) ? end_b[bb - 1] : 0;
            if (pre_b[bb][3]) begin                     // KV RST_PEND: its response (size unknown) is complete, then it is lost
                tt = 0; rd(A_RXSTAT);
                while (rdat[2] !== 1'b1) begin
                    tt = tt + 1;
                    if (tt > 400) $fatal(1, "FAIL %s: DONE never set for a pending response (beat %0d)", `NAME, bb);
                    rd(A_RXSTAT);
                end
                chk(rdat[15:8] != 0, 1, "pending response in the RX FIFO");
                do_clear;
                npend = npend + 1;
            end
            if (pre_b[bb][0]) begin
                drain(w0);
                do_clear;
            end else if (pre_b[bb][1]) begin            // the last result is waiting in the RX FIFO when the reset hits
                wait_level(w0 - ne);
                while (ne < w0 - 1) pop_one(w0);
                do_clear;
                ne = w0;                                // that result is lost by design
            end
            if (pre_b[bb][2]) drain(w0);
            push_beat(bb);
            if (!skp_b[bb]) pop_avail(end_b[bb]);
        end
    endtask

    function integer lcount(input integer a, input integer z);
        integer q;
        begin
            lcount = 0;
            for (q = a; q < z; q = q + 1) if (exp_r[q][8]) lcount = lcount + 1;
        end
    endfunction

    task chk_clean_status(input [8*40-1:0] what);
        begin
            rd(A_STATUS); chk(rdat, 32'h0000_0006, what);
            rd(A_RXSTAT); chk(rdat, 32'h0000_0001, "RXSTATUS empty");
            rd(A_CYCLES); chk(rdat, 0, "CYCLES after clear");
        end
    endtask

    task frame_cycles_check(input integer nbeats);
        begin
`ifdef FRAMECHK
            rd(A_CYCLES);
            n_checks = n_checks + 1;
            if (rdat < nbeats || rdat > 400) $fatal(1, "FAIL %s: CYCLES=%0d for a %0d-beat frame", `NAME, rdat, nbeats);
            rd(A_STATUS);
            chk(rdat[4:3], 2'b10, "STATUS {DONE,BUSY} after a frame");
`else
            rd(A_CYCLES);
`endif
        end
    endtask

    initial begin
        if (!$value$plusargs("VEC=%s", vfile)) $fatal(1, "FAIL %s: no +VEC=<vectors.hex>", `NAME);
        if (!$value$plusargs("NLIM=%d", nlim)) nlim = MAXB;
        // ---------------------------------------------------------- flatten the vectors
        nb = 0; nexp = 0; pfirst = 0;
`ifdef FRAME
        $readmemh(vfile, vb);
        if (vb[0] !== 8'hA5) $fatal(1, "FAIL %s: not a vector file (header %h)", `NAME, vb[0]);
        nvec = vb[1] * 256 + vb[2];
        for (k = 1; k <= nvec; k = k + 1) begin
            nrec_n = vb[16*k];
            for (j = 0; j < nrec_n; j = j + 1) begin
                in_b[nb] = {(j == nrec_n - 1), vb[16*k + 1 + j]}; pre_b[nb] = 4'd0; skp_b[nb] = 1'b0;
                if (j == nrec_n - 1) begin
                    exp_r[nexp] = {1'b0, vb[16*k + 13]}; nexp = nexp + 1;
                    exp_r[nexp] = {1'b1, vb[16*k + 14]}; nexp = nexp + 1;
                end
                end_b[nb] = nexp; nb = nb + 1;
            end
            if (k == 1) pfirst = nrec_n;
        end
`else
        $readmemh(vfile, vw);
  `ifdef PITCH
        if (vw[0][31:24] !== 8'hA5) $fatal(1, "FAIL %s: not a vector file (header %h)", `NAME, vw[0]);
        nvec = vw[0][23:0];
        for (k = 0; k < nvec; k = k + 1) begin
            w = vw[4 + k];
            in_b[nb] = {w[8], w[7:0]}; pre_b[nb] = 4'd0; skp_b[nb] = 1'b0;
            if (w[9]) begin exp_r[nexp] = {w[10], w[18:11]}; nexp = nexp + 1; end
            end_b[nb] = nexp; nb = nb + 1;
        end
  `elsif KV
        if (vw[0] !== 32'hA5) $fatal(1, "FAIL %s: not a vector file (header %h)", `NAME, vw[0]);
        nvec = vw[1] * 256 + vw[2];
        carry = 0;
        for (k = 1; k <= nvec; k = k + 1) begin
            typ = vw[40*k]; nin = vw[40*k + 1]; nout = vw[40*k + 2];
            if (typ == 2) carry = carry | 1;                                  // RST_IDLE: CLEAR before the next beat
            else if (typ == 1 || typ == 3 || typ == 4) begin
                for (j = 0; j < nin; j = j + 1) begin
                    in_b[nb] = {(typ != 3 && j == nin - 1), vw[40*k + 12 + j][7:0]};
                    pre_b[nb] = carry | ((typ == 4 && j == 0) ? 4 : 0); carry = 0;
                    skp_b[nb] = (typ == 4);
                    if (typ == 1 && j == nin - 1)
                        for (b = 0; b < nout; b = b + 1) begin exp_r[nexp] = {(b == nout - 1), vw[40*k + 4 + b][7:0]}; nexp = nexp + 1; end
                    end_b[nb] = nexp; nb = nb + 1;
                end
                if (typ == 3) carry = carry | 1;                              // RST_MID: CLEAR after the partial frame
                if (typ == 4) carry = carry | 8;                              // RST_PEND: DONE, then CLEAR
            end else $fatal(1, "FAIL %s: record %0d has unknown type %0d", `NAME, k, typ);
            if (k == 1) begin
                if (typ != 1) $fatal(1, "FAIL %s: record 1 is not a COMMAND", `NAME);
                pfirst = nin;
            end
        end
        if (carry != 0) $fatal(1, "FAIL %s: the last record is a reset with no beat after it", `NAME);
  `else
        if (vw[0] !== 32'hA5A5A5A5) $fatal(1, "FAIL %s: not a vector file (header %h)", `NAME, vw[0]);
        nvec = vw[1];
        for (k = 0; k < nvec; k = k + 1) begin
            w = vw[3 + k];
            in_b[nb] = {w[8], w[7:0]}; pre_b[nb] = w[9] ? 4'd1 : w[20] ? 4'd2 : 4'd0; skp_b[nb] = 1'b0;
            if (w[10]) begin exp_r[nexp] = {w[19], w[18:11]}; nexp = nexp + 1; end
            end_b[nb] = nexp; nb = nb + 1;
        end
  `endif
`ifndef KV
        pfirst = 12;
`endif
`endif
        if (nb < 1 || nb >= MAXB) $fatal(1, "FAIL %s: bad beat count %0d", `NAME, nb);
        nbu = (nlim < nb) ? nlim : nb;
        if (pfirst > nbu) pfirst = nbu;

        // ---------------------------------------------------------- reset
        ne = 0; i = 0;
        repeat (3) @(negedge wb_clk_i);
        wb_rst_i = 1'b0;
        idle(2);
        mon_en = 1'b1;

        // ---------------------------------------------------------- 0. registers
        rd(A_ID);                chk(rdat, 32'h5354_5201, "ID");
        wb(1'b0, 4'b0001, A_ID, 0); chk(rdat, 32'h0000_0001, "ID sel 0001");
        wb(1'b0, 4'b0010, A_ID, 0); chk(rdat, 32'h0000_5200, "ID sel 0010");
        wb(1'b0, 4'b1100, A_ID, 0); chk(rdat, 32'h5354_0000, "ID sel 1100");
        wb(1'b0, 4'b0000, A_ID, 0); chk(rdat, 32'h0, "ID sel 0000");
        rd(A_CAPS);              chk(rdat, 32'h0010_1001, "CAPS");
        rd(A_CTRL);              chk(rdat, 0, "CTRL reset value");
        rd(A_STATUS);            chk(rdat, 32'h0000_0006, "STATUS after reset");
        rd(A_RXSTAT);            chk(rdat, 32'h0000_0001, "RXSTATUS after reset");
        rd(A_CYCLES);            chk(rdat, 0, "CYCLES after reset");
        wr(4'b0001, A_CTRL, 32'h0000_0001); rd(A_CTRL); chk(rdat, 1, "CTRL irq enable");
        wr(4'b0010, A_CTRL, 32'h0000_0100); rd(A_CTRL); chk(rdat, 1, "CTRL: CLEAR (lane 1) reads 0, irq enable kept");
        wr(4'b0001, A_CTRL, 32'h0000_0000); rd(A_CTRL); chk(rdat, 0, "CTRL irq disable");
        rd(32'h3000_0024);       chk(rdat, 0, "unmapped 0x24");
        rd(32'h3000_003C);       chk(rdat, 0, "unmapped 0x3C");
        rd(32'h3000_00FC);       chk(rdat, 0, "unmapped 0xFC");
        rd(A_TXDATA);            chk(rdat, 0, "TXDATA reads 0");
        rd(A_TXLAST);            chk(rdat, 0, "TXLAST reads 0");
        rd(32'h3000_0100);       chk(rdat, 0, "out of window 0x3000_0100");
        rd(32'h2000_0000);       chk(rdat, 0, "out of window 0x2000_0000");
        rd(32'h3100_0000);       chk(rdat, 0, "out of window 0x3100_0000");
        wr(4'hF, 32'h3000_0024, 32'hFFFF_FFFF);
        wr(4'hF, A_ID, 32'hFFFF_FFFF);
        wr(4'hF, A_STATUS, 32'hFFFF_FFFF);
        wr(4'hF, A_CAPS, 32'hFFFF_FFFF);
        wr(4'hF, 32'h3000_010C, 32'h0000_0055);          // TXDATA offset in another page: ignored
        wr(4'b0010, A_TXDATA, 32'h0000_0055);            // byte lane 0 not selected: ignored
        wr(4'b1110, A_TXLAST, 32'h0000_0055);
        idle(10);
        rd(A_STATUS);            chk(rdat, 32'h0000_0006, "STATUS after ignored writes");
        rd(A_ID);                chk(rdat, 32'h5354_5201, "ID after writes to it");
        rd(A_RXDATA);            chk(rdat, 0, "RXDATA while empty");
        rd(A_STATUS);            chk(rdat, 32'h0000_0046, "STATUS after RXDATA underflow (ERR_UF)");
        do_clear;
        chk_clean_status("STATUS after CLEAR");

        // ---------------------------------------------------------- 1. phase 0: irq disabled, first record
        for (b = 0; b < pfirst; b = b + 1) begin i = b; do_beat(b); end
        drain(end_b[pfirst - 1]);
        frame_cycles_check(pfirst);
        rd(A_STATUS);
        chk(rdat[6:5], 2'b00, "STATUS errors after phase 0");
        chk(irq_rises, 0, "irq while disabled");
        e0 = end_b[pfirst - 1];
        wr(4'b0001, A_CTRL, 32'h0000_0001); rd(A_CTRL); chk(rdat, 1, "CTRL irq enable");
        irq_exp = 0;

        // ---------------------------------------------------------- 2. burst until the TX FIFO is full
        b = pfirst; filled = 0; t = 0;
        while (!filled) begin
            if (b >= nbu || b - pfirst >= 300 || pre_b[b] != 4'd0 || skp_b[b])
                $fatal(1, "FAIL %s: burst ended at beat %0d without filling the TX FIFO", `NAME, b);
            i = b;
            rd(A_STATUS);
            if (rdat[0] && rdat[23:16] == 16) filled = 1;     // TX full and the engine stalled behind a full RX FIFO
            else if (!rdat[0]) begin push_beat(b); b = b + 1; end
            else begin t = t + 1; if (t > 400) $fatal(1, "FAIL %s: TX full but RX level %0d", `NAME, rdat[23:16]); end
        end
        chk(rdat[15:8], 16, "TX level when full");
        chk(rdat[6:5], 2'b00, "no error before the overflow");
        push_beat(b);                                            // dropped
        rd(A_STATUS);
        chk(rdat[5], 1, "ERR_OVF after a write while full");
        nclr0 = nclr;
        chk(rdat[15:8], 16, "TX level unchanged by the dropped write");
        // give the stalled engine room again: pop results until the TX FIFO has space
        t = 0;
        rd(A_STATUS);
        while (rdat[0]) begin
            pop_avail(end_b[b - 1]);
            t = t + 1; if (t > 400) $fatal(1, "FAIL %s: TX FIFO stays full after popping results", `NAME);
            rd(A_STATUS);
        end
        // ---------------------------------------------------------- 3. interleaved: the rest (beat b is sent again)
        for (; b < nbu; b = b + 1) begin i = b; do_beat(b); end
        drain(end_b[nbu - 1]);
        irq_exp = lcount(e0, end_b[nbu - 1]) + npend;   // a lost KV RST_PEND response also pulses irq once
        idle(10);
        chk(irq_hi, irq_rises, "irq pulses one clock wide");
        chk(irq_rises, irq_exp, "irq pulses = m_last beats captured");
        rd(A_STATUS); chk(rdat[6:5], (nclr == nclr0) ? 2'b01 : 2'b00, "ERR_OVF sticky until CLEAR, no ERR_UF");

        // ---------------------------------------------------------- 4. underflow, CLEAR in the middle of a run, replay
        rd(A_RXDATA);            chk(rdat, 0, "RXDATA while empty");
        rd(A_STATUS);            chk(rdat[6:5], (nclr == nclr0) ? 2'b11 : 2'b10, "ERR_UF set (and ERR_OVF if no CLEAR since)");
        do_clear;
        chk_clean_status("STATUS after CLEAR (errors, levels, cycles)");
        for (b = 0; b < 3 && b < pfirst - 1; b = b + 1) push_beat(b);
        do_clear;
        idle(5);
        chk_clean_status("STATUS after CLEAR in the middle of a frame");
        ne = 0;
        for (b = 0; b < pfirst; b = b + 1) begin i = b; do_beat(b); end
        drain(e0);
        frame_cycles_check(pfirst);
        irq_exp = irq_exp + lcount(0, e0);
        idle(10);
        chk(irq_rises, irq_exp, "irq pulses after the replay");
        $display("PASS %s: %0d input beats, %0d result beats checked (registers, burst/overflow, back-pressure, irq, CLEAR, replay), %0d bus transactions, %0d checks%0s",
                 `NAME, nbu, end_b[nbu - 1] + e0, n_tx, n_checks, (nbu < nb) ? " [input limited by NLIM]" : "");
        $finish;
    end
endmodule
`default_nettype wire
