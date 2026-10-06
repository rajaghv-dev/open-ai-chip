// SPDX-License-Identifier: Apache-2.0
// soc_tb -- a tiny local SoC: PicoRV32 (Wishbone master) + 64 KiB RAM + console/exit/cycle registers + wb_stream_adapter
// driving kv_attn_n8 at 0x3000_0000 (copy of soc_sim/soc_tb.v for the KV-cache firmware). Stand-in for Caravel's management core.
//   0x0000_0000..0x0000_FFFF  RAM (firmware loaded with $readmemh from +hex=<file>, default firmware.hex)
//   0x2000_0000 CONSOLE  W  low byte printed with $write
//   0x2000_0004 EXIT     W  1 = PASS, anything else = FAIL (ends the simulation)
//   0x2000_0008 CYCLE    R  free-running clock counter (counts from reset release)
//   0x2000_000C IRQST    R  bit 0: user_irq[0] seen since the last write to this register (sticky); any write clears
//   0x3000_0000..         user_project_wrapper (Wishbone slave window of tiny_ai_core)
`timescale 1ns/1ps
`default_nettype none
module kv_soc_tb;
    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    wire        trap;
    wire [31:0] adr, dat_o;
    reg  [31:0] dat_i;
    wire        we, stb, cyc;
    wire [3:0]  sel;
    wire        ack;

    picorv32_wb #(
        .ENABLE_COUNTERS(1), .ENABLE_REGS_DUALPORT(1), .BARREL_SHIFTER(0), .COMPRESSED_ISA(0),
        .ENABLE_MUL(0), .ENABLE_DIV(0), .ENABLE_IRQ(0),
        .PROGADDR_RESET(32'h0), .STACKADDR(32'h0001_0000)
    ) cpu (
        .trap(trap), .wb_rst_i(rst), .wb_clk_i(clk),
        .wbm_adr_o(adr), .wbm_dat_o(dat_o), .wbm_dat_i(dat_i), .wbm_we_o(we), .wbm_sel_o(sel),
        .wbm_stb_o(stb), .wbm_ack_i(ack), .wbm_cyc_o(cyc),
        .pcpi_valid(), .pcpi_insn(), .pcpi_rs1(), .pcpi_rs2(), .pcpi_wr(1'b0), .pcpi_rd(32'b0),
        .pcpi_wait(1'b0), .pcpi_ready(1'b0), .irq(32'b0), .eoi(), .trace_valid(), .trace_data(), .mem_instr());

    // picorv32_wb drives sel = mem_wstrb, i.e. 0 for reads; Caravel's master drives full byte lanes on reads and the
    // core masks read data by sel, so reads get sel = 1111 here.
    wire [3:0] sel_bus = we ? sel : 4'hF;

    // ---- decode
    wire valid    = cyc & stb;
    wire ram_sel  = (adr[31:16] == 16'h0000);
    wire misc_sel = (adr[31:8]  == 24'h200000);
    wire tai_sel  = (adr[31:8]  == 24'h300000);
    wire oth_sel  = ~ram_sel & ~misc_sel & ~tai_sel;

    // ---- RAM (one-clock registered ack, like the core's)
    reg [31:0] ram [0:16383];
    reg        ack_loc;                         // RAM / misc / unmapped ack
    wire       take_loc = valid & ~ack_loc & (ram_sel | misc_sel | oth_sel);
    initial begin : load
        reg [1023:0] f;
        integer i;
        for (i = 0; i < 16384; i = i + 1) ram[i] = 32'h0000_0013;      // nop
        if (!$value$plusargs("hex=%s", f)) f = "firmware.hex";
        $readmemh(f, ram);
    end
    wire [13:0] widx = adr[15:2];
    always @(posedge clk) begin
        if (take_loc && ram_sel && we) begin
            if (sel[0]) ram[widx][7:0]   <= dat_o[7:0];
            if (sel[1]) ram[widx][15:8]  <= dat_o[15:8];
            if (sel[2]) ram[widx][23:16] <= dat_o[23:16];
            if (sel[3]) ram[widx][31:24] <= dat_o[31:24];
        end
    end

    // ---- misc registers
    reg [31:0] cycle_cnt;
    reg        irq_seen;
    wire [2:0] user_irq;
    wire [31:0] tai_dat;
    wire        tai_ack;
    reg  [31:0] misc_rd;
    always @(*) begin
        case (adr[3:2])
            2'd2:    misc_rd = cycle_cnt;
            2'd3:    misc_rd = {31'd0, irq_seen};
            default: misc_rd = 32'd0;
        endcase
    end
    always @(posedge clk) begin
        if (rst) begin
            cycle_cnt <= 32'd0; irq_seen <= 1'b0; ack_loc <= 1'b0;
        end else begin
            cycle_cnt <= cycle_cnt + 32'd1;
            ack_loc   <= take_loc;
            if (user_irq[0]) irq_seen <= 1'b1;
            if (take_loc && misc_sel && we) begin
                case (adr[3:2])
                    2'd0: $write("%c", dat_o[7:0]);
                    2'd1: begin
                        if (dat_o == 32'd1) $display("SOC_SIM: firmware exit PASS after %0d cycles", cycle_cnt);
                        else                $display("SOC_SIM: firmware exit FAIL code %0d after %0d cycles", dat_o, cycle_cnt);
                        $finish;
                    end
                    2'd3: irq_seen <= 1'b0;
                    default: ;
                endcase
            end
            if (take_loc && oth_sel) $display("SOC_SIM: access to unmapped address %h (we=%b)", adr, we);
        end
    end

    assign ack = ack_loc | tai_ack;
    always @(*) begin
        dat_i = 32'd0;
        if (ack_loc) dat_i = ram_sel ? ram[widx] : misc_sel ? misc_rd : 32'd0;
        else if (tai_ack) dat_i = tai_dat;
    end

    // ---- wb_stream_adapter + the unchanged kv_attn_n8 engine at 0x3000_0000
    wire        eng_rst, s_valid, s_last, s_ready, m_valid, m_last, m_ready;
    wire [7:0]  s_data, m_data;
    wb_stream_adapter #(.TX_DEPTH(16), .RX_DEPTH(16)) u_adapter (
        .wb_clk_i(clk), .wb_rst_i(rst),
        .wbs_stb_i(stb & tai_sel), .wbs_cyc_i(cyc & tai_sel), .wbs_we_i(we), .wbs_sel_i(sel_bus),
        .wbs_dat_i(dat_o), .wbs_adr_i(adr), .wbs_ack_o(tai_ack), .wbs_dat_o(tai_dat), .irq(user_irq),
        .eng_rst(eng_rst), .s_valid(s_valid), .s_data(s_data), .s_last(s_last), .s_ready(s_ready),
        .m_valid(m_valid), .m_data(m_data), .m_last(m_last), .m_ready(m_ready));
    kv_attn_n8 u_engine (
        .clk(clk), .rst(eng_rst),
        .s_valid(s_valid), .s_data(s_data), .s_last(s_last), .s_ready(s_ready),
        .m_valid(m_valid), .m_data(m_data), .m_last(m_last), .m_ready(m_ready));

    // ---- reset, watchdog, trap
    initial begin
        repeat (10) @(posedge clk);
        rst <= 1'b0;
    end
    always @(posedge clk) if (!rst && trap) begin
        $display("SOC_SIM: CPU trap at cycle %0d (pc/bus adr %h)", cycle_cnt, adr);
        $display("FAIL: cpu trap");
        $finish;
    end
    initial begin
        #(10 * 40_000_000);
        $display("FAIL: watchdog (40M cycles)");
        $finish;
    end
endmodule
`default_nettype wire
