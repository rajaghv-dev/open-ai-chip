# firmware: real RISC-V code against the verified hardware, no Caravel

Two firmware programs live here, each run on its own local PicoRV32 SoC sim (iverilog, no Docker, no downloads):

| Program | Command (make target) | DUT | Result of the latest run (2026-10-06) |
|---|---|---|---|
| `firmware/` (this directory) | `make -C firmware sim` (`make soc-sim`) | `user_project_wrapper` RTL -> `tiny_ai_core` | PASS, 1,899,308 cycles, wall 24 s |
| `firmware/kv/` | `make -C firmware/kv sim` (`make soc-kv`) | `wb_stream_adapter` + `kv_attn_n8` RTL | PASS, 1,020,782 cycles, wall 13 s |

Scope: these are not Caravel simulations. The full-Caravel RTL/gate-level/SDF sims (`caravel_sim/`, `make caravel-rtl|caravel-gl|
caravel-fullgl|caravel-sdf-wrapper`) and the local precheck (`make precheck`) were run only on `user_project_wrapper` (tiny_ai_core).
The `user_project_wrapper_soc_itm` and `user_project_wrapper_soc_kv` wrappers (hardened, signoff clean) were not run through
`caravel_sim` or the precheck; `soc_kv_attn_n8` is the hardened version of the adapter + `kv_attn_n8` pair that `make soc-kv` simulates
at RTL (the sim instantiates the two modules directly, not the wrapper).

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
Runtime: about 24 s wall, 1,899,308 simulated clocks (`SOC_SIM: firmware exit PASS after 1899308 cycles`).

## Files

- `gen_expected.py`: generates `build/expected.h` (all test cases, expected class / score / CYCLES, labels, and the
  learned parameters) from `model/tiny_ai/golden.py` and `weights.json`. Nothing is hand-written.
- `main.c`, `start.S`, `link.ld`, `Makefile`, `bin2hex.py`.
- `kv/` (`main.c`, `gen_expected.py`, `Makefile`) and `soc_sim/kv/` (`kv_soc_tb.v`, `run.sh`): the KV-cache program, see the last section.
- `soc_sim/` (`soc_tb.v`, `run.sh`, `third_party/picorv32/`): the SoC sim of the first program.

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

## Relation to the real Caravel run (docs/SOC_PLAN.md ladder iv-vi)

- Done, for `user_project_wrapper` (tiny_ai_core) only: a VexRiscv program (`caravel_sim/tiny_ai_wb.c`, ID read plus four
  cases, not this exhaustive firmware) passes in the full-Caravel RTL sim, the hybrid gate-level sim and the full-chip gate-level
  sim; see caravel_sim/README.md and docs/CARAVEL_SIM.md. The register sequences are the same Wishbone accesses as here.
- Not done: this exhaustive firmware and the KV firmware have not been compiled for Caravel's management SoC (`defs.h`
  addresses, `mprj_*` helpers), and no Caravel sim exists for the soc_itm / soc_kv wrappers.
- Differences to expect: Wishbone latency through Caravel's arbitration, different CPU cycle costs, flash-resident
  code, and the cycle counter being a Caravel timer rather than this testbench register. The cycle tables here are
  PicoRV32 numbers, not Caravel numbers.

## KV-cache attention: prefill vs decode

A second firmware program, `firmware/kv/`, runs on a second SoC sim, `soc_sim/kv/` (PicoRV32 + RAM + `wb_stream_adapter` +
the unchanged `kv_attn_n8` engine, spec in `model/kv_attention/spec.md`). One command: `make -C firmware/kv sim` or `make soc-kv` (13 s wall time and 1,020,782 simulated clocks in the latest run:
`SOC_SIM: firmware exit PASS after 1020782 cycles`; CAPS read 0x00101001). It reuses `start.S`, `link.ld` and `bin2hex.py` from this directory unchanged.

What it does: eight sessions, P = 0..7. Each session sends RESET_CACHE, then PREFILL of P prompt tokens as ONE frame
`[02, t0..t(P-1)]`, then DECODE `[03, t]` until the 8-entry cache is full, then one more DECODE (the CACHE_FULL error
response). Every response beat (2 beats, or 8 for a successful DECODE) is compared with values generated by
`firmware/kv/gen_expected.py` from `model/kv_attention/golden.py`; any mismatch prints a FAIL line and the run exits
FAIL. Cycle counts use the free-running counter register, timer-read overhead (11) removed. The engine-latency check
subtracts a same-shape frame on a full cache (2 input beats, 2 response beats, engine L = 2), so the CPU write gap
cancels and the difference must equal n+7 (= (n+3-2) + (8-2) response beats); that is checked for every decode.

Measured (this run's console output, CPU clock cycles):

    PREFILL: one frame [02, t0..t(P-1)]
      P  roundtrip  per_token  write  wait  read  CYCLES_reg
      1        328      328.0    127    41   160          56
      2        379      189.5    178    41   160         107
      3        430      143.3    229    41   160         158
      4        481      120.3    280    41   160         209
      5        532      106.4    331    41   160         260
      6        583       97.2    382    41   160         311
      7        634       90.6    433    41   160         362

    DECODE by cache fill n (round trip = write + wait + read)
      n  roundtrip   write    wait    read  (wr%/wait%/rd%)  CYCLES_reg  minus_base  engine_L(n+3)
      0      670.0   127.0    41.0   502.0  (18/6/74)        63.0         7.0              3
      1      670.0   127.0    41.0   502.0  (18/6/74)        64.0         8.0              4
      2      670.0   127.0    41.0   502.0  (18/6/74)        65.0         9.0              5
      3      670.0   127.0    41.0   502.0  (18/6/74)        66.0        10.0              6
      4      670.0   127.0    41.0   502.0  (18/6/74)        67.0        11.0              7
      5      670.0   127.0    41.0   502.0  (18/6/74)        68.0        12.0              8
      6      670.0   127.0    41.0   502.0  (18/6/74)        69.0        13.0              9
      7      670.0   127.0    41.0   502.0  (18/6/74)        70.0        14.0             10
    baseline (DECODE on a full cache, 2 response beats): roundtrip 328, CYCLES_reg 56

`CYCLES_reg` is the adapter's CYCLES register (first TX beat accepted to the last response beat captured), so it also
contains the CPU's gap between the two input pushes and the response beats; `minus_base` removes that and shows the
engine-side growth, exactly one cycle per cached entry.

The lesson, in plain words:

- **Prefill amortises the fixed cost.** One prefill frame pays the bus setup, the opcode beat, the wait and the 2-beat
  response once; each extra prompt token adds only one 51-cycle bus write (and 1 engine cycle). So the cost per prompt
  token falls with P: 328.0 at P = 1 to 90.6 at P = 7 (the total grows only 51 per added token).
- **Decode pays a full round trip per token.** Every generated token is its own frame: 2 writes, a wait, and 8 response
  reads, 670 cycles here: about 2 times a one-token prefill's 328 (my division 670 / 328 = 2.0) and about 7 times the
  per-token cost of a 7-token prefill (670 / 90.6 = 7.4), with nothing to share it over. Reading the 8-beat answer
  (502 cycles) is three quarters of it.
- **Decode engine time grows with the cache.** CYCLES minus baseline rises 7, 8, ... 14 for n = 0..7, i.e. the engine's
  n+3 latency (one cache entry per cycle through one dot-product unit). In this tiny system that growth (at most 7
  cycles) is far smaller than one bus access (about 51 cycles) and the wait phase is a single status poll, so the CPU-side
  round trip does not move with n; it would only show with a slower engine or a bigger cache.

This is the tiny-scale version of "prefill is compute-bound, decode is memory/latency-bound" (see
`docs/LLM_INFERENCE.md`): many tokens share one setup and run at one per cycle, while each decoded token pays the whole
round trip and re-reads a cache that keeps growing. Numbers are for these 8-bit tokens, an 8-entry cache and a PicoRV32
doing one bus transaction per byte; they say nothing about real LLM sizes.
