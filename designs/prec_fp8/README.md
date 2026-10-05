# prec_fp8

**Status:** hardened; `make flow-all DESIGN=prec_fp8` passes all 5 stages (simulate 931 cases, gds, check, gate-level synth and routed). Measured setup WNS at the slow corners: max_ss 0.259 ns, nom_ss 0.426 ns, min_ss 0.588 ns; hold >= 0.107 ns at all 9 corners; 1404 cells, die 28900 um2. Tool-only timing repair closed the slow corner (config.json: RUN_POST_GRT_RESIZER_TIMING, PL/GRT_RESIZER_SETUP_SLACK_MARGIN 0.5, setup buffering and gate cloning; no RTL timing change; CLOCK_PERIOD stays 25 ns). Before it, the pipelined RTL measured WNS -1.141 ns at max_ss_100C_1v60. Margin at max_ss is thin; a run on a different machine should be rechecked.

**Property:** Precision study, fp8 (OCP E4M3) weights with an fp16 accumulator: what a float multiply-accumulate costs.

**Task and model:** 3 x 3 image of 4-bit pixels; one neuron, `class = (bias + sum w[i]*x[i] >= 0)` (1 = vertical bar,
0 = horizontal bar). Weights and bias are the trained fp32 values rounded to E4M3 (subnormals flushed to +0, so
`w0` and `w8` become 0). Contract: `model/precision_hw/spec.md`; bit-exact reference: `model/precision_hw/golden.py`.

**Hardware:** one serial MAC, one input per clock, split into two pipeline stages. Stage 1: ROM, 4 x 4 significand
multiply, normalise; the exact fp16 product (no rounding) is registered. Stage 2: fp16 adder from the product register
(magnitude compare and swap, alignment shifter with guard/round/sticky, add/subtract, leading-zero count, normalising
shift, round-to-nearest-even, flush-to-zero, pack) into `acc`. No Inf/NaN/subnormal logic (spec section 4.6 proves it
is unreachable). **Latency 3 cycles** after the last beat (DRAIN lasts two cycles). Why: the first, single-cycle version
failed setup at 25 ns at the slow corner (`max_ss_100C_1v60`, WNS -2.144 ns, 16 endpoints), so the product is registered
between the multiplier and the adder (spec section 6, float schedule). Results are bit-identical.

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), `s_valid`, `s_data[7:0]`, `s_last`, `s_ready`,
`m_valid`, `m_data[7:0]`, `m_last`, `m_ready`. Output: beat 0 = `{6'b0, error, class}`, beat 1 = `acc[15:8] ^ acc[7:0]`.

## Run

```bash
make simulate DESIGN=prec_fp8     # 931 cases
make flow-all DESIGN=prec_fp8     # full flow (not run yet)
```

## Files

| Path | Contents |
|---|---|
| `config.json` | LibreLane configuration (170 x 170 um, see the `//DIE_AREA` key) |
| `rtl/prec_fp8.v` | the engine |
| `rtl/prec_fp8_rom.v` | E4M3 parameters, **generated** by `model/precision_hw/gen.py` (do not edit) |
| `tb/prec_fp8_tb.v` | testbench; body shared in `shared/tb/stream_tb.vh` |
| `tb/vectors.hex` | **generated** vectors (931 cases) |

## Verification notes

- All 931 vectors pass. An additional throw-away check compared the adder and product against a Python Fraction model
  on 400,000 random and cancellation-biased (acc, weight, pixel) triples with zero mismatches.
- 47 flip-flops (was 32): acc 16, state 3, count 4, error 1, x_vld 1, x_pix 4, x_idx 4, p_vld 1, product register 13
  (sign 1, exponent 5, mantissa 7; the low 3 mantissa bits are always 0).
- Timing: stage 1 is ROM, multiplier, leading-one detect and normalise shift; stage 2 is compare/swap, align shifter,
  add/subtract, leading-zero count, normalise shifter, rounding incrementer. The adder stage is now the longer one and
  is to be confirmed at the slow corner after re-hardening.
