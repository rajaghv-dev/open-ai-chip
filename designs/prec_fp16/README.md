# prec_fp16

**Property:** the cost of a 16-bit float neuron: IEEE 754 binary16: 1 sign, 5 exponent, 10 stored mantissa bits (11-bit significand), bias 15, smallest normal 2^-14.

**Status:** hardened; `make flow-all DESIGN=prec_fp16` passes all 5 stages (simulate 931 cases, gds, check, gate-level synth and routed). Measured setup WNS at the slow corners: max_ss 0.111 ns, nom_ss 0.281 ns, min_ss 0.434 ns; hold >= 0.107 ns at all 9 corners; 1932 cells, die 48400 um2. Tool-only timing repair closed the slow corner (config.json: RUN_POST_GRT_RESIZER_TIMING, PL/GRT_RESIZER_SETUP_SLACK_MARGIN 0.5, setup buffering and gate cloning; no RTL timing change; CLOCK_PERIOD stays 25 ns). Before it, the pipelined RTL measured WNS -1.318 ns at max_ss_100C_1v60. Margin at max_ss is thin; a run on a different machine should be rechecked.

**Task and model:** 3 x 3 image of 4-bit pixels; output 1 for a vertical bar, 0 for a horizontal bar. One neuron,
`class = (bias + sum w[i]*x[i] >= 0)`, weights learned by logistic regression (fp32), rounded to the format
(RNE, subnormals stored as +0). Contract: `model/precision_hw/spec.md`; bit-exact reference `model/precision_hw/golden.py`.
The same task is built in seven formats (bin, tern, int4, int8, fp8, fp16, bf16) so that the number format is the
only variable. For this one: parameter bits 160, accuracy 94.05 % (same decision as fp32 on 100.00 % of the test set).

**Hardware:** ONE floating-point MAC, reused for the 9 inputs, one per clock (the two-stage MAC of item k overlaps the
arrival of item k+1). Accumulator format = weight format; the product is rounded to the format, then the sum is
rounded (unfused, two RNE roundings per step). Flush-to-zero below the smallest normal; no Inf/NaN/subnormal logic
(proved unnecessary, spec 4.6). The MAC is two pipeline stages: stage 1 multiplies and rounds the product (`prec_fp16_fmul`) into a product register
(`p_bits`, valid bit `p_vld`); stage 2 adds it into the accumulator (`prec_fp16_fadd`). One beat per clock is kept, results are
bit-identical to the single-cycle MAC. Latency 3 cycles after the last beat (E0 accepts it, E1 registers the product,
E2 accumulates, `m_valid` rises after E2). Why: the single-cycle MAC fails 25 ns at the slow corners (measured on
prec_fp8: -2.144 ns at max_ss_100C_1v60), and fp16/bf16 chains are longer.

| Stage (in `rtl/prec_fp16.v`) | Hardware | This format |
|---|---|---|
| unpack, zero test | exponent == 0 | 5-bit exponent |
| multiply (stage 1) | significand x pixel (exact) | 11 x 4 multiplier |
| normalise, RNE | LZC (<= 4 places), incrementer | product rounded to the format |
| *product register* | 16-bit `p_bits` + `p_vld` | pipeline cut |
| align (stage 2) | right shifter with sticky | exponent difference of 5 bits |
| add / subtract | magnitude add or subtract | 14-bit aligned operands, 15-bit sum |
| normalise | LZC and left shifter | up to the full sum width |
| round, FTZ | RNE incrementer, exponent underflow flush | result exponent 5 bits |

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), input stream `s_valid`, `s_data[7:0]`, `s_last`, `s_ready`,
output stream `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`. Output: beat 0 = `{6'b0, error, class}`,
beat 1 = `acc[15:8] ^ acc[7:0]` (the whole accumulator is checked through the pins). `error`: `s_data[7:4] != 0` (item
skipped) or a frame that is not exactly 9 beats.

**Flip-flops:** 50 RTL bits (52 in silicon after the one-hot FSM recoding, `output/metrics.json`; was 32 with the single-cycle MAC): state 3 (was 2), count 4, error 1, x_vld 1, x_pix 4, x_idx 4, p_vld 1, p_bits 16, acc 16.

## Run

```bash
make simulate DESIGN=prec_fp16    # iverilog, 931 cases from tb/vectors.hex
```

## Files

| Path | Contents |
|---|---|
| `config.json` | LibreLane configuration (220 x 220 um start value, met4 max, 25 ns clock; see the `//DIE_AREA` key) |
| `rtl/prec_fp16.v` | the engine, the multiply stage `prec_fp16_fmul`, the add stage `prec_fp16_fadd` and the leading-zero counter `prec_fp16_lzc` |
| `rtl/prec_fp16_rom.v` | parameters, **generated** by `model/precision_hw/gen.py` (do not edit) |
| `tb/prec_fp16_tb.v` | testbench; body shared in `shared/tb/stream_tb.vh` |
| `tb/vectors.hex` | **generated** vectors, 931 cases (test images, corner images, errors, short and long frames) |

**Timing note:** the single-cycle MAC was one long path (ROM, multiplier, rounder, compare, aligner, adder, leading-zero count,
normaliser, rounder, flush) and the sibling prec_fp8 missed 25 ns at the slow corner, so the product register (spec.md
section 6 fallback, latency 3) is in. The pipelined version still missed at the slow corners; RUN_POST_GRT_RESIZER_TIMING with setup slack margins 0.5 and
setup buffering / gate cloning (`config.json`) closed it: setup +0.111 / +0.281 / +0.434 ns at max_ss / nom_ss / min_ss (NOTES.md, Timing).
