// SPDX-License-Identifier: Apache-2.0
// Self-checking testbench for designs/audio_onset: every beat of tb/vectors.hex (model/audio_onset/gen_rom.py, record
// layout there). Ports only, so it runs unchanged on the RTL and on gate-level netlists. Run with
// +VEC=designs/audio_onset/tb/vectors.hex.
//
// The vector file is one long stream of 68,829 input beats: a de Bruijn sequence in which each of the 65,536 four-sample
// windows occurs exactly once (every window content is therefore checked, as the newest window of some beat), then
// short streams, s_last everywhere, error samples, generated onset sequences and resets. The expected m_data / m_last
// of every output beat come from golden.py. The whole file is played three times with different pacing:
//   pass 0  full rate: no gaps, m_ready always 1; also checks s_ready always 1 and the exact latency (result in the
//           cycle after the input beat, none otherwise)
//   pass 1  random input gaps (with garbage on s_data/s_last while s_valid is low) and random output stalls
//   pass 2  heavy output back-pressure (m_ready low 3 cycles in 4)
// Every cycle: s_ready === (!m_valid | m_ready); a stalled result holds m_valid, m_data, m_last; no result without an
// accepted input beat; results arrive in order; X never passes (!== everywhere). Output beats are compared with golden.
// Reset: between beats ("drain-reset") and with a result waiting ("stall-reset", the result is discarded).
// First failure: $fatal(1, "FAIL ..."). Success: "PASS audio_onset_tb: ...".
`timescale 1ns/1ps
module audio_onset_tb;
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

    audio_onset dut (.clk(clk), .rst(rst), .s_valid(s_valid), .s_data(s_data), .s_last(s_last), .s_ready(s_ready),
                     .m_valid(m_valid), .m_data(m_data), .m_last(m_last), .m_ready(m_ready));

    always #5 clk = ~clk;

    localparam integer MAXV = 131072;
    reg  [31:0] vec [0:MAXV-1];
    reg  [7:0]  exp_d [0:MAXV-1];       // queue of expected output beats, in order
    reg         exp_l [0:MAXV-1];
    reg  [8*512-1:0] vfile;      // up to 512 characters of path
    integer nbeats, nexp, exp_count, n_recv, n_checks, seed, pass, i, total_out, total_in, cyc;
    integer gap_pct, stall_pct;
    // current beat being offered (for the expected-output queue)
    reg        cur_ev;
    reg  [7:0] cur_ed;
    reg        cur_el;
    // tracking for the stall hold rule and the full-rate latency rule
    reg        held_valid, pend;
    reg  [7:0] held_d;
    reg        held_l;
    reg        lat_check;

    initial begin
        #(64'd400000000);
        $fatal(1, "FAIL timeout: testbench did not finish");
    end

    // One clock cycle: inputs change on the falling edge, are sampled by the DUT at the next rising edge, outputs and the
    // transfer decisions are evaluated 1 ns after the falling edge (settled, also for gate-level delays).
    task cycle(input sv, input [7:0] sd, input sl, input mr, output accepted);
        begin
            @(negedge clk);
            s_valid = sv; s_data = sd; s_last = sl; m_ready = mr;
            #1;
            cyc = cyc + 1;
            n_checks = n_checks + 1;
            if (s_ready !== (~m_valid | mr) || (^{s_ready, m_valid}) === 1'bx)
                $fatal(1, "FAIL pass %0d t=%0t: s_ready=%b m_valid=%b m_ready=%b (want s_ready = !m_valid | m_ready)",
                       pass, $time, s_ready, m_valid, mr);
            if (held_valid && (m_valid !== 1'b1 || m_data !== held_d || m_last !== held_l))
                $fatal(1, "FAIL pass %0d t=%0t: stalled result changed: m_valid=%b m_data=%h m_last=%b, held %h/%b",
                       pass, $time, m_valid, m_data, m_last, held_d, held_l);
            if (lat_check && m_valid !== pend)
                $fatal(1, "FAIL pass %0d t=%0t: latency: m_valid=%b, expected %b (result due exactly 1 cycle after the beat)",
                       pass, $time, m_valid, pend);
            if (m_valid === 1'b1 && n_recv >= exp_count)
                $fatal(1, "FAIL pass %0d t=%0t: unexpected output beat %h (%0d received, %0d due)",
                       pass, $time, m_data, n_recv, exp_count);
            if (m_valid === 1'b1 && mr) begin
                n_checks = n_checks + 2;
                if (m_data !== exp_d[n_recv])
                    $fatal(1, "FAIL pass %0d output beat %0d: m_data=%h expected %h (t=%0t)", pass, n_recv, m_data, exp_d[n_recv], $time);
                if (m_last !== exp_l[n_recv])
                    $fatal(1, "FAIL pass %0d output beat %0d: m_last=%b expected %b (t=%0t)", pass, n_recv, m_last, exp_l[n_recv], $time);
                n_recv = n_recv + 1;
            end
            held_valid = (m_valid === 1'b1) && !mr;
            held_d = m_data; held_l = m_last;
            accepted = sv && (s_ready === 1'b1);
            pend = 1'b0;
            if (accepted) begin
                total_in = total_in + 1;
                if (cur_ev) begin
                    exp_d[exp_count] = cur_ed; exp_l[exp_count] = cur_el;
                    exp_count = exp_count + 1;
                    pend = 1'b1;
                end
            end
        end
    endtask

    function pick_mr(input integer dummy);
        begin
            pick_mr = (pass == 0) ? 1'b1 : (($random(seed) & 32'h7fffffff) % 100 >= stall_pct);
        end
    endfunction

    reg acc_dummy;
    task idle_cycle;
        begin
            cur_ev = 1'b0;
            cycle(1'b0, $random(seed), $random(seed), pick_mr(0), acc_dummy);
        end
    endtask

    task drain;
        integer guard;
        begin
            guard = 0;
            while (n_recv != exp_count || m_valid !== 1'b0) begin
                idle_cycle;
                guard = guard + 1;
                if (guard > 2000) $fatal(1, "FAIL pass %0d: results not delivered (%0d of %0d)", pass, n_recv, exp_count);
            end
        end
    endtask

    task do_reset;
        begin
            @(negedge clk); rst = 1'b1; s_valid = 1'b0; s_last = 1'b0; m_ready = 1'b0;
            @(negedge clk); @(negedge clk); rst = 1'b0;
            #1;
            n_checks = n_checks + 1;
            if (s_ready !== 1'b1 || m_valid !== 1'b0)
                $fatal(1, "FAIL pass %0d after reset: s_ready=%b m_valid=%b", pass, s_ready, m_valid);
            n_recv = exp_count;          // a result lost to the reset is not expected any more
            held_valid = 1'b0; pend = 1'b0;
        end
    endtask

    reg [31:0] w;
    reg        offered, accepted;
    integer    guard, k, fd, nh, hw;
    reg [31:0] hdr [0:2];
    reg [8*256-1:0] line;
    initial begin
        n_checks = 0; seed = 32'h0A11; total_out = 0; total_in = 0; cyc = 0;
        if (!$value$plusargs("VEC=%s", vfile)) $fatal(1, "FAIL no +VEC=<vectors.hex>");
        // header: the first three non-comment lines are the magic, the beat count and the output count
        fd = $fopen(vfile, "r");
        if (fd == 0) $fatal(1, "FAIL cannot open %0s", vfile);
        nh = 0;
        while (nh < 3 && !$feof(fd)) begin
            if ($fgets(line, fd) && $sscanf(line, "%h", hw) == 1) begin hdr[nh] = hw; nh = nh + 1; end
        end
        $fclose(fd);
        if (nh != 3 || hdr[0] !== 32'hA5A5A5A5) $fatal(1, "FAIL %0s: not a vector file (header %h)", vfile, hdr[0]);
        nbeats = hdr[1]; nexp = hdr[2];
        if (nbeats < 1 || nbeats + 3 > MAXV) $fatal(1, "FAIL bad beat count %0d", nbeats);
        $readmemh(vfile, vec, 0, nbeats + 2);
        for (pass = 0; pass < 3; pass = pass + 1) begin
            gap_pct   = (pass == 1) ? 25 : 0;
            stall_pct = (pass == 1) ? 25 : (pass == 2) ? 75 : 0;
            exp_count = 0; n_recv = 0; held_valid = 1'b0; pend = 1'b0; lat_check = 1'b0;
            do_reset;
            lat_check = (pass == 0);
            for (i = 0; i < nbeats; i = i + 1) begin
                w = vec[3 + i];
                if (w[9]) begin
                    drain; do_reset;
                end else if (w[20]) begin
                    // hold the output back until the previous beat's result is waiting, then reset: it is lost
                    lat_check = 1'b0; cur_ev = 1'b0; guard = 0;
                    cycle(1'b0, 8'd0, 1'b0, 1'b0, acc_dummy);
                    while (m_valid !== 1'b1) begin
                        cycle(1'b0, 8'd0, 1'b0, 1'b0, acc_dummy);
                        guard = guard + 1;
                        if (guard > 10) $fatal(1, "FAIL pass %0d beat %0d: no result waiting for the stall-reset", pass, i);
                    end
                    do_reset;
                    lat_check = (pass == 0);
                end
                cur_ev = w[10]; cur_ed = w[18:11]; cur_el = w[19];
                offered = 1'b0; accepted = 1'b0; guard = 0;
                while (!accepted) begin
                    if (!offered && gap_pct != 0 && (($random(seed) & 32'h7fffffff) % 100 < gap_pct)) begin
                        cur_ev = 1'b0;        // gap: s_valid low, garbage on data and last
                        cycle(1'b0, $random(seed), $random(seed), pick_mr(0), acc_dummy);
                        cur_ev = w[10];
                    end else begin
                        offered = 1'b1;
                        cycle(1'b1, w[7:0], w[8], pick_mr(0), accepted);
                    end
                    guard = guard + 1;
                    if (guard > 5000) $fatal(1, "FAIL pass %0d beat %0d: input never accepted", pass, i);
                end
            end
            drain;
            lat_check = 1'b0;
            // nothing more may come out
            for (k = 0; k < 20; k = k + 1) idle_cycle;
            if (n_recv !== exp_count) $fatal(1, "FAIL pass %0d: %0d results received, %0d expected", pass, n_recv, exp_count);
            total_out = total_out + n_recv;
        end
        $display("PASS audio_onset_tb: %0d beats x 3 passes (all 65536 windows, short streams, errors, resets; full-rate, gaps, stalls), %0d output beats compared, %0d checks",
                 nbeats, total_out, n_checks);
        $finish;
    end
endmodule
