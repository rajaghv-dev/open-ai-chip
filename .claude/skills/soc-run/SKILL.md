---
name: soc-run
description: Run, debug and extend firmware on the open-ai-chip SoC. Use when running or debugging RISC-V firmware against tiny_ai_core on the local PicoRV32 SoC (make soc-sim), adding a firmware test case, putting a stream engine behind shared/rtl/wb_stream_adapter.v (make adapter-test), measuring CPU-vs-accelerator cycles, running the full-Caravel RTL / hybrid / full-chip gate-level sims (make caravel-rtl, caravel-gl, caravel-fullgl), SDF runs (caravel-sdf-wrapper), or the local ChipFoundry precheck (make precheck).
---

# soc-run: firmware and SoC simulation ladder

Repo root: run everything from there. Longer tables and snippets: [reference.md](reference.md).
Never run several heavy sims at once; check `ps` for running iverilog/vvp first.

## 1. Verification ladder (cheapest first)

| Step | Command | Wall | What it proves |
|---|---|---|---|
| (i) engine RTL | `make simulate DESIGN=<d>` | s to 1 min | engine alone vs vectors (docs/SOC_PLAN.md section 4) |
| (ii) PicoRV32 SoC | `make soc-sim` (= `make -C firmware sim`) | ~25 s, ~1.9 M clocks | real C firmware drives the REAL `user_project_wrapper` -> `tiny_ai_core` over Wishbone: register map, protocol errors, exhaustive vs golden, cycle table (firmware/README.md, soc_sim/run.sh) |
| (ii) adapter | `make adapter-test` (`ONLY="a b"` to subset) | ~9 s | `wb_stream_adapter` + each of 13 stream engines, Wishbone only, vs each `designs/<d>/tb/vectors.hex` (tests/adapter/run.sh) |
| (iv) Caravel RTL | `make caravel-rtl` | ~53 s | real VexRiscv mgmt core, SPI-flash boot, full Caravel RTL, 4 cases, pass flag on mprj_io (docs/CARAVEL_SIM.md) |
| (v) hybrid GL | `make caravel-gl` | ~59 s | wrapper + macro as routed power-aware netlists, rest RTL, unit delay, no SDF (caravel_sim/README.md) |
| (v+) full-chip GL / SDF | see section 8 | | being added |
| (vi) precheck | see section 8 | | being added |

Local PicoRV32 is NOT Caravel: no VexRiscv, flash boot, housekeeping or padframe (firmware/README.md). Passing (ii) says the
register sequences are right, not that Caravel integration works; that is (iv).

## 2. Toolchain facts (all verified in the repo)

- PicoRV32 SoC firmware flags: `-march=rv32i -mabi=ilp32 -Os -ffreestanding -fno-builtin -nostdlib -fno-pic`; no libgcc needed
  because there is no M extension, so NEVER multiply, divide or modulo at run time (firmware/Makefile, firmware/README.md).
  Prefix `riscv64-elf-` (brew `riscv64-elf-gcc`); override with `CROSS=`.
- Caravel firmware: `-march=rv32i_zicsr -mabi=ilp32 -D__vexriscv__ -ffreestanding -nostdlib`, linked with the mgmt core's
  `sections.lds`, `crt0_vex.S`, `isr.c` (caravel_sim/run_rtl.sh). Without `_zicsr` new binutils reject the CSR instructions.
- `picorv32_wb` drives `sel = 0` on reads; the core masks read data by `sel`, so `soc_tb.v` forces `sel = 1111` on reads
  (soc_sim/soc_tb.v lines 34-36). Any new Wishbone slave testbench must do the same; Caravel's master drives full lanes.
- Timing-sensitive "while busy" tests (INPUT/START/CLEAR landing during a run) need inline asm so two stores issue
  back-to-back: `__asm__ volatile("sw %1,0(%0)\n\tsw %2,0(%0)" :: ... : "memory")` (firmware/main.c ~lines 190, 204). Plain C
  adds loads/branches and the engine finishes first (6-15 clocks).
- Hierarchy inside Caravel CC2509 is `uut.chip_core.mprj.mprj` (user_project_wrapper = `.mprj`, our macro = `.mprj.mprj`);
  older templates use `uut.mprj.mprj` (caravel_sim/tiny_ai_wb_tb.v line 174). Use it for `$display` probes or SDF targets.
- PicoRV32 is vendored unmodified: YosysHQ/picorv32 main @ ef203c2b (ISC), soc_sim/third_party/picorv32/SOURCE.txt.
- macOS sed: `sed -i` needs a suffix argument; use `sed -i.bak -e ...` (as caravel_sim/run_rtl.sh does for `@10` -> `@00`).
  Never write GNU-only `sed -i -e`.
- Caravel downloads (not committed; build/ is git-ignored) at `build/caravel/`, shallow clones, tag CC2509
  (caravel_sim/README.md, VERSIONS.txt): `caravel` = chipfoundry/caravel-lite (953 MB, 58c8c77), `mgmt_core_wrapper` =
  chipfoundry/caravel_mgmt_soc_litex (4.1 GB, 503eda0). `make caravel-*` exits early with the sizes if either is missing
  (Makefile). sky130A PDK at `~/.volare` (override `PDK_ROOT`). Tools: riscv64-elf-gcc 16.2, iverilog 13.

## 3. Run and read the PicoRV32 SoC sim

`make soc-sim`. Success needs ALL of: a line `PASS`, `SOC_SIM: firmware exit PASS after N cycles`, no line starting `FAIL`
(soc_sim/run.sh last line; exit code is the verdict). Log: `soc_sim/build/sim.log`. Failure modes printed by soc_tb.v:
`CPU trap at cycle N` (illegal instruction / misaligned access), `watchdog (40M cycles)`, `access to unmapped address`,
`firmware exit FAIL code N`. Memory map (soc_sim/soc_tb.v header): RAM 64 KiB at 0; CONSOLE 0x2000_0000 (W, prints low byte);
EXIT 0x2000_0004 (W, 1 = PASS else FAIL code); CYCLE 0x2000_0008 (R, free-running); IRQST 0x2000_000C (R, sticky irq[0]);
tiny_ai_core at 0x3000_0000. `start.S` sets sp, zeroes bss, calls `main`, writes its return to EXIT; `link.ld` puts all in 64K RAM.

## 4. Add a firmware test

1. Expected values come from the golden model, never typed by hand. Extend `firmware/gen_expected.py` (reads
   `model/tiny_ai/golden.py` + `weights.json`) so it emits the new cases/classes/scores/CYCLES into `firmware/build/expected.h`.
   The Makefile already depends on those inputs, so `make soc-sim` regenerates it.
2. Add the check in `firmware/main.c` using the helpers: `wr/rd`, `putc_/puts_/putdec`, `check_eq(got, want, "label")`,
   `tick()` for timing. Run the accelerator via the existing one-inference helper (CLEAR, mode, INPUT pushes, START, poll
   STATUS busy, read RESULT and CYCLES). Compare ID/CAPS/STATUS, RESULT, CYCLES and "no ERROR" as the existing cases do.
3. Signalling: print on the console; failures accumulate in the check counters; `main` returns 1 for PASS and any other value
   as the FAIL code, which `start.S` writes to EXIT. The harness requires the `PASS` line AND the exit-PASS line.
4. Negative-check your test once: break one expected value, confirm `make soc-sim` exits non-zero, revert.
5. Keep it exhaustive where cheap (current run: 16 + 512 + 256 inputs, no subsampling) but watch the 25 s budget and the
   40 M-cycle watchdog. No 32-bit multiply/divide (section 2).

Caravel firmware (caravel_sim/tiny_ai_wb.c): add a case to the `run(mode, in, n, exp_res, exp_cyc, id)` table with values
from `model/tiny_ai/golden.py core_run()`. Signalling on `mprj_io[31:16]` via `reg_mprj_datal = v << 16`: `0xAB60` started,
`0xAB61` pass, `0xE0xx` fail where xx = case id | 0x10 (status) / 0x20 (RESULT) / 0x30 (CYCLES) / 0x40 (timeout); ID mismatch is
`0xE001`. Negative test: `EXTRA_CFLAGS=-DNEG_TEST caravel_sim/run_rtl.sh` must end `FAIL code 0xe032`. Final line to expect:
`Monitor: tiny_ai_core Caravel firmware (RTL) PASS` / `(GL) PASS`. Logs and VCD: `build/caravel/work/` (run_rtl.log, run_gl.log).
Caravel setup in firmware: `reg_wb_enable = 1`, set mprj_io 31..16 to `GPIO_MODE_MGMT_STD_OUTPUT`, `reg_mprj_xfer = 1` then wait.

## 5. Register maps (authoritative: the RTL headers)

tiny_ai_core, base 0x3000_0000 (designs/tiny_ai_core/rtl/tiny_ai_core.v header): 0x00 ID 0x54414901; 0x04 CTRL [1:0] mode,
W bit8 START, bit9 CLEAR; 0x08 STATUS [0] BUSY [1] DONE [2] ERROR [5:4] mode [11:8] count; 0x0C INPUT (W, [7:0]);
0x10 RESULT [0] class [15:8] signed score; 0x14 CYCLES; 0x18 CAPS; 0x1C DEBUG. Modes: 0 vision_all_lit (4 inputs),
1 vision_block (9), 2 text_sentiment (4). START and CLEAR in one write: CLEAR wins.

wb_stream_adapter, same base (shared/rtl/wb_stream_adapter.v header; table in reference.md): 0x00 ID 0x53545201, 0x04 CTRL,
0x08 STATUS, 0x0C TXDATA, 0x10 TXLAST, 0x14 RXDATA (read pops), 0x18 RXSTATUS, 0x1C CYCLES, 0x20 CAPS.
Re-read the header before relying on bit fields; do not copy from memory.

## 6. Put a stream engine behind the adapter

1. Engine has the 24-port stream interface (`clk, rst, s_valid/s_data/s_last/s_ready, m_valid/m_data/m_last/m_ready`).
   Soft test: add `name:FORMAT` to `ENGINES` in tests/adapter/run.sh (FORMAT = FRAME, PITCH or ONSET, per
   tests/adapter/adapter_tb.v header: record layouts of its `tb/vectors.hex`), then `ONLY=<name> make adapter-test`.
   The tb checks registers, burst/ERR_OVF, back-pressure, irq count, ERR_UF, CLEAR + replay with `!==` (X never passes).
2. Hardening: copy the pattern of `designs/soc_image_text_match/` (adapter + ONE unchanged engine, port list identical to
   `tiny_ai_core` so it drops into `user_project_wrapper` as `mprj`); its README has the register map and the C driver
   (CLEAR; check ID; N-1 TXDATA writes; TXLAST; poll STATUS DONE or RXSTATUS; pop RXDATA until m_last).
3. Frame engines: N input beats, last one via TXLAST, then 2 result beats. Streaming engines: one result per sample after warm-up.

## 7. Measured insight and how to measure a new engine

Cycle table (firmware/README.md, this repo's run, averages over all cases, CPU clocks):
vision_all_lit sw 153.0 vs accel round trip 490.0 (327 write + 79 wait + 84 read), CYCLES reg 6, sw/accel 0.3x;
vision_block 1318.6 vs 728.0, CYCLES 15, 1.8x; text_sentiment 351.0 vs 490.0, CYCLES 6, 0.7x.
Insight: the engine computes in 6-15 clocks but each PicoRV32 bus transaction costs ~55 clocks, so the round trip is
bus-bound; accelerator wins only on the most work per input (9-pixel convolution). Says nothing about large networks.
To measure a new engine the same way: (a) read `CYCLE` before/after with `tick()`, subtracting one timer read (11 cycles,
measured at start); (b) time the software version on an already-unpacked input (no bus); (c) time the accelerator as
write phase + wait (poll STATUS) + read phase; (d) separately report the engine's own CYCLES register; (e) average over ALL
cases, print the table, state the ratio. Caravel numbers will differ (arbitration latency, flash-resident code, timer source).

## 8. Full-chip GL, SDF and precheck (details: docs/CARAVEL_SIM.md, docs/PRECHECK.md)

| Step | Command | Wall | Result |
|---|---|---|---|
| full-chip GL, firmware, functional cells, unit delay | `make caravel-fullgl` (`caravel_sim/run_fullgl.sh`, iverilog native) | 14 m 23 s | PASS |
| wrapper + macro GL + SDF | `make caravel-sdf-wrapper` (`caravel_sim/run_sdf_wrapper.sh`; `CORNER=nom_ss_100C_1v60`, `CLK_HALF=<ns>` for a too-fast-clock check) | ~5 s | PASS at nom_tt_025C_1v80, nom_ss_100C_1v60, max_ss_100C_1v60 |
| full-chip GL + SDF + firmware | `SCOPE=full caravel_sim/run_sdf.sh` | not completed | < 0.06 simulated us per wall second |
| local precheck | `make precheck` (`precheck/run_precheck.sh [check...]`) | 61 s | 14 of 14 PASS |

Never run these concurrently with a flow. Without `build/caravel` (or the CVC binary / image `openchip-cvc64-base`) the make targets stop with a message.
Lessons:
- iverilog is unusable for SDF (`$sdf_annotate` takes 2 args, mis-parses `cell.pin` INTERCONNECT, no timing checks). The only open SDF simulator is
  Open Verilog CVC 7.00b: x86-only, so it runs in an emulated amd64 container (colima profile `osl`). It needs fixed copies of the cvc-pdk models and the
  36 INTERCONNECT entries ending at top-level output ports dropped (the script does both). Timing checks are not enforced (`+notimingchecks`); SDF is proven live
  only by period sweeps (fails at short periods, passes at longer ones). Annotation: 2186/2186 IOPATH, 2312/2348 INTERCONNECT.
- caravel-lite ships no SDF: `caravel_sim/gen_caravel_sdf.sh` generates one with OpenSTA from the shipped SPEF. The mgmt_core_wrapper SDFs do not match the flat caravel_core.
- Full-chip GL+SDF does not fit 45 min emulated; unblock: an x86 Linux host, or shorten the SPI-flash boot (615 us of 4100 us).
- Simulators must use the project's `designs/user_project_wrapper/rtl/user_defines.v` (GPIO 5..37 `GPIO_MODE_MGMT_STD_INPUT_NOPULL`, owner decision 2026-10-06); run_rtl/run_gl/run_fullgl do.
  The template default has `GPIO_MODE_INVALID`, which fails the precheck `gpio_defines` and `oeb` checks.
- Precheck runs in our own container, not ChipFoundry's `mpw_precheck` image: say so whenever reporting it. `cf login/init/push` stay human-only.
- Do not mistake a simulator container for a flow: `scripts/flow/find_reusable_run.py` once counted one and refused to reuse a run; it now only counts LibreLane containers.
