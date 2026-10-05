// SPDX-License-Identifier: Apache-2.0
// Testbench for Caravel's user_project_wrapper holding one soc_image_text_match (mprj): body copied from designs/soc_image_text_match/tb/soc_image_text_match_tb.v, run through the wrapper ports (RTL and gate level): ports only, no hierarchical references, no parameters.
// Drives the Wishbone register map of shared/rtl/wb_stream_adapter.v. Vectors: +VEC=<file> (default for make simulate:
// designs/soc_image_text_match/tb/vectors.hex, a copy of designs/image_text_match/tb/vectors.hex, 2,079 cases; layout:
// header record A5 <count hi> <count lo>, then 16-byte records [0] n beats, [1..n] inputs, [13] result beat 0,
// [14] result beat 1). ALL cases are run.
// Per case: write n-1 beats to TXDATA and the last to TXLAST, wait for the one irq[0] pulse (odd cases) or poll
// RXSTATUS.DONE (even cases), read RXDATA twice and compare {valid, m_last, m_data}, check RX and TX empty, CYCLES in range.
// Also: ID, CAPS, CTRL, unmapped / out-of-window reads, ack exactly one clock wide, irq[2:1] = 0, one irq pulse per case,
// CLEAR in the middle of a frame, reset in the middle of a frame. Comparisons use !== so X never passes.
// First failure: $fatal(1, "FAIL ..."). Success: "PASS user_project_wrapper_soc_itm_tb: ...".
`timescale 1ns/1ps
module user_project_wrapper_soc_itm_tb;
    reg          wb_clk_i = 1'b0;
    reg          wb_rst_i = 1'b1;
    reg          wbs_stb_i = 1'b0, wbs_cyc_i = 1'b0, wbs_we_i = 1'b0;
    reg  [3:0]   wbs_sel_i = 4'd0;
    reg  [31:0]  wbs_dat_i = 32'd0, wbs_adr_i = 32'd0;
    wire         wbs_ack_o;
    wire [31:0]  wbs_dat_o;
    wire [2:0]   irq;

    reg  [127:0] la_data_in = 128'd0, la_oenb = {128{1'b1}};   // wrapper ports the design does not use
    reg  [37:0]  io_in = 38'd0;
    wire [127:0] la_data_out;
    wire [37:0]  io_out, io_oeb;
    reg          user_clock2 = 1'b0;

    user_project_wrapper dut (
        .wb_clk_i(wb_clk_i), .wb_rst_i(wb_rst_i),
        .wbs_stb_i(wbs_stb_i), .wbs_cyc_i(wbs_cyc_i), .wbs_we_i(wbs_we_i), .wbs_sel_i(wbs_sel_i),
        .wbs_dat_i(wbs_dat_i), .wbs_adr_i(wbs_adr_i), .wbs_ack_o(wbs_ack_o), .wbs_dat_o(wbs_dat_o),
        .la_data_in(la_data_in), .la_data_out(la_data_out), .la_oenb(la_oenb),
        .io_in(io_in), .io_out(io_out), .io_oeb(io_oeb),
        .user_clock2(user_clock2), .user_irq(irq));   // analog_io left unconnected

    always #5 wb_clk_i = ~wb_clk_i;

    localparam [31:0] A_ID = 32'h3000_0000, A_CTRL = 32'h3000_0004, A_STATUS = 32'h3000_0008, A_TXDATA = 32'h3000_000C,
                      A_TXLAST = 32'h3000_0010, A_RXDATA = 32'h3000_0014, A_RXSTAT = 32'h3000_0018,
                      A_CYCLES = 32'h3000_001C, A_CAPS = 32'h3000_0020;
    localparam integer MAXREC = 4096;

    reg  [7:0]  vec [0:16*MAXREC-1];
    reg  [1023:0] vfile;
    integer     nvec, k, j, n, n_checks = 0, cur = 0, reqs = 0, acks = 0, irq_rises = 0, irq_hi = 0, t, cyc;
    reg  [31:0] rdat;
    reg         mon_en = 1'b0, prev_ack = 1'b0, prev_irq = 1'b0;

    // monitors, sampled 2 ns after the falling edge
    always @(negedge wb_clk_i) begin
        #2;
        if (mon_en) begin
            if (wbs_ack_o !== 1'b0 && wbs_ack_o !== 1'b1) $fatal(1, "FAIL wbs_ack_o is X (t=%0t)", $time);
            if (wbs_ack_o === 1'b1) begin
                if (prev_ack) $fatal(1, "FAIL wbs_ack_o wider than one clock (t=%0t)", $time);
                acks = acks + 1;
                if (acks > reqs) $fatal(1, "FAIL wbs_ack_o without a request (t=%0t)", $time);
            end
            prev_ack = (wbs_ack_o === 1'b1);
            if (irq !== 3'b000 && irq !== 3'b001) $fatal(1, "FAIL irq=%b (X or irq[2:1] != 0) (t=%0t)", irq, $time);
            if (irq[0] === 1'b1) begin
                irq_hi = irq_hi + 1;
                if (!prev_irq) irq_rises = irq_rises + 1;
            end
            prev_irq = (irq[0] === 1'b1);
        end
    end

    initial begin
        #(64'd400000000);
        $fatal(1, "FAIL timeout: testbench did not finish");
    end

    task chk(input [63:0] got, input [63:0] want, input [8*40-1:0] what);
        begin
            n_checks = n_checks + 1;
            if (got !== want) $fatal(1, "FAIL case %0d %0s: got %h expected %h (t=%0t)", cur, what, got, want, $time);
        end
    endtask
    task idle(input integer c); begin repeat (c) @(negedge wb_clk_i); end endtask

    task wb(input we, input [3:0] sel, input [31:0] adr, input [31:0] dat);
        integer w;
        begin
            reqs = reqs + 1;
            wbs_cyc_i = 1'b1; wbs_stb_i = 1'b1; wbs_we_i = we; wbs_sel_i = sel; wbs_adr_i = adr; wbs_dat_i = dat;
            w = 0;
            @(negedge wb_clk_i);
            while (wbs_ack_o !== 1'b1) begin
                if (wbs_ack_o !== 1'b0) $fatal(1, "FAIL case %0d: wbs_ack_o is X during a request", cur);
                w = w + 1;
                if (w > 8) $fatal(1, "FAIL case %0d: no wbs_ack_o within 8 cycles (adr %h)", cur, adr);
                @(negedge wb_clk_i);
            end
            rdat = wbs_dat_o;
            wbs_cyc_i = 1'b0; wbs_stb_i = 1'b0; wbs_we_i = 1'b0; wbs_sel_i = 4'd0; wbs_adr_i = 32'd0; wbs_dat_i = 32'd0;
            @(negedge wb_clk_i);
            chk(wbs_ack_o, 0, "ack one clock wide");
        end
    endtask
    task rd(input [31:0] adr); wb(1'b0, 4'hF, adr, 32'd0); endtask
    task wr(input [3:0] sel, input [31:0] adr, input [31:0] dat); wb(1'b1, sel, adr, dat); endtask
    task do_clear; begin wr(4'b0010, A_CTRL, 32'h0000_0100); idle(3); end endtask

    task send(input integer r);
        integer b, m;
        begin
            m = vec[16*r];
            for (b = 0; b < m; b = b + 1)
                wr(4'hF, (b == m - 1) ? A_TXLAST : A_TXDATA, {24'd0, vec[16*r + 1 + b]});
        end
    endtask

    integer rises0;
    initial begin
        if (!$value$plusargs("VEC=%s", vfile)) $fatal(1, "FAIL no +VEC=<vectors.hex>");
        $readmemh(vfile, vec);
        if (vec[0] !== 8'hA5) $fatal(1, "FAIL not a vector file (header %h)", vec[0]);
        nvec = vec[1] * 256 + vec[2];
        if (nvec < 1 || nvec >= MAXREC) $fatal(1, "FAIL bad case count %0d", nvec);
        repeat (3) @(negedge wb_clk_i);
        wb_rst_i = 1'b0;
        idle(2);
        mon_en = 1'b1;

        // registers
        rd(A_ID);     chk(rdat, 32'h5354_5201, "ID");
        rd(A_CAPS);   chk(rdat, 32'h0010_1001, "CAPS");
        rd(A_CTRL);   chk(rdat, 0, "CTRL after reset");
        rd(A_STATUS); chk(rdat, 32'h0000_0006, "STATUS after reset");
        rd(32'h3000_0024); chk(rdat, 0, "unmapped offset");
        rd(32'h3000_0100); chk(rdat, 0, "out of window");
        rd(32'h2000_0000); chk(rdat, 0, "out of window (other region)");
        wr(4'b0001, A_CTRL, 32'd1); rd(A_CTRL); chk(rdat, 1, "CTRL irq enable");

        // every case
        for (k = 1; k <= nvec; k = k + 1) begin
            cur = k;
            rises0 = irq_rises;
            send(k);
            if (k % 2) begin                                    // wait for the irq[0] pulse
                t = 0;
                while (irq_rises == rises0) begin
                    @(negedge wb_clk_i); t = t + 1;
                    if (t > 400) $fatal(1, "FAIL case %0d: no irq[0] pulse", k);
                end
            end else begin                                      // poll RXSTATUS.DONE
                t = 0; rd(A_RXSTAT);
                while (rdat[2] !== 1'b1) begin
                    t = t + 1;
                    if (t > 400) $fatal(1, "FAIL case %0d: DONE never set", k);
                    rd(A_RXSTAT);
                end
            end
            rd(A_RXSTAT);  chk(rdat[15:8], 2, "RX level");
            rd(A_RXDATA);  chk(rdat, {22'd0, 2'b10, vec[16*k + 13]}, "RXDATA beat 0 {valid,last,data}");
            rd(A_RXDATA);  chk(rdat, {22'd0, 2'b11, vec[16*k + 14]}, "RXDATA beat 1 {valid,last,data}");
            rd(A_CYCLES);
            n_checks = n_checks + 1;
            if (rdat < vec[16*k] || rdat > 100) $fatal(1, "FAIL case %0d: CYCLES=%0d out of range", k, rdat);
            rd(A_STATUS);  chk(rdat, 32'h0000_0016, "STATUS {DONE, RX empty, TX empty}");
            idle(3);
            chk(irq_rises, rises0 + 1, "one irq pulse per case");
        end
        chk(irq_hi, irq_rises, "irq pulses one clock wide");
        chk(irq_rises, nvec, "irq pulses total");

        // CLEAR and reset in the middle of a frame, then a full case
        cur = 0;
        wr(4'hF, A_TXDATA, 32'd1); wr(4'hF, A_TXDATA, 32'd1);
        do_clear;
        rd(A_STATUS); chk(rdat, 32'h0000_0006, "STATUS after CLEAR mid-frame");
        cur = nvec; rises0 = irq_rises; send(nvec); idle(60);
        rd(A_RXDATA); chk(rdat, {22'd0, 2'b10, vec[16*nvec + 13]}, "beat 0 after CLEAR");
        rd(A_RXDATA); chk(rdat, {22'd0, 2'b11, vec[16*nvec + 14]}, "beat 1 after CLEAR");
        cur = 0;
        wr(4'hF, A_TXDATA, 32'd1);
        @(negedge wb_clk_i); wb_rst_i = 1'b1; @(negedge wb_clk_i); @(negedge wb_clk_i); wb_rst_i = 1'b0;
        idle(2);
        rd(A_STATUS); chk(rdat, 32'h0000_0006, "STATUS after reset mid-frame");
        rd(A_CTRL);   chk(rdat, 0, "CTRL after reset");
        cur = 1; send(1); idle(60);
        rd(A_RXDATA); chk(rdat, {22'd0, 2'b10, vec[16*1 + 13]}, "beat 0 after reset");
        rd(A_RXDATA); chk(rdat, {22'd0, 2'b11, vec[16*1 + 14]}, "beat 1 after reset");
        $display("PASS user_project_wrapper_soc_itm_tb: %0d cases (all of tb/vectors.hex: results, m_last, CYCLES, irq, DONE polling), registers, CLEAR/reset mid-frame, %0d checks",
                 nvec, n_checks);
        $finish;
    end
endmodule
