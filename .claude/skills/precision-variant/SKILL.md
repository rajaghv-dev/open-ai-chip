---
name: precision-variant
description: Add or change a number-format variant of the precision study (the seven prec_<fmt> engines bin, tern, int4, int8, fp8, fp16, bf16 - one neuron, same 24 pins, only the arithmetic differs) in the open-ai-chip repo. Use when asked to add a format (fp4, int2, e5m2, tf32 ...), change a format's rounding/accumulator/latency, re-quantise the weights, fix a float timing failure at 40 MHz, resize a prec_* die, regenerate the prec ROMs and vectors, or extend docs/PRECISION_STUDY.md with a new row.
---

# Precision variant

The study builds one 9-weight neuron seven times; the number format is the only variable (docs/PRECISION_STUDY.md section 1). The contract is `model/precision_hw/spec.md`; `model/precision_hw/golden.py` is the bit-exact reference and wins if the two disagree (spec.md header). RTL must match golden.py on every vector of `designs/prec_<fmt>/tb/vectors.hex`. Lessons, numbers and file anchors: reference.md. For the generic engine recipe (config, wiring, gates) read the add-tiny-engine skill; only the differences are here.

## Files you touch

| File | Role |
|---|---|
| `model/precision_hw/spec.md` | contract: quantisation (sec 3), arithmetic (4), stream (5), schedule and latency (6), ROM ports (7), vectors (8), results (9) |
| `model/precision_hw/golden.py` | `FMTS`, `LATENCY`, `WBITS`, `BIAS_BITS`, `ACC_BITS`, `INT_QMAX`, `FLOATS`/`FLOAT_FMT`, `quantise`, `infer`, `run`, `fold8`, `check`, `trace` |
| `model/precision_hw/gen.py` | writes `designs/prec_<fmt>/rtl/prec_<fmt>_rom.v` and `tb/vectors.hex` (no fitting step; sha256 of golden.py + gen.py in the header; write-if-changed) |
| `model/precision_hw/report.py` | study tables A/B; reads each design's `README.md` (the word `Latency` followed by the number), `output/metrics.json`, `output/reports/synth_stat.rpt` |
| `designs/prec_<fmt>/` | config.json, README.md, NOTES.md, rtl/prec_<fmt>.v (+ _fmul/_fadd/_lzc submodules for floats), tb/prec_<fmt>_tb.v |

## Add a format: steps

1. **Spec first.** Add the format to spec.md: weight encoding, bias encoding and units, accumulator format and width, every rounding rule (RNE, FTZ, tie rules), zero handling, class rule, beat 1 = `fold8(acc)` (raw accumulator bits zero-extended to bytes and XORed, so every accumulator bit reaches the pins), latency, ROM ports. Prove no overflow/NaN/Inf (spec 4.6 style) so the RTL needs no such logic; state the proof's precondition and assert it in gen.py.
2. **golden.py.** Add to `FMTS` (order = report and gen order), `WBITS`, `BIAS_BITS`, `ACC_BITS`, `LATENCY` (`3` for the two-stage float MAC, else `2`); quantise in `quantise()`; implement the step in `infer()` using exact `fractions.Fraction` arithmetic, never float. Integer pixels are unsigned: zero-extend before signed multiplies. Run `python3 model/precision_hw/golden.py --check`, then `golden.py <fmt> p0 ... p8` and `golden.py --trace <fmt> p0..p8` for any input.
3. **gen.py.** Add the ROM text branch (continuous `assign` only, `addr >= 9` reads 0, ROM ports from spec sec 7). The case list is shared by all formats (931 cases) and **must stay <= 1023** (`MAX_CASES`; stream_tb.vh `MAXREC = 1024`). Run `python3 model/precision_hw/gen.py`; a second run must write nothing.
4. **RTL.** Copy the nearest sibling (integer: prec_int8; float: prec_fp16 or prec_fp8). Keep the canonical micro-architecture of spec sec 6: LOAD/DRAIN/OUT0/OUT1, `x_vld/x_pix/x_idx` input stage, acc preloaded with bias (bin: 0) on reset and after the OUT1 beat. Header sections: WHY THIS IS AI, NEURAL NETWORK <-> HARDWARE, FLOAT RULES, BLOCKS (IO / MEMORY / COMPUTE / CONTROL).
5. **Design dir.** `tb/prec_<fmt>_tb.v` with `define DUT prec_<fmt>` and `include "stream_tb.vh"`; config.json from the closest sibling; README.md including a line `Latency <n>` (report.py parses it); NOTES.md after the flow (write-design-notes skill).
6. **Wire it in** (see add-tiny-engine step 9): Makefile `ALL_DESIGNS`; tests/run_tests.sh `NEWENG`; scripts/check_generated.sh design list; scripts/docs/tables.py `ORDER`; tests/adapter/run.sh `<name>:FRAME`. The model dir (precision_hw) is already in `MODELS`, `generate`, `model-check` and `check_generated.sh`.
7. **Gates:** `golden.py --check` clean; `make simulate DESIGN=prec_<fmt>` PASS; corrupt-vector negative test fails (run_tests.sh already does prec_int8: copy for the new format); `make flow-all DESIGN=prec_<fmt>` all 5 PASS; `python3 model/precision_hw/report.py`; `make table`; `make test`; `make check-generated`.

## Timing lessons (40 MHz, 25 ns, sky130, slow corner max_ss_100C_1v60)

- A **single-stage float MAC misses 25 ns**: fp8 -2.144 ns (spec.md sec 6, run `designs/prec_fp8/runs/RUN_2026-10-05_20-07-06`), fp16 -6.667 ns (designs/prec_fp16/NOTES.md, `RUN_2026-10-05_20-09-08`).
- **Two-stage MAC, latency 3** (stage 1 multiply+round into a product register, stage 2 add+round): still short, fp8 -1.141, fp16 -1.318, bf16 -0.927 ns (prec_fp8/README.md; prec_fp16/NOTES.md `RUN_2026-10-05_20-21-30`; prec_bf16/NOTES.md `RUN_2026-10-05_20-11-27`).
- **Tool timing repair closed it, thinly**: config keys `RUN_POST_GRT_RESIZER_TIMING`, `PL_RESIZER_SETUP_SLACK_MARGIN 0.5`, `GRT_RESIZER_SETUP_SLACK_MARGIN 0.5`, `PL_RESIZER_SETUP_BUFFERING`, `PL_RESIZER_SETUP_GATE_CLONING`, `GRT_RESIZER_SETUP_BUFFERING`, `GRT_RESIZER_SETUP_GATE_CLONING`, all on, no RTL change. Result max_ss setup +0.259 (fp8), +0.111 (fp16), +0.044 (bf16) ns (docs/PRECISION_STUDY.md sec 6). Treat as fragile; recheck on another machine.
- **The accumulate loop is the speed limit.** The next add needs the previous sum, so align/add/LZC/normalise/round cannot be pipelined without slowing the loop; only the multiply can be cut off. A faster float needs interleaved accumulators or integer accumulation (PRECISION_STUDY sec 8). Integer MACs close in one stage (int8 +11.387 ns).
- Do not "fix" a failing float by raising CLOCK_PERIOD in one design: the study compares formats at one clock.
- Latency is part of the contract: change `golden.LATENCY`, spec.md sec 6, the README `Latency` line, regenerate vectors (every record's byte 15), then the RTL.

## Hardware-honesty lessons

- **Weights are constants, area is "hard-wired weights"**: synthesis folds the ROM into logic; no memory is counted (PRECISION_STUDY sec 2). `param b` is what a programmable version would store. (prec_bf16/NOTES.md notes the multiplier operand is a mux of constants selected by the registered `x_idx`, so it is still a general multiplier.)
- **Provably dead register bits**: every bf16 weight has biased exponent 0x74..0x7D, so every nonzero product exponent is 0x74..0x81 and exponent bit 6 equals NOT bit 7. Synthesis deleted that flop, and signoff failed "logic lost" (52 RTL registers, 51 cells). Rule: `check_signoff.py` requires surviving sequential cells >= RTL registers - allowance, allowance default 0 (scripts/flow/signoff_allowances.json). Prefer making the RTL honest (prec_bf16 stores 15 product bits and rebuilds bit 13 as `~p_bits_r[13]`, allowance 0, `registers: RTL 51 (allowance 0), surviving 51`). Use an allowance entry only with a reason a reviewer can verify in the RTL. Regenerating the ROM with larger exponents breaks the rebuild: recheck the range argument whenever weights change.
- One-hot FSM recoding adds flops (bf16 49 RTL bits -> 51 cells); count them before blaming synthesis.
- **Die sizing**: ~40 % utilisation target, but the dies were guessed (fp16/bf16 started at 220 um for an estimate of 3-5k cells and came out at 905/776 synthesised cells, utilisation 29.1 % / 24.9 %). Compare cell area, never die area. Use `//DIE_AREA` notes; slew margin 20 if repair runs out of memory.
- **Vector limit 1023** cases (stream_tb.vh `MAXREC = 1024`); gen.py asserts it. Adding case groups means trimming others (N_TEST_CASES 600, N_MARGIN 120, N_RANDOM 60).

## Extending docs/PRECISION_STUDY.md

The page is generated-by-hand from `report.py` output plus committed evidence; every number cites a file. After a hardening run: run `python3 model/precision_hw/report.py`, paste Study table A and B (sec 2) and the synthesised cell mix (sec 3), update the area ratios, the wirelength/vias table (sec 5, `route__wirelength`, `route__vias`), the setup-slack table and the three-column float history (sec 6, from `runs/RUN_*/final/metrics.json`), and the accuracy-vs-area bars (sec 7). Keep the honest limits (sec 8): 2000 test images, 0.53-point standard error, so rank only by "same decision as fp32". Mark values not found in repo files as "not found in repo files" (as the bf16 single-stage cell does); do not invent them. Update spec.md sec 9 and the format counts in README.md and Makefile `help`.
