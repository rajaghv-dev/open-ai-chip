# caravel_sim: full-Caravel simulation of user_project_wrapper (tiny_ai_core) with real firmware

Ladder steps (iv) RTL and (v) gate-level (hybrid), native on Apple-silicon macOS, no Docker, no cocotb.
Route used: the classic caravel_user_project DV flow (C test -> RISC-V GCC -> hex in a SPI-flash model, iverilog testbench),
adapted from the template's `verilog/dv/wb_port`. Caravel and the management core sources are NOT modified.

Setup (one time; downloads about 5 GB, shallow clones; versions in VERSIONS.txt):
    git clone -b CC2509 --depth=1 https://github.com/chipfoundry/caravel-lite build/caravel/caravel
    git clone -b CC2509 --depth=1 https://github.com/chipfoundry/caravel_mgmt_soc_litex build/caravel/mgmt_core_wrapper
    brew install riscv64-elf-gcc iverilog        # sky130A PDK expected at ~/.volare (PDK_ROOT to override)

Run:
    caravel_sim/run_rtl.sh     # about 55 s: RTL
    caravel_sim/run_gl.sh      # about 60 s: our routed pnl netlists for wrapper + macro, rest RTL
Expected last line: `Monitor: tiny_ai_core Caravel firmware (RTL) PASS` / `(GL) PASS`.
Negative test: `EXTRA_CFLAGS=-DNEG_TEST caravel_sim/run_rtl.sh` -> `Monitor: tiny_ai_core Caravel firmware FAIL code 0xe032`.
Outputs/logs/VCD: build/caravel/work/ (git-ignored). `iverilog -DTRACE_WB` prints Wishbone acks (add to SIMDEF).

Files: tiny_ai_wb.c (firmware), tiny_ai_wb_tb.v (testbench, pass/fail on mprj_io[31:16]), includes.rtl.user (user RTL list),
run_rtl.sh, run_gl.sh.

Firmware: enables the user Wishbone (reg_wb_enable), drives mprj_io[31:16] as management outputs for the result code,
reads ID 0x54414901, then runs vision_all_lit 1 1 1 1 (RESULT 0x0401, CYCLES 6), text_sentiment 1 1 3 0 (0x0101, 6),
vision_block all ones (0x0401, 15) and a diagonal image (0x0200, 15), expected values from model/tiny_ai/golden.py core_run().
Signalling: 0xAB60 started, 0xAB61 pass, 0xE0xx fail (xx = case id | 0x10 status, 0x20 RESULT, 0x30 CYCLES, 0x40 timeout; ID mismatch = 0xE001).

Notes: the GCC needs `-march=rv32i_zicsr` (new binutils); the template's riscv32-unknown-linux-gnu prefix is replaced by
riscv64-elf-gcc (multilib rv32i). Firmware runs from a SPI flash model, so it is slow (about 4.1 ms simulated for the whole test).
Gate-level here is hybrid: only user_project_wrapper + tiny_ai_core are netlists (wrapper run RUN_2026-10-05_19-52-33 final/pnl,
macro build/macros/tiny_ai_core/pnl, functional sky130 cell models, unit delay, no SDF). Full-chip GL of the Caravel/mgmt core
and SDF timing sims (need cvc64) were not attempted.
