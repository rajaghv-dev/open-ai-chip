// SPDX-License-Identifier: Apache-2.0
// Self-checking testbench for the ChipIgnite template user project (designs/user_proj_example/rtl/user_proj_example.v,
// module user_proj_example, from chipfoundry/caravel_user_project). Written for this repository from the
// RTL's behaviour; it is not part of the template.
//
// SCOPE: the unit alone -- a 16-bit counter reachable over the Wishbone slave port and the logic analyser.
// The template's own tests (io_ports, la_test1, la_test2) run inside a full Caravel simulation driven by
// management-core firmware; that is OUT OF SCOPE here, as is the user_project_wrapper.
//
// Checks: reset state, free-running count, Wishbone read / full write / byte writes / write with no byte
// enables, logic-analyser load of the count (full and partial mask), logic-analyser clock and reset
// override, io_out / io_oeb / la_data_out / irq outputs. Comparisons use === / !== so X never passes.
// Fails with $fatal(1, ...) on the first wrong value or on a timeout; prints "PASS" and finishes otherwise.
// Runs on the RTL and, unchanged, on the synthesised and routed gate-level netlists (scripts/flow/gl_sim.sh).
`timescale 1ns/1ps

module user_proj_example_tb;
    reg         wb_clk_i = 0;
    reg         wb_rst_i = 1;
    reg         wbs_stb_i = 0, wbs_cyc_i = 0, wbs_we_i = 0;
    reg  [3:0]  wbs_sel_i = 0;
    reg  [31:0] wbs_dat_i = 0, wbs_adr_i = 0;
    wire        wbs_ack_o;
    wire [31:0] wbs_dat_o;
    reg  [127:0] la_data_in = 0;
    reg  [127:0] la_oenb = {128{1'b1}};
    wire [127:0] la_data_out;
    wire [15:0] io_in = 16'h0;
    wire [15:0] io_out, io_oeb;
    wire [2:0]  irq;

    user_proj_example dut (
        .wb_clk_i(wb_clk_i), .wb_rst_i(wb_rst_i),
        .wbs_stb_i(wbs_stb_i), .wbs_cyc_i(wbs_cyc_i), .wbs_we_i(wbs_we_i), .wbs_sel_i(wbs_sel_i),
        .wbs_dat_i(wbs_dat_i), .wbs_adr_i(wbs_adr_i), .wbs_ack_o(wbs_ack_o), .wbs_dat_o(wbs_dat_o),
        .la_data_in(la_data_in), .la_data_out(la_data_out), .la_oenb(la_oenb),
        .io_in(io_in), .io_out(io_out), .io_oeb(io_oeb), .irq(irq));

    always #5 wb_clk_i = ~wb_clk_i;   // 100 MHz; inputs change on the falling edge, outputs are sampled there

    wire [15:0] count = la_data_out[15:0];
    integer n_checks = 0;
    reg [15:0] c0, r0;
    integer i;

    // hard timeout: the whole test needs about 120 clock cycles
    initial begin
        #100000;
        $fatal(1, "FAIL timeout: testbench did not finish");
    end

    task expect16(input [15:0] got, input [15:0] want, input [255:0] what);
        begin
            n_checks = n_checks + 1;
            if (got !== want)
                $fatal(1, "FAIL %0s: got %h expected %h (t=%0t)", what, got, want, $time);
        end
    endtask

    task tick(input integer n);   // n rising edges, then settle at the following falling edge
        begin
            repeat (n) @(posedge wb_clk_i);
            @(negedge wb_clk_i);
        end
    endtask

    // Wishbone cycle; called at a falling edge, returns at the falling edge after the ack
    task wb(input we, input [3:0] sel, input [31:0] dat);
        integer t;
        begin
            wbs_cyc_i = 1; wbs_stb_i = 1; wbs_we_i = we; wbs_sel_i = sel; wbs_dat_i = dat; wbs_adr_i = 32'h3000_0000;
            t = 0;
            @(posedge wb_clk_i);
            @(negedge wb_clk_i);
            while (wbs_ack_o !== 1'b1) begin
                t = t + 1;
                if (t > 8) $fatal(1, "FAIL wishbone: no ack within 8 cycles");
                @(negedge wb_clk_i);
            end
            wbs_cyc_i = 0; wbs_stb_i = 0; wbs_we_i = 0; wbs_sel_i = 0; wbs_dat_i = 0;
        end
    endtask

    initial begin
        @(negedge wb_clk_i);
        // ---- reset state: counter 0, pads are inputs (oeb high), no ack, irq tied low
        tick(3);
        expect16(count, 16'h0000, "count in reset");
        expect16(io_out, 16'h0000, "io_out in reset");
        expect16(io_oeb, 16'hFFFF, "io_oeb in reset (inputs)");
        expect16({15'b0, wbs_ack_o}, 16'h0, "ack in reset");
        expect16({13'b0, irq}, 16'h0, "irq");
        n_checks = n_checks + 1;
        if (la_data_out[127:16] !== {112{1'b0}}) $fatal(1, "FAIL la_data_out[127:16] not zero: %h", la_data_out[127:16]);

        // ---- free-running count after reset release
        wb_rst_i = 0;
        tick(10);
        expect16(count, 16'd10, "count 10 cycles after reset");
        expect16(io_out, 16'd10, "io_out mirrors count");
        expect16(io_oeb, 16'h0000, "io_oeb after reset (outputs)");

        // ---- Wishbone read: returns the count the cycle before, ack for one cycle
        c0 = count;
        wb(1'b0, 4'b1111, 32'h0);
        expect16(wbs_dat_o[15:0], c0, "wishbone read data");
        expect16({16'b0, wbs_dat_o[31:16]}, 16'h0, "wishbone read upper half");
        tick(1);
        expect16({15'b0, wbs_ack_o}, 16'h0, "ack drops after one cycle");

        // ---- Wishbone full write
        c0 = count;
        wb(1'b1, 4'b0011, 32'h0000_1234);
        expect16(count, 16'h1234, "full write 0x1234");
        expect16(wbs_dat_o[15:0], c0, "write returns old count");
        tick(1);
        expect16(count, 16'h1235, "counts on from written value");

        // ---- byte writes (low byte stays below FF so there is no carry into the upper byte)
        wb(1'b1, 4'b0001, 32'h0000_00AB);
        expect16(count, 16'h12AB, "write low byte only");
        tick(1);   // let ack fall: a new request while ack is high is only accepted one cycle later
        wb(1'b1, 4'b0010, 32'h0000_5500);
        expect16(count[15:8], 16'h55, "write high byte only");
        tick(1);
        c0 = count;
        wb(1'b1, 4'b0000, 32'h0000_FFFF);   // we=1 but no byte enables: nothing is written, the count keeps running
        expect16(count, c0 + 16'd1, "write with sel=0 does not load");

        // ---- logic analyser: load all 16 bits (la_oenb low = LA drives probe), count holds
        la_data_in[63:48] = 16'hBEEF;
        la_oenb[63:48]    = 16'h0000;
        tick(3);
        expect16(count, 16'hBEEF, "LA load 0xBEEF");
        expect16(io_out, 16'hBEEF, "io_out follows LA load");
        // ---- partial mask: only the low byte is LA controlled; other bits load as 0 (la_write & la_input)
        la_oenb[63:48] = 16'hFF00;
        la_data_in[63:48] = 16'h1234;
        tick(2);
        expect16(count, 16'h0034, "LA partial load");
        // ---- LA released: counting resumes
        la_oenb[63:48] = 16'hFFFF;
        tick(5);
        expect16(count, 16'h0039, "counting resumes after LA release");

        // ---- LA reset override (probe 65): forces reset, pads become inputs
        la_oenb[65] = 1'b0; la_data_in[65] = 1'b1;
        tick(2);
        expect16(count, 16'h0000, "LA-forced reset clears count");
        expect16(io_oeb, 16'hFFFF, "LA-forced reset sets io_oeb");
        la_data_in[65] = 1'b0;
        tick(4);
        expect16(count, 16'd4, "count after LA reset released");
        expect16(io_oeb, 16'h0000, "io_oeb after LA reset released");
        la_oenb[65] = 1'b1;

        // ---- LA clock override (probe 64): wb_clk_i is ignored, la_data_in[64] clocks the counter
        c0 = count;
        la_oenb[64] = 1'b0; la_data_in[64] = 1'b0;
        tick(1);
        c0 = count;
        tick(5);
        expect16(count, c0, "count holds while the LA owns the clock");
        for (i = 0; i < 3; i = i + 1) begin
            #1 la_data_in[64] = 1'b1;
            #1 la_data_in[64] = 1'b0;
        end
        #1;
        expect16(count, c0 + 16'd3, "three LA clock pulses");
        la_oenb[64] = 1'b1;
        tick(2);

        $display("PASS user_proj_example_tb: %0d checks (reset, count, Wishbone read/write, LA load/clock/reset)", n_checks);
        $finish;
    end
endmodule
