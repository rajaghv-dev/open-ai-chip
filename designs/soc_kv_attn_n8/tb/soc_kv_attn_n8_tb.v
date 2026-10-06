// SPDX-License-Identifier: Apache-2.0
// Testbench for soc_kv_attn_n8 (RTL now, gate level later): ports only, no hierarchical references, no parameters.
// Drives the Wishbone register map of shared/rtl/wb_stream_adapter.v. Vectors: +VEC=<file> (default for make simulate:
// designs/soc_kv_attn_n8/tb/vectors.hex, a copy of designs/kv_attn_n8/tb/vectors.hex; layout: header record
// A5 <records hi> <records lo> N bits ring version, then 40-byte records [0] type 01 COMMAND / 02 RST_IDLE / 03 RST_MID /
// 04 RST_PEND, [1] n_in, [2] n_out, [3] latency, [4..11] expected beats, [12..39] input beats; model/kv_attention/spec.md).
// ALL records are run, in order, through TXDATA / TXLAST / RXSTATUS / RXDATA / CTRL.CLEAR only:
//   COMMAND   n_in beats (TXLAST on the last), wait until the RX level is n_out, read n_out beats and compare
//             {valid, m_last, m_data}, STATUS = DONE + both FIFOs empty, CYCLES in range, one irq[0] pulse
//   RST_IDLE  CLEAR
//   RST_MID   the partial frame (no TXLAST), then CLEAR
//   RST_PEND  the whole frame (n_out is 0 in this record), wait for RXSTATUS.DONE and the irq pulse (the response sits in
//             the RX FIFO), then CLEAR (the response is lost by design)
// After every CLEAR: STATUS, RXSTATUS and CYCLES are the reset values. The next record must behave as on an empty cache.
// Also: ID, CAPS, CTRL, unmapped / out-of-window reads, ack exactly one clock wide, irq[2:1] = 0, no ERR_OVF / ERR_UF.
// Comparisons use !== so X never passes. First failure: $fatal(1, "FAIL ..."). Success: "PASS soc_kv_attn_n8_tb: ...".
`timescale 1ns/1ps
module soc_kv_attn_n8_tb;
    reg          wb_clk_i = 1'b0;
    reg          wb_rst_i = 1'b1;
    reg          wbs_stb_i = 1'b0, wbs_cyc_i = 1'b0, wbs_we_i = 1'b0;
    reg  [3:0]   wbs_sel_i = 4'd0;
    reg  [31:0]  wbs_dat_i = 32'd0, wbs_adr_i = 32'd0;
    wire         wbs_ack_o;
    wire [31:0]  wbs_dat_o;
    wire [2:0]   irq;

    soc_kv_attn_n8 dut (
        .wb_clk_i(wb_clk_i), .wb_rst_i(wb_rst_i),
        .wbs_stb_i(wbs_stb_i), .wbs_cyc_i(wbs_cyc_i), .wbs_we_i(wbs_we_i), .wbs_sel_i(wbs_sel_i),
        .wbs_dat_i(wbs_dat_i), .wbs_adr_i(wbs_adr_i), .wbs_ack_o(wbs_ack_o), .wbs_dat_o(wbs_dat_o),
        .irq(irq));
    always #5 wb_clk_i = ~wb_clk_i;

    localparam [31:0] A_ID = 32'h3000_0000, A_CTRL = 32'h3000_0004, A_STATUS = 32'h3000_0008, A_TXDATA = 32'h3000_000C,
                      A_TXLAST = 32'h3000_0010, A_RXDATA = 32'h3000_0014, A_RXSTAT = 32'h3000_0018,
                      A_CYCLES = 32'h3000_001C, A_CAPS = 32'h3000_0020;
    localparam integer MAXREC = 3000;

    reg  [7:0]  vec [0:40*(MAXREC+1)-1];
    reg  [1023:0] vfile;
    integer     fd, h0, h1, h2, h3, h4, h5, h6;
    reg  [8*512-1:0] line;
    integer     nvec, k, b, typ, nin, nout, lat, n_checks = 0, cur = 0, reqs = 0, acks = 0, irq_rises = 0, irq_hi = 0, t;
    integer     n_cmd = 0, n_clr = 0, n_pend = 0, rises0;
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
        #(64'd1000000000);
        $fatal(1, "FAIL timeout: testbench did not finish");
    end

    task chk(input [63:0] got, input [63:0] want, input [8*40-1:0] what);
        begin
            n_checks = n_checks + 1;
            if (got !== want) $fatal(1, "FAIL record %0d %0s: got %h expected %h (t=%0t)", cur, what, got, want, $time);
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
                if (wbs_ack_o !== 1'b0) $fatal(1, "FAIL record %0d: wbs_ack_o is X during a request", cur);
                w = w + 1;
                if (w > 8) $fatal(1, "FAIL record %0d: no wbs_ack_o within 8 cycles (adr %h)", cur, adr);
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
    task do_clear;
        begin
            wr(4'b0010, A_CTRL, 32'h0000_0100); idle(3);
            rd(A_STATUS); chk(rdat, 32'h0000_0006, "STATUS after CLEAR");
            rd(A_RXSTAT); chk(rdat, 32'h0000_0001, "RXSTATUS after CLEAR");
            rd(A_CYCLES); chk(rdat, 0, "CYCLES after CLEAR");
        end
    endtask

    // first nb beats of record r; TXLAST on the last one when fin is set
    task send(input integer r, input integer nb, input fin);
        integer q;
        begin
            for (q = 0; q < nb; q = q + 1)
                wr(4'hF, (fin && q == nb - 1) ? A_TXLAST : A_TXDATA, {24'd0, vec[40*r + 12 + q]});
        end
    endtask

    // wait until the response of record r (n_out beats) sits in the RX FIFO
    task wait_resp(input integer r, input integer m);
        integer w;
        begin
            w = 0; rd(A_RXSTAT);
            while (rdat[15:8] !== m) begin
                w = w + 1;
                if (w > 400) $fatal(1, "FAIL record %0d: RX level %0d, expected %0d", r, rdat[15:8], m);
                rd(A_RXSTAT);
            end
        end
    endtask

    initial begin
        if (!$value$plusargs("VEC=%s", vfile)) $fatal(1, "FAIL no +VEC=<vectors.hex>");
        // header record = first line that is not a comment: A5, records hi/lo, N, bits, ring, version
        fd = $fopen(vfile, "r");
        if (fd == 0) $fatal(1, "FAIL cannot open %0s", vfile);
        h0 = 0;
        while (h0 == 0 && !$feof(fd)) begin
            if ($fgets(line, fd) && $sscanf(line, "%h %h %h %h %h %h %h", h0, h1, h2, h3, h4, h5, h6) != 7) h0 = 0;
        end
        $fclose(fd);
        if (h0 !== 32'hA5) $fatal(1, "FAIL not a vector file (header %h)", h0);
        if (h3 !== 8 || h4 !== 8 || h5 !== 0)
            $fatal(1, "FAIL header N=%0d bits=%0d ring=%0d is not kv_attn_n8", h3, h4, h5);
        nvec = h1 * 256 + h2;
        if (nvec < 1 || nvec > MAXREC) $fatal(1, "FAIL bad record count %0d", nvec);
        $readmemh(vfile, vec, 0, 40 * (nvec + 1) - 1);
        repeat (3) @(negedge wb_clk_i);
        wb_rst_i = 1'b0;
        idle(2);
        mon_en = 1'b1;

        // registers
        rd(A_ID);     chk(rdat, 32'h5354_5201, "ID");
        rd(A_CAPS);   chk(rdat, 32'h0010_1001, "CAPS");
        rd(A_CTRL);   chk(rdat, 0, "CTRL after reset");
        rd(A_STATUS); chk(rdat, 32'h0000_0006, "STATUS after reset");
        rd(A_RXSTAT); chk(rdat, 32'h0000_0001, "RXSTATUS after reset");
        rd(A_CYCLES); chk(rdat, 0, "CYCLES after reset");
        rd(32'h3000_0024); chk(rdat, 0, "unmapped offset");
        rd(32'h3000_0100); chk(rdat, 0, "out of window");
        rd(32'h2000_0000); chk(rdat, 0, "out of window (other region)");
        wr(4'b0001, A_CTRL, 32'd1); rd(A_CTRL); chk(rdat, 1, "CTRL irq enable");
        wr(4'b0010, A_CTRL, 32'h0000_0100); rd(A_CTRL); chk(rdat, 1, "CTRL: CLEAR reads 0, irq enable kept");

        // every record
        for (k = 1; k <= nvec; k = k + 1) begin
            cur = k;
            typ = vec[40*k]; nin = vec[40*k + 1]; nout = vec[40*k + 2]; lat = vec[40*k + 3];
            rises0 = irq_rises;
            case (typ)
                1: begin                                            // COMMAND
                    send(k, nin, 1'b1);
                    wait_resp(k, nout);
                    for (b = 0; b < nout; b = b + 1) begin
                        rd(A_RXDATA);
                        chk(rdat, {22'd0, 1'b1, (b == nout - 1), vec[40*k + 4 + b]}, "RXDATA {valid,last,data}");
                    end
                    rd(A_RXSTAT); chk(rdat, 32'h0000_0005, "RXSTATUS empty, DONE");
                    rd(A_STATUS); chk(rdat, 32'h0000_0016, "STATUS {DONE, RX empty, TX empty}");
                    rd(A_CYCLES);
                    n_checks = n_checks + 1;
                    if (rdat < nin + nout || rdat > 4 * nin + lat + nout + 40)
                        $fatal(1, "FAIL record %0d: CYCLES=%0d out of range (n_in %0d, latency %0d, n_out %0d)", k, rdat, nin, lat, nout);
                    idle(3);
                    chk(irq_rises, rises0 + 1, "one irq pulse per command");
                    n_cmd = n_cmd + 1;
                end
                2: begin do_clear; n_clr = n_clr + 1; end           // RST_IDLE
                3: begin send(k, nin, 1'b0); do_clear; n_clr = n_clr + 1; end   // RST_MID
                4: begin                                            // RST_PEND
                    send(k, nin, 1'b1);
                    t = 0; rd(A_RXSTAT);                            // n_out is 0 in this record: wait for DONE (m_last captured)
                    while (rdat[2] !== 1'b1) begin
                        t = t + 1;
                        if (t > 400) $fatal(1, "FAIL record %0d: DONE never set", k);
                        rd(A_RXSTAT);
                    end
                    chk(rdat[15:8] != 0, 1, "response waiting in the RX FIFO");
                    idle(3);
                    chk(irq_rises, rises0 + 1, "irq pulse of the pending response");
                    do_clear; n_clr = n_clr + 1; n_pend = n_pend + 1;
                end
                default: $fatal(1, "FAIL record %0d: unknown record type %0d", k, typ);
            endcase
        end
        chk(irq_hi, irq_rises, "irq pulses one clock wide");
        chk(irq_rises, n_cmd + n_pend, "irq pulses total");
        rd(A_STATUS); chk(rdat[6:5], 2'b00, "STATUS errors at the end (no ERR_OVF, no ERR_UF)");
        $display("PASS soc_kv_attn_n8_tb: %0d records (all of tb/vectors.hex: %0d commands with every response beat, m_last, CYCLES, irq; %0d CLEARs of which %0d with a response pending), registers, %0d checks",
                 nvec, n_cmd, n_clr, n_pend, n_checks);
        $finish;
    end
endmodule
