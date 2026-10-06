// SPDX-License-Identifier: Apache-2.0
// kv_attn_tb.vh -- body of the self-checking testbench of the kv_attn_* engines. Included inside a module by
// designs/kv_attn_<v>/tb/kv_attn_<v>_tb.v after `define DUT, EXP_N, EXP_BITS, EXP_RING. Ports only: it runs unchanged
// on the RTL and on gate-level netlists.
//
// Vectors: +VEC=<file> (designs/kv_attn_<v>/tb/vectors.hex, model/kv_attention/gen.py, spec.md section 9): a header
// record (A5, records hi/lo, N, KV bits, ring, version) and 40-word records: [0] type 01 COMMAND / 02 RST_IDLE /
// 03 RST_MID / 04 RST_PEND, [1] n_in, [2] n_out, [3] latency, [4..11] expected beats, [12..39] input beats.
// Checked: header against the DUT's parameters; every output beat (!==, so X never passes); m_last on the last beat
// only; the latency (edges from the last input beat to the first m_valid, as in stream_tb.vh) exactly; m_valid low
// before the frame is complete; s_ready low from the last input beat until the last response beat is taken, and high
// right after; m_valid, m_data and m_last held while m_ready is low; reset when idle / in the middle of a frame / with
// a response pending (the next record must then behave as on an empty cache). Input beats come with random
// s_valid gaps, output beats with random m_ready stalls. First failure: $fatal(1, "FAIL ..."). Success: "PASS ...".
`define STR(x) `"x`"

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

    localparam integer MAXREC = 3000;
    reg  [7:0] vec [0:40*(MAXREC+1)-1];
    integer    fd, h0, h1, h2, h3, h4, h5, h6;
    reg  [8*512-1:0] line;
    integer    nvec, n_checks, n_cmd, n_rst, seed, k;
    reg  [1023:0] vfile;

    // cycle counter and first-m_valid capture (sampled at the falling edge, half a cycle after the flops switch)
    integer cyc = 0;
    always @(posedge clk) cyc <= cyc + 1;
    integer t_last, t_mv;
    reg     mv_seen;
    always @(negedge clk) if (m_valid === 1'b1 && !mv_seen) begin mv_seen = 1'b1; t_mv = cyc; end

    // output stability under back-pressure
    reg       held_valid = 1'b0;
    reg [7:0] held_data;
    reg       held_last;
    always @(negedge clk) #1 begin
        if (held_valid && !rst) begin
            if (m_valid !== 1'b1 || m_data !== held_data || m_last !== held_last)
                $fatal(1, "FAIL output changed while stalled: m_valid=%b m_data=%h m_last=%b, held %h/%b (t=%0t)",
                       m_valid, m_data, m_last, held_data, held_last, $time);
        end
        held_valid = (m_valid === 1'b1) && !m_ready && !rst;
        held_data  = m_data;
        held_last  = m_last;
    end

    // hard timeout (simulated time, ns)
    initial begin
        #(64'd400000000);
        $fatal(1, "FAIL timeout: testbench did not finish");
    end

    task check8(input [7:0] got, input [7:0] want, input [127:0] what, input integer idx);
        begin
            n_checks = n_checks + 1;
            if (got !== want)
                $fatal(1, "FAIL record %0d %0s: got %h expected %h (t=%0t)", idx, what, got, want, $time);
        end
    endtask

    task do_reset;
        begin
            @(negedge clk); rst = 1'b1; s_valid = 1'b0; s_last = 1'b0; m_ready = 1'b0;
            @(negedge clk); @(negedge clk); rst = 1'b0;
            if (s_ready !== 1'b1 || m_valid !== 1'b0)
                $fatal(1, "FAIL after reset: s_ready=%b m_valid=%b", s_ready, m_valid);
            n_checks = n_checks + 1;
        end
    endtask

    // drive the first nb beats of record r (random s_valid gaps); with s_last on beat n_in-1 when fin is set.
    // Inputs change on the falling edge, the beat is taken at the next rising edge.
    task send(input integer r, input integer nb, input fin);
        integer b;
        begin
            b = 0; mv_seen = 1'b0;
            while (b < nb) begin
                @(negedge clk);
                if (m_valid !== 1'b0) $fatal(1, "FAIL record %0d: m_valid before the frame is complete", r);
                if (($random(seed) & 3) == 0) begin
                    s_valid = 1'b0; s_last = 1'b0;
                end else begin
                    s_valid = 1'b1; s_data = vec[40*r + 12 + b]; s_last = fin && (b == nb - 1);
                    if (s_ready === 1'b1) begin
                        if (fin && b == nb - 1) t_last = cyc + 1;
                        b = b + 1;
                    end else if (!fin || b != nb - 1 || 1'b1) begin
                        $fatal(1, "FAIL record %0d: s_ready low while receiving beat %0d", r, b);
                    end
                end
            end
            @(negedge clk); s_valid = 1'b0; s_last = 1'b0; s_data = 8'd0;
            if (fin) begin
                n_checks = n_checks + 1;
                if (s_ready !== 1'b0) $fatal(1, "FAIL record %0d: s_ready high after the last input beat", r);
            end
        end
    endtask

    task receive_and_check(input integer r);
        integer got_n, nout, waitc;
        reg [7:0] bt;
        reg       lt;
        begin
            got_n = 0; nout = vec[40*r + 2]; waitc = 0;
            while (got_n < nout) begin
                if (s_ready !== 1'b0) $fatal(1, "FAIL record %0d: s_ready high before the response was taken (beat %0d)", r, got_n);
                m_ready = (($random(seed) & 3) != 0);
                if (m_valid === 1'b1 && m_ready) begin
                    bt = m_data; lt = m_last;
                    check8(bt, vec[40*r + 4 + got_n], "response beat", r);
                    check8({7'd0, lt}, {7'd0, (got_n == nout - 1)}, "m_last", r);
                    if (got_n == 0) check8(t_mv - t_last + 1, vec[40*r + 3], "latency (cycles)", r);
                    got_n = got_n + 1;
                end
                waitc = waitc + 1;
                if (waitc > 2000) $fatal(1, "FAIL record %0d: response did not complete", r);
                @(negedge clk);
            end
            m_ready = 1'b0;
            n_checks = n_checks + 1;
            if (s_ready !== 1'b1) $fatal(1, "FAIL record %0d: s_ready low after the last response beat", r);
            if (m_valid !== 1'b0) $fatal(1, "FAIL record %0d: m_valid high after the last response beat", r);
        end
    endtask

    integer typ, wc;
    initial begin
        n_checks = 0; n_cmd = 0; n_rst = 0; seed = 32'h7A1;
        if (!$value$plusargs("VEC=%s", vfile)) $fatal(1, "FAIL no +VEC=<vectors.hex>");
        // header record = first line that is not a comment: A5, records hi/lo, N, bits, ring, version
        fd = $fopen(vfile, "r");
        if (fd == 0) $fatal(1, "FAIL cannot open %0s", vfile);
        h0 = 0;
        while (h0 == 0 && !$feof(fd)) begin
            if ($fgets(line, fd) && $sscanf(line, "%h %h %h %h %h %h %h", h0, h1, h2, h3, h4, h5, h6) != 7) h0 = 0;
        end
        $fclose(fd);
        if (h0 !== 32'hA5) $fatal(1, "FAIL %0s: not a vector file (header %h)", vfile, h0);
        if (h3 !== `EXP_N || h4 !== `EXP_BITS || h5 !== `EXP_RING)
            $fatal(1, "FAIL header N=%0d bits=%0d ring=%0d does not match the DUT (N=%0d bits=%0d ring=%0d)",
                   h3, h4, h5, `EXP_N, `EXP_BITS, `EXP_RING);
        nvec = h1 * 256 + h2;
        if (nvec < 1 || nvec > MAXREC) $fatal(1, "FAIL bad record count %0d", nvec);
        $readmemh(vfile, vec, 0, 40 * (nvec + 1) - 1);
        do_reset;
        for (k = 1; k <= nvec; k = k + 1) begin
            typ = vec[40*k];
            case (typ)
                1: begin                                    // COMMAND
                    send(k, vec[40*k + 1], 1'b1);
                    receive_and_check(k);
                    n_cmd = n_cmd + 1;
                end
                2: begin                                    // RST_IDLE
                    do_reset; n_rst = n_rst + 1;
                end
                3: begin                                    // RST_MID: unfinished frame, then reset
                    send(k, vec[40*k + 1], 1'b0);
                    do_reset; n_rst = n_rst + 1;
                end
                4: begin                                    // RST_PEND: whole frame, response not taken, then reset
                    send(k, vec[40*k + 1], 1'b1);
                    wc = 0;
                    while (m_valid !== 1'b1) begin
                        @(negedge clk); wc = wc + 1;
                        if (wc > 2000) $fatal(1, "FAIL record %0d: no response to hold", k);
                    end
                    do_reset; n_rst = n_rst + 1;
                end
                default: $fatal(1, "FAIL record %0d: unknown record type %0d", k, typ);
            endcase
        end
        $display("PASS %0s_tb: %0d records, %0d checks (%0d commands: beats, m_last, latency, s_ready, back-pressure; %0d resets)",
                 `STR(`DUT), nvec, n_checks, n_cmd, n_rst);
        $finish;
    end
