# Full-Caravel simulation (ladder steps iv and v)

Result: working natively on the Mac. A real VexRiscv management-core program, booted from a SPI flash model inside the
complete Caravel RTL, reads tiny_ai_core's ID through the real Wishbone path, runs one case per mode and checks RESULT and
CYCLES against model/tiny_ai/golden.py.

| Step | Command | Result | Wall time |
|---|---|---|---|
| (iv) RTL | `caravel_sim/run_rtl.sh` | `Monitor: tiny_ai_core Caravel firmware (RTL) PASS` (pass flag at 4102.5 us sim time) | 53 s |
| (v) gate-level, hybrid | `caravel_sim/run_gl.sh` | `Monitor: tiny_ai_core Caravel firmware (GL) PASS` | 59 s |
| negative check | `EXTRA_CFLAGS=-DNEG_TEST caravel_sim/run_rtl.sh` | `Monitor: tiny_ai_core Caravel firmware FAIL code 0xe032` | 55 s |

Step (v) is hybrid: user_project_wrapper (RUN_2026-10-05_19-52-33 final/pnl) and tiny_ai_core (build/macros/tiny_ai_core/pnl)
are our routed power-aware netlists with sky130_fd_sc_hd functional models and unit delay; Caravel and the management core
stay RTL. Not done: full-chip GL, SDF back-annotation (needs cvc64), cocotb/Docker route.

Versions: caravel-lite CC2509 (58c8c77), caravel_mgmt_soc_litex CC2509 (503eda0), both from the template b510613 Makefile pins;
riscv64-elf-gcc 16.2.0, iverilog 13, sky130A at ~/.volare. Downloads: caravel 953 MB, mgmt core 4.1 GB (shallow). Work dir 182 MB.
Details, firmware, and exact commands: caravel_sim/README.md. Caveats: needs `-march=rv32i_zicsr`; the template's
riscv32-unknown-linux-gnu toolchain prefix is replaced; the wrapper pnl comes from an earlier wrapper run than the macro export
(identical interface). No amd64 Docker image was needed.
