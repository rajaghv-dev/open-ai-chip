# precision-variant reference (every number cites its file)

## Measured results, per format

| fmt | latency | acc bits | die um | std cells | util % | setup@max_ss ns | source |
|---|---|---|---|---|---|---|---|
| bin | 2 | 4 | 80x80 | 199 | 37.6 | 16.396 | docs/PRECISION_STUDY.md sec 2, 6 |
| tern | 2 | 10 | 80x80 | 293 | 63.5 | 16.346 | same |
| int4 | 2 | 12 | 80x80 | 377 | 83.3 | 13.457 | same |
| int8 | 2 | 17 | 120x120 | 642 | 45.7 | 11.387 | same |
| fp8 | 3 | 16 (fp16) | 170x170 | 1404 | 39.6 | 0.259 | same |
| fp16 | 3 | 16 | 220x220 | 1932 | 29.1 | 0.111 | same |
| bf16 | 3 | 16 (bf16) | 220x220 | 1754 | 24.9 | 0.044 | same |

Accuracy on 2000 held-out images: fp32 94.05, bin 88.95, tern 94.15, int4 94.25, int8 94.05, fp8 94.15, fp16 94.05, bf16 94.00 (spec.md sec 9; PRECISION_STUDY sec 2). Std-cell area relative to bin: 1.0 / 1.7 / 2.2 / 3.3 / 6.3 / 8.1 / 6.9 (sec 2).

Float timing history (PRECISION_STUDY sec 6; run metrics `designs/prec_<fmt>/runs/RUN_*/final/metrics.json`, `timing__setup__ws`):

| fmt | single-stage | two-stage (default repair) | final (tool repair) | evidence |
|---|---|---|---|---|
| fp8 | -2.144 (16 failing endpoints per ss corner; -1.916 nom_ss, -1.695 min_ss) | -1.141 | +0.259 | spec.md sec 6; prec_fp8/README.md |
| fp16 | -6.667 | -1.318 (14 failing endpoints at nom_ss and min_ss) | +0.111 | prec_fp16/NOTES.md lines ~190-191 |
| bf16 | not found in repo files | -0.9275 (123 repair buffers) | +0.044 | prec_bf16/NOTES.md line ~187 |

Timing-repair buffers (`design__instance__count__class:timing_repair_buffer`): fp8 125, fp16 180, bf16 133, against 35 to 79 for the integer designs (PRECISION_STUDY sec 6). Hold slack +0.107 to +0.115 ns everywhere.

## Contract details worth remembering (spec.md)

- Task: 3x3 image of 4-bit pixels, vertical bar = 1, horizontal bar = 0; generator `golden.make_image`, LCG(seed) `s = (1103515245*s + 12345) & 0x7FFFFFFF`; train seed 1 (400 images), test seed 2 (2000). Training: logistic regression, 1500 epochs, lr 0.5, own Taylor `exp`, only `+ - * /`, weights rounded to fp32 (sec 1-2).
- Quantisation (sec 3): bin sign bit + threshold T fitted on binarised training data (T = 3); tern TWN (delta = 0.7 mean|w|); int4/int8 symmetric (Q 7/127, scale from the 9 weights only, bias in same units, clamp [-Smax-1, Smax], Smax = 9*15*Q); floats RNE then FTZ (fp8 w0 and w8 flush to 0).
- Float arithmetic (sec 4.4): product rounded once to the accumulator format, sum rounded once, unfused; FTZ on inputs and outputs; exact zero is +0, never -0; fp8 products are exact in fp16, accumulator fp16; `class = ~acc[15]`. Safe range: fp8 |acc| < 61100 for any E4M3 ROM; fp16/bf16 require max(|param|) <= 256 (gen.py asserts it) (sec 4.6).
- Errors (sec 5.3): `s_data[7:4] != 0` makes the item unused (error set), wrong length sets error; the result is still produced. In bin an unused item counts nothing (not pixel 0).
- Float schedule (sec 6): `DRAIN0`, `DRAIN1` last exactly 2 cycles from `s_last`, independent of `count`; latency 3 for every frame, error or not; new frame cannot overlap the drain because `s_ready` is low.
- Vectors (sec 8): same 931 inputs for all formats: 600 test + 37 disagreement + 16 uniform + 27 single pixel + 4 shapes + 16 max/min + 120 margin + 60 random + 8 short + 3 long + 36 bad + 4 misc.
- Worked trace: `python3 model/precision_hw/golden.py --trace fp16 15 0 3 0 12 12 4 2 1` -> class 0, beat1 d5 (spec.md sec 4.4).

## Die sizing log

| fmt | die | note in config.json `//DIE_AREA` / source |
|---|---|---|
| fp8 | 170x170 | "about 35-40% utilisation, leaving routing room for the long adder path"; estimate 1200-1600 cells, 9000-12000 um2 |
| fp16, bf16 | 220x220 | "~40% utilisation of one serial float MAC ... roughly 3-5k cells; start 220 um; revisit after the first synthesis"; came out 905 / 776 synthesised cells and 29.1 / 24.9 % utilisation |
| int8 | 120x120 | has a `//DIE_AREA` note; 45.7 % |
| bin, tern, int4 | 80x80 | int4 is at 83.3 % utilisation |

fp16 NOTES.md line ~292: the 8226 fill cells (28986.6 um2) are 2.4x the logic area because the die was oversized; compare cell area. Slew margin is 40 in all seven prec configs (they pre-date the 20 convention of image_text_match).

## bf16 dead exponent bit (prec_bf16/NOTES.md lines ~74-78, ~270)

ROM exponents (biased): 0x78, 0x7D, 0x79, 0x7D, 0x79, 0x7D, 0x7A, 0x7D, 0x74 (weights) and the bias, all in 0x74..0x7D; pixel <= 15 adds at most 3 to the exponent plus 1 for a rounding carry, so nonzero products sit in 0x74..0x81, where bit 6 = NOT bit 7. `p_vld` is 0 for zero products, so the one value where the identity fails never reaches the adder. Product register: 15 bits `{sign, exp[7], exp[5:0], mantissa[6:0]}`. History: 16-bit register -> RTL 52 flops, 51 cells survived, `check_signoff` failed "logic lost" -> RTL fixed to 15 bits, `stage_check.log`: `registers: RTL 51 (allowance 0), surviving sequential cells 51`. Hidden coupling: larger weight exponents require putting the bit back.

## Stale text to verify, do not copy blindly

- Checked 2026-10-06: the earlier stale items (prec_bf16/README.md "not yet confirmed" sentence, PRECISION_STUDY Sources listing NOTES.md for four formats only) are fixed in the repo; all seven designs have a NOTES.md and the bf16 README now ends with the closed timing (+0.044 ns at max_ss).
- PRECISION_STUDY sec 3 says a multiplier by a known constant is much smaller; prec_bf16/NOTES.md line ~80 says the operand is a mux of ROM constants addressed by a register, so synthesis builds a general multiplier. Check `synth_stat.rpt` before claiming either for a new format (not re-verified in this pass).
