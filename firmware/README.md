# firmware: real RISC-V code against the verified hardware, no Caravel

What this is: a learning-scale stand-in for Caravel's management core. A PicoRV32 (rv32i, ISC license,
`soc_sim/third_party/picorv32/`, Wishbone master `picorv32_wb`) runs C firmware in an iverilog SoC
(`soc_sim/soc_tb.v`) made of 64 KiB RAM, a console / exit / cycle-counter / irq-status block at `0x2000_0000`, and the
REAL `user_project_wrapper` (-> `tiny_ai_core` -> the three engines, RTL unchanged) at `0x3000_0000`.
It is NOT Caravel's VexRiscv management core, flash/SPI boot, housekeeping or padframe. It is register-level identical
to what Caravel firmware does: the same Wishbone reads and writes of the `tiny_ai_core` register map.
One interconnect detail: `picorv32_wb` drives `sel = 0` on reads, so the testbench forces `sel = 1111` on reads (as
Caravel's master does; the core masks read data by `sel`).

## Run

    make -C firmware sim        # builds firmware, compiles the SoC with iverilog, runs; exit 0 only on PASS

Needs `riscv64-elf-gcc` (brew), iverilog, python3. Toolchain flags (work, no libgcc needed):
`-march=rv32i -mabi=ilp32 -Os -ffreestanding -fno-builtin -nostdlib`. The CPU has no MUL/DIV; the code never
multiplies or divides at run time (a shift-subtract divide is used for printing averages).
Runtime: about 25 s wall, about 1.9 M simulated clocks.

## Files

- `gen_expected.py`: generates `build/expected.h` (all test cases, expected class / score / CYCLES, labels, and the
  learned parameters) from `model/tiny_ai/golden.py` and `weights.json`. Nothing is hand-written.
- `main.c`, `start.S`, `link.ld`, `Makefile`, `bin2hex.py`.

## What the firmware does

1. Register block: ID, CAPS, STATUS reset value, DEBUG buffer, unmapped reads/writes, byte-lane writes (`sb`).
2. Protocol negatives: START with 0 or 3-of-4 inputs, out-of-range inputs (modes 0, 2, 3), START in mode 3, 10th
   input, START+CLEAR in one write (CLEAR wins), INPUT / START / CLEAR while busy: each sets sticky ERROR; CLEAR removes it.
3. irq[0]: the completion pulse is seen (polled through a sticky status bit; PicoRV32 IRQs not used).
4. Exhaustive: all 16 `vision_all_lit`, all 512 `vision_block`, all 256 `text_sentiment` inputs, through the
   accelerator (class, score, CYCLES, no ERROR) and through the pure-C software networks (XNOR-count neuron; 2x2 kernel
   over 4 windows with max pooling / OR; embedding sum), both compared with the golden tables. No subsampling.
5. Cycle comparison table, then `PASS` / `FAIL` and exit code via the EXIT register.

## Measured cycle comparison (this run's console output; CPU clock cycles, averages over all cases)

All timing uses the free-running cycle-counter register; each interval has one timer read (11 cycles, measured at
start) subtracted. Software time is the network on an already-unpacked input (no bus). Accelerator round-trip =
CLEAR + mode + N INPUT pushes + START (write) + polling STATUS until done (wait) + RESULT and CYCLES reads (read).

    mode            cases  sw_cpu  accel_roundtrip  (write+wait+read)  accel_CYCLES_reg  sw/accel
    vision_all_lit     16  153.0  490.0  (327.0+79.0+84.0)  6.0  0.3x
    vision_block      512  1318.6  728.0  (565.0+79.0+84.0)  15.0  1.8x
    text_sentiment    256  351.0  490.0  (327.0+79.0+84.0)  6.0  0.7x

Reading it: the accelerator computes in 6 to 15 clocks, but a PicoRV32 bus transaction costs tens of clocks, so the
round trip is dominated by bus writes (about 55 clocks each). For the 4-input networks the software wins; only the
9-pixel convolution (the most work per input) is faster on the accelerator. The comparison is for these tiny networks
on a slow multi-cycle CPU with a one-input-per-write interface; it says nothing about large networks.

## What remains for the real Caravel run (docs/SOC_PLAN.md ladder iv-vi)

- Same register sequences compiled for Caravel's management SoC (VexRiscv, `defs.h` addresses, `mprj_*` helpers), run
  in the full-Caravel RTL simulation with the real `user_project_wrapper` (step iv).
- Then gate-level simulation with the hardened macro netlists (v) and the physical flow checks (vi).
- Differences to expect: Wishbone latency through Caravel's arbitration, different CPU cycle costs, flash-resident
  code, and the cycle counter being a Caravel timer rather than this testbench register.
