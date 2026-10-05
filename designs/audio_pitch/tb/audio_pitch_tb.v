// SPDX-License-Identifier: Apache-2.0
// Self-checking testbench for designs/audio_pitch (streaming: no frames). Ports only, so it also runs unchanged on the
// synthesised and routed gate-level netlists. Run with +VEC=designs/audio_pitch/tb/vectors.hex (model/audio_pitch/gen_rom.py).
//
// One word per input beat (layout in gen_rom.py): sample, s_last, and the result beat that must follow it, if any.
// The whole file is sent three times: with random input gaps and output stalls; at full rate (m_ready always high,
// s_ready must then never be low); and again with other random gaps. Checked on every cycle and result beat:
//   - every result beat equals golden.py's, in order, and none appears before its input beat was accepted;
//   - no result beat that was not expected (checked after each pass too), and none missing;
//   - while m_valid is high and m_ready low, m_valid, m_data and m_last hold;
//   - s_valid low: s_data and s_last are driven with garbage and must be ignored;
//   - reset in the middle of a recording (history must be cleared) and with a result waiting.
// Comparisons use !== so X never passes. First failure: $fatal(1, "FAIL ..."). Success: "PASS audio_pitch_tb: ...".
`timescale 1ns/1ps
`define DUT audio_pitch
module audio_pitch_tb;
    reg        clk = 1'b0;
    reg        rst = 1'b1;
    reg        s_valid = 1'b0;
    reg  [7:0] s_data = 8'd0;
    reg        s_last = 1'b0;
    wire       s_ready;
    wire       m_valid;
    wire [7:0] m_data;
    wire       m_last;
    reg        m_ready = 1'b0;

    `DUT dut (.clk(clk), .rst(rst), .s_valid(s_valid), .s_data(s_data), .s_last(s_last), .s_ready(s_ready),
              .m_valid(m_valid), .m_data(m_data), .m_last(m_last), .m_ready(m_ready));

    always #5 clk = ~clk;

    localparam integer MAXW = 16384;
    reg  [31:0] vec [0:MAXW-1];
    reg  [31:0] hdr [0:3];
    reg  [8*256-1:0] line;
    reg  [31:0] hv;
    integer     hn;
    integer     fd, nbeats, nrec, seg0, seg1, k, seed, n_checks, n_results;
    integer     nacc;            // index (into vec) of the next input beat to be accepted
    integer     ne;              // index (into vec) of the next input beat whose expected result has not arrived yet
    integer     hi;              // end of the current range
    reg  [1023:0] vfile;
    reg         held_valid, held_last, taken;
    reg  [7:0]  held_data;
    reg         running;
    integer     gapmode, stallmode;
    integer     cyc;

    initial begin
        #(64'd400000000);
        $fatal(1, "FAIL timeout: testbench did not finish");
    end

    task check_eq(input [31:0] got, input [31:0] want, input [127:0] what);
        begin
            n_checks = n_checks + 1;
            if (got !== want) $fatal(1, "FAIL %0s: got %h expected %h (beat %0d, t=%0t)", what, got, want, nacc - 4, $time);
        end
    endtask

    // one clock cycle of stimulus + observation. Inputs change on the falling edge; the handshake is sampled 1 ns
    // before the rising edge, after everything has settled.
    task cycle(input integer from, input integer to, input send_en);
        begin
            @(negedge clk);
            cyc = cyc + 1;
            if (taken) begin s_valid = 1'b0; taken = 1'b0; end
            if (!s_valid) begin
                // idle: garbage on data and last, which must be ignored
                s_data = $random(seed); s_last = $random(seed);
                if (send_en && nacc < to && (gapmode == 0 || ($random(seed) & 3) != 0)) begin
                    s_valid = 1'b1; s_data = vec[nacc][7:0]; s_last = vec[nacc][8];
                end
            end
            m_ready = (stallmode == 0) ? 1'b1 : (stallmode == 1) ? (($random(seed) & 3) != 0) : 1'b0;
            #4;
            if (s_ready !== 1'b0 && s_ready !== 1'b1) $fatal(1, "FAIL s_ready is %b (t=%0t)", s_ready, $time);
            if (m_valid !== 1'b0 && m_valid !== 1'b1) $fatal(1, "FAIL m_valid is %b (t=%0t)", m_valid, $time);
            if (stallmode == 0 && s_ready !== 1'b1) $fatal(1, "FAIL s_ready low although m_ready is high (t=%0t)", $time);
            // output stability under back-pressure
            if (held_valid && (m_valid !== 1'b1 || m_data !== held_data || m_last !== held_last))
                $fatal(1, "FAIL output changed while stalled: m_valid=%b m_data=%h m_last=%b, held %h/%b (t=%0t)",
                       m_valid, m_data, m_last, held_data, held_last, $time);
            held_valid = (m_valid === 1'b1) && !m_ready;
            held_data = m_data; held_last = m_last;
            // result beat taken at the coming edge
            if (m_valid === 1'b1 && m_ready) begin
                while (ne < hi && !vec[ne][9]) ne = ne + 1;
                if (ne >= nacc || ne >= hi)
                    $fatal(1, "FAIL unexpected result beat %h (no result due; beats accepted so far %0d, t=%0t)", m_data, nacc - 4, $time);
                check_eq(m_data, {24'd0, vec[ne][18:11]}, "result m_data {error,class,count}");
                check_eq({31'd0, m_last}, {31'd0, vec[ne][10]}, "result m_last");
                n_results = n_results + 1;
                ne = ne + 1;
            end
            // input beat taken at the coming edge
            if (s_valid && s_ready === 1'b1) begin nacc = nacc + 1; taken = 1'b1; end
        end
    endtask

    task do_reset;
        begin
            @(negedge clk); rst = 1'b1; s_valid = 1'b0; s_last = 1'b0; m_ready = 1'b0; taken = 1'b0; held_valid = 1'b0;
            @(negedge clk); @(negedge clk); rst = 1'b0;
            check_eq({31'd0, s_ready}, 32'd1, "s_ready after reset");
            check_eq({31'd0, m_valid}, 32'd0, "m_valid after reset");
        end
    endtask

    // send vec[from..to) and, if drain, wait until every expected result beat has been taken and nothing else comes
    task run_range(input integer from, input integer to, input integer gm, input integer sm, input drain);
        integer t, idle;
        begin
            gapmode = gm; stallmode = sm; hi = to; nacc = from; ne = from; taken = 1'b0; t = 0;
            while (nacc < to) begin
                cycle(from, to, 1'b1);
                t = t + 1;
                if (t > 40 * (to - from) + 100) $fatal(1, "FAIL stuck: beats accepted %0d of %0d", nacc - from, to - from);
            end
            if (drain) begin
                stallmode = (sm == 0) ? 0 : 1; gapmode = gm;
                idle = 0; t = 0;
                while (idle < 12) begin
                    cycle(from, to, 1'b0);
                    while (ne < to && !vec[ne][9]) ne = ne + 1;
                    if (ne >= to && m_valid !== 1'b1) idle = idle + 1; else idle = 0;
                    t = t + 1;
                    if (t > 2000) $fatal(1, "FAIL result beat missing (expected beat %0d)", ne - from);
                end
                check_eq({31'd0, m_valid}, 32'd0, "m_valid after drain");
                s_valid = 1'b0;
            end
        end
    endtask

    integer pass;
    initial begin
        n_checks = 0; n_results = 0; seed = 32'h51A7; cyc = 0; held_valid = 1'b0; taken = 1'b0; gapmode = 0; stallmode = 0;
        if (!$value$plusargs("VEC=%s", vfile)) $fatal(1, "FAIL no +VEC=<vectors.hex>");
        fd = $fopen(vfile, "r");
        if (fd == 0) $fatal(1, "FAIL cannot open %0s", vfile);
        // header first (the first four non-comment lines): it gives the file length
        hn = 0;
        while (hn < 4 && !$feof(fd)) begin
            if ($fgets(line, fd) && $sscanf(line, "%h", hv) == 1) begin hdr[hn] = hv; hn = hn + 1; end
        end
        $fclose(fd);
        if (hn != 4) $fatal(1, "FAIL %0s: header incomplete", vfile);
        if (hdr[0][31:24] !== 8'hA5) $fatal(1, "FAIL %0s: not a vector file (header %h)", vfile, hdr[0]);
        nbeats = hdr[0][23:0]; nrec = hdr[1]; seg0 = hdr[2]; seg1 = hdr[3];
        if (nbeats < 1 || 4 + nbeats > MAXW || seg1 !== 4 + nbeats) $fatal(1, "FAIL bad vector header");
        $readmemh(vfile, vec, 0, 4 + nbeats - 1);
        do_reset;
        // three passes over the whole file: random gaps/stalls, full rate, random again
        run_range(4, 4 + nbeats, 1, 1, 1'b1);
        run_range(4, 4 + nbeats, 0, 0, 1'b1);
        run_range(4, 4 + nbeats, 1, 1, 1'b1);
        // reset in the middle of a recording: 5 samples (still warming up), reset, then the whole recording; the
        // history must be gone, so the first result comes only after the 8th sample (an early one is "unexpected")
        run_range(seg0, seg0 + 5, 0, 0, 1'b0);
        do_reset;
        run_range(seg0, seg1, 1, 1, 1'b1);
        // reset in the middle of a recording after the first result beat has been taken
        run_range(seg0, seg0 + 10, 0, 0, 1'b1);
        do_reset;
        run_range(seg0, seg1, 1, 1, 1'b1);
        // reset while a result is waiting (m_ready held low), then the whole recording again
        run_range(seg0, seg0 + 8, 0, 2, 1'b0);
        @(negedge clk); @(negedge clk);
        check_eq({31'd0, m_valid}, 32'd1, "m_valid while stalled");
        check_eq({31'd0, s_ready}, 32'd0, "s_ready while the result register is full");
        do_reset;
        run_range(seg0, seg1, 0, 1, 1'b1);
        $display("PASS audio_pitch_tb: %0d recordings x3 passes, %0d result beats, %0d checks (values, order, stalls, full rate, garbage when idle, error flag, s_last, reset)",
                 nrec, n_results, n_checks);
        $finish;
    end
endmodule
