# Precision study in silicon: specification of the seven `prec_<fmt>` engines

One tiny task, one tiny model, implemented seven times. The engines are identical except for the number format:

| `fmt` | format | design directory |
|---|---|---|
| `bin`  | binary: 1-bit weights, binarised inputs, XNOR-popcount | `designs/prec_bin/` |
| `tern` | ternary weights {-1, 0, +1} | `designs/prec_tern/` |
| `int4` | 4-bit signed integer weights | `designs/prec_int4/` |
| `int8` | 8-bit signed integer weights | `designs/prec_int8/` |
| `fp8`  | OCP fp8 E4M3 weights, fp16 accumulation | `designs/prec_fp8/` |
| `fp16` | IEEE 754 binary16 | `designs/prec_fp16/` |
| `bf16` | bfloat16 | `designs/prec_bf16/` |

This file is the **contract**: the RTL must match `model/precision_hw/golden.py` bit for bit on every vector of
`designs/prec_<fmt>/tb/vectors.hex`. If this text and `golden.py` ever disagree, `golden.py` is the reference and
the disagreement is a bug to report.

Files:

| file | role |
|---|---|
| `model/precision_hw/golden.py` | data, training, quantisation, the bit-exact reference (`run`, `infer`), self-checks (`--check`), step trace (`--trace`) |
| `model/precision_hw/gen.py` | writes `designs/prec_<fmt>/rtl/prec_<fmt>_rom.v` and `designs/prec_<fmt>/tb/vectors.hex` (write-if-changed, sha256 header) |
| `model/precision_hw/report.py` | the study table; adds the hardware table when `designs/prec_<fmt>/output/metrics.json` exists |

Commands: `python3 model/precision_hw/golden.py --check`, `python3 model/precision_hw/gen.py`,
`python3 model/precision_hw/report.py`. All use the Python 3.9 standard library only and are deterministic.

---

## 1. Task and data

- **Image:** 3 x 3 pixels in raster order (index `i = 3*row + col`, 0..8). Each pixel is a **4-bit unsigned integer, 0..15**.
- **Label:** 1 = vertical bar (centre column: pixels 1, 4, 7), 0 = horizontal bar (centre row: pixels 3, 4, 5).
  The centre pixel 4 is on in both classes.
- **Generator** (`golden.make_image`): for each pixel in raster order:
  1. `on` = pixel lies on the bar of the label;
  2. with probability 0.15 (`rng.u() < 0.15`), `on = not on` (flip);
  3. `v = (12.0 if on else 3.0) + 2.0 * gauss()`, where `gauss()` = sum of 12 `rng.u()` minus 6 (Irwin-Hall);
  4. `pixel = clamp(floor(v + 0.5), 0, 15)`.
- **Random source:** `LCG(seed)`: `s = (1103515245*s + 12345) & 0x7FFFFFFF`, `u() = s / 2^31` (the same as
  `model/examples/precision.py`). Labels alternate 0, 1, 0, 1, ... (exactly balanced).
- **Sets:** training set = seed 1, 400 images. Held-out test set = seed 2, 2000 images. Nothing is tuned on the test set.

## 2. Model and fp32 reference

- One neuron: `sum = b + w[0]*x[0] + ... + w[8]*x[8]`, decision `class = (sum >= 0)`.
- **Training:** logistic regression, full-batch gradient descent, weights and bias start at 0, 1500 epochs,
  learning rate 0.5, on inputs `x/16` (an exact power-of-two scale, folded back afterwards: `w = w'/16`).
  Arithmetic is IEEE double with only `+ - * /` (the sigmoid uses a deterministic Taylor `exp`, not libm), so
  every platform produces the same bits. The final 9 weights and the bias are rounded to **fp32**: that is the
  reference model.
- **fp32 reference decision:** fp32 weights and bias, **exact** (rational) sum, `class = sum >= 0`.
  It reaches 92.00 % train and 94.05 % test accuracy.

Trained weights (rows = image rows) and bias:

```
  +0.014786  +0.291126  +0.028851
  -0.316742  -0.019083  -0.267208
  +0.044593  +0.294643  +0.000830        bias -0.181312
```

## 3. Quantisation (fp32 parameters to each format)

All seven formats store exactly **9 weights + 1 bias-like constant**, derived from the same fp32 parameters
after training (post-training quantisation). Exact rational arithmetic (`fractions.Fraction`) is used throughout.
`RNE(q)` = round to the nearest integer, ties to even.

### 3.1 `bin`: binary weights and threshold

- Weight bit `s[i] = 1` if `w[i] >= 0` else `0` (1 means +1, 0 means -1).
- Input bit `a[i] = 1` if `pixel >= 8`, i.e. `a[i] = pixel[3]`.
- Accumulator: `m = number of i with a[i] == s[i]` (XNOR, then count), 0..9.
- `class = (m >= T)`. `T` is a 4-bit unsigned integer, 0..10, **fitted on the binarised training set**: the
  `T` with the highest training accuracy, the smallest such `T` on ties. Result: **T = 3**.
- (The ±1 view: with `A = 2a-1`, `S = 2s-1`, `sum(A*S) = 2m - 9`, so `m >= T` is `sum(A*S) >= 2T - 9`.)

### 3.2 `tern`: ternary weights (TWN rule)

- Threshold `delta = 0.7 * mean(|w[i]|)` (9 weights). `q[i] = +1` if `w[i] > delta`, `-1` if `w[i] < -delta`, else `0`.
- Scale `alpha = mean(|w[i]|)` over the weights with `q[i] != 0`.
- Bias code `qb = clamp(RNE(b / alpha), -136, 135)`.
- Weight encoding: 2-bit two's complement (`01` = +1, `11` = -1, `00` = 0; `10` is never stored).
- Bias: 9-bit two's complement.

### 3.3 `int4`, `int8`: symmetric per-tensor integers

- `Q = 7` (int4) or `127` (int8). `scale = max(|w[i]|) / Q` over the 9 weights (the bias does not set the scale).
- `q[i] = RNE(w[i] / scale)`, always in `[-Q, Q]` (`-Q-1` is never produced).
- Bias in the **same units**: `qb = clamp(RNE(b / scale), -Smax-1, Smax)` with `Smax = 9 * 15 * Q`.
  The hardware needs no scale multiply: `sum >= 0` in integer units has the same sign as `scale * sum`.
- Encoding: weights 4-bit / 8-bit two's complement; bias 11-bit (int4) / 16-bit (int8) two's complement.

The bias clamp `[-Smax-1, Smax]` never changes a decision: with `|sum of products| <= Smax`, any bias above `Smax`
classifies everything as 1 (as does `Smax`), and any bias below `-Smax-1` classifies everything as 0 (as does
`-Smax-1`). It did not trigger here. The same rule gives the ternary range (`Smax = 135`).

### 3.4 `fp8`, `fp16`, `bf16`: float weights

- Each weight and the bias is rounded to the format with RNE (`golden.round_ftz`, section 4.4), then **flushed to
  zero (FTZ)**: a result below the smallest normal number is stored as `+0` (all-zero bits).
  Subnormal patterns are therefore never stored.
- `fp8` stores weights and bias in **E4M3**; `fp16` in binary16; `bf16` in bfloat16.
- Consequence for `fp8`: `w[0] = 0.0148` and `w[8] = 0.00083` are below 2^-6 = 0.015625 (smallest E4M3 normal)
  and become `+0`. With subnormals they would be kept (2^-9 steps). This cost is part of the measurement.

### 3.5 The resulting parameters (the generated ROMs hold exactly these)

| fmt | w0 | w1 | w2 | w3 | w4 | w5 | w6 | w7 | w8 | bias / T |
|---|---|---|---|---|---|---|---|---|---|---|
| fp32 | +0.0148 | +0.2911 | +0.0289 | -0.3167 | -0.0191 | -0.2672 | +0.0446 | +0.2946 | +0.0008 | -0.181312 |
| bin  | 1 | 1 | 1 | 0 | 0 | 0 | 1 | 1 | 1 | T = 3 |
| tern | 0 | +1 | 0 | -1 | 0 | -1 | 0 | +1 | 0 | -1 (alpha 0.29243) |
| int4 | 0 | +6 | +1 | -7 | 0 | -6 | +1 | +7 | 0 | -4 (scale 0.0452489) |
| int8 | +6 | +117 | +12 | -127 | -8 | -107 | +18 | +118 | 0 | -73 (scale 0.00249404) |
| fp8 (E4M3 hex) | 00 | 29 | 0F | AA | 8A | A9 | 13 | 29 | 00 | A4 = -0.1875 |
| fp16 (hex) | 2392 | 34A8 | 2763 | B511 | A4E3 | B446 | 29B5 | 34B7 | 12CD | B1CD = -0.181274 |
| bf16 (hex) | 3C72 | 3E95 | 3CEC | BEA2 | BC9C | BE89 | 3D37 | 3E97 | 3A5A | BE3A = -0.181641 |

---

## 4. Arithmetic: the bit-exact rules

### 4.1 Common structure (all formats)

```
acc = BIAS                          (bin: acc = 0)            "bias first"
for i = 0 .. 8 in raster order, for every USED item:          (unused item: no step at all, see 5.3)
    acc = acc (+) ( w[i] (x) x[i] )
class = (acc >= 0)                  (bin: class = (acc >= T))
```

`(x)` and `(+)` are the format's multiply and add defined below. One multiply-accumulate (MAC) unit performs all
steps serially, one step per clock (section 6).

### 4.2 Integer formats (`tern`, `int4`, `int8`): exact

- `x[i]` is the 4-bit pixel as an **unsigned** integer 0..15. (RTL pitfall: zero-extend it before a signed
  multiply; `$signed(4'hF)` is -1, not 15.)
- Product `w[i] * x[i]` and sum are **exact** two's-complement integers. No rounding, no saturation, no
  overflow (widths below are sized for any code the ROM may hold, not only the trained values).
- `tern` needs no multiplier: the step is `acc + x`, `acc - x` or nothing.

| fmt | weight | bias | product range | accumulator range (any ROM content) | **accumulator width** |
|---|---|---|---|---|---|
| tern | 2-bit | 9-bit `[-136, 135]` | `[-15, 15]` | `[-271, 270]` | **10 bits** signed |
| int4 | 4-bit | 11-bit `[-946, 945]` | `[-105, 105]` | `[-1891, 1890]` | **12 bits** signed |
| int8 | 8-bit | 16-bit `[-17146, 17145]` | `[-1905, 1905]` | `[-34291, 34290]` | **17 bits** signed |

- `class = ~acc[W-1]` (`acc >= 0`).
- With the trained parameters, the actual ranges are smaller: tern `[-31, 29]`, int4 `[-199, 221]`,
  int8 `[-3703, 3992]` (checked over all inputs by `golden.py --check`).

### 4.3 `bin`

- Step: `acc = acc + (pixel[3] XNOR s[i])`, 4-bit unsigned counter starting at 0 (max 9). `class = (acc >= T)`,
  a 4-bit unsigned compare.

### 4.4 Float formats

**Encodings.** `S | exponent | mantissa`, value `(-1)^S * 1.mantissa * 2^(exponent - bias)` for exponent fields
other than 0 and the special ones.

| | sign | exp bits | mant bits | bias | smallest normal | largest finite | specials |
|---|---|---|---|---|---|---|---|
| E4M3 (OCP fp8) | 1 | 4 | 3 | 7 | 2^-6 | 448 (`0x7E`) | no Inf; `S.1111.111` = NaN |
| fp16 (binary16) | 1 | 5 | 10 | 15 | 2^-14 | 65504 | exp `11111` = Inf/NaN |
| bf16 | 1 | 8 | 7 | 127 | 2^-126 | ~3.39e38 | exp `11111111` = Inf/NaN |

**Format of each engine.**

| fmt | weights and bias | input `x` | product format | accumulator format |
|---|---|---|---|---|
| fp8  | E4M3 | pixel, exact in any format | **fp16** (exact, see below) | **fp16** |
| fp16 | fp16 | pixel | fp16 | fp16 |
| bf16 | bf16 | pixel | bf16 | bf16 |

**Rules.**

1. **Input encoding.** Every pixel 0..15 is an integer with at most 4 significant bits and is **exact** in E4M3,
   fp16 and bf16 (15 = 1.111b x 2^3). The RTL may convert the pixel to the format (exact; a 4-bit leading-one
   detector) and use a generic format multiplier, or multiply the weight significand by the 4-bit integer
   directly. Both give identical bits, because the product is defined mathematically (rule 3).
2. **FTZ on inputs.** A weight or bias with exponent field 0 is zero (its mantissa is ignored). Generated ROMs
   never contain subnormals, Inf or NaN (section 3.4), and they never contain `-0`.
3. **Product.** `p = R_acc(w * x)`: the **exact** product, rounded once to the **accumulator format** with rule 5.
   If `w == 0` or `x == 0`, the product is `+0` and the step leaves `acc` unchanged (rule 6).
   - fp16: an 11-bit x 4-bit significand product (at most 15 bits) rounded to 11 bits.
   - bf16: an 8-bit x 4-bit product (at most 12 bits) rounded to 8 bits.
   - fp8: a 4-bit x 4-bit product (at most 8 bits) is **exact in fp16**, so no rounding happens. Exponent
     range: `2^-6 <= |w*x| <= 448*15 = 6720`, inside the fp16 normal range. The E4M3-to-fp16 widening of the
     bias is also exact.
4. **Sum.** `acc = R_acc(acc + p)`: the **exact** sum of the two operands, rounded once with rule 5. This is
   unfused: the product is rounded first and the sum is rounded again (two roundings per step).
5. **Rounding `R_fmt(v)`** (`golden.round_ftz`): if `v == 0`, the result is `+0`. Otherwise round `|v|` to the
   format's precision (11 significant bits for fp16, 8 for bf16) with **round-to-nearest, ties-to-even**, as if
   the exponent were unbounded, and restore the sign. If the rounded magnitude is below the smallest normal
   (2^-14 for fp16, 2^-126 for bf16), the result is **`+0`** (FTZ on outputs, sign dropped).
   - A correct RTL adder needs guard, round and sticky bits through alignment, and a leading-zero count and
     normalising shift after cancellation.
   - When does FTZ fire? Never on a product: `|w| >= smallest normal` and `x >= 1`. On a sum, only when
     cancellation leaves `|acc + p| < smallest normal`. Every normal value is a multiple of the ulp of the
     smallest normal, so such a sum is exact. Rounding before or after the tininess check therefore gives the same
     result: there is no "tininess before or after rounding" ambiguity. (FTZ never fired on the 2000 test
     images; see `report.py`.)
6. **Zero handling and sign.** `-0` never occurs: zeros from rule 3 and rule 5 are `+0`, an exact cancellation
   `a + (-a)` is `+0` (as IEEE RNE), and ROMs hold no `-0`. A step with a zero product leaves `acc` unchanged. An
   IEEE-style adder computing `acc + (±0)` gives the same bits, because `acc` is never `-0`. The RTL may skip or
   perform such steps.
7. **Class.** `class = ~acc[15]` (sign bit; `+0` gives class 1, consistent with `sum >= 0`).

**Worked trace** (`python3 model/precision_hw/golden.py --trace fp16 15 0 3 0 12 12 4 2 1`; use it for any input):

```
fp16  pixels [15, 0, 3, 0, 12, 12, 4, 2, 1]
  load bias  b1cd = -0.181274414   acc b1cd
  i=0  x=15  w=0.0147857666 prod 3319 = 0.221801758  acc 2930 = 0.0405273438
  i=1  x= 0  w=0.291015625  zero product: acc unchanged 2930
  i=2  x= 3  w=0.0288543701 prod 2d8a = 0.0865478516 acc 3011 = 0.127075195
  i=3  x= 0  w=-0.316650391 zero product: acc unchanged 3011
  i=4  x=12  w=-0.0190887451 prod b354 = -0.229003906 acc ae86 = -0.101928711
  i=5  x=12  w=-0.267089844 prod c269 = -3.20507812  acc c29d = -3.30664062
  i=6  x= 4  w=0.0445861816 prod 31b5 = 0.178344727  acc c242 = -3.12890625
  i=7  x= 2  w=0.294677734  prod 38b7 = 0.589355469  acc c114 = -2.5390625
  i=8  x= 1  w=0.000830173492 prod 12cd = 0.000830173492 acc c114 = -2.5390625     (absorbed by rounding)
  class 0  beat1 d5
```

For the same input, fp8 accumulates `b200, b200, b200, ae60, ae60, b558, c36b, c313, c1f3, c1f3` (beat1 `32`),
and bf16 ends at `c023` (beat1 `e3`).

### 4.5 Why these float choices (honest notes)

- **Same-format accumulation** for fp16 and bf16 isolates the format as the only variable, and it is the cheapest
  float datapath. Real accelerators usually accumulate bf16/fp16 products in fp32; that would raise accuracy and
  area. bf16 accumulation is coarse (8 significant bits), and that is part of what the study measures.
- **fp8 accumulates in fp16**, as fp8 hardware accumulates in a wider format; fp32 would be more common but is
  out of scale for this engine. Because E4M3 x 4-bit products are exact in fp16, fp8's only rounding is the fp16
  accumulation.
- **FTZ** (inputs and outputs) removes subnormal handling from the RTL. Section 4.4 rule 5 shows it never changes a
  sum's value except to flush a value whose exact magnitude is already below the smallest normal. It costs fp8 two
  small weights (section 3.4).
- **Unfused** multiply then add, with two RNE roundings, is the simplest honest pipeline. An FMA would round once.

### 4.6 No NaN, no Inf, no overflow: the proof

- **Inputs:** pixels are 0..15. ROM codes are finite normals or `+0` (the encoder asserts it; E4M3 never stores
  `0x7F`/`0xFF`).
- **fp8:** for **any** finite E4M3 weights and bias, `|acc| <= 448 + 9*448*15 = 60928` exactly. Each of the at
  most 9 roundings adds at most half an ulp (<= 16 at this magnitude), so `|acc| < 61100 < 65504`. Products are
  `<= 6720`. No overflow is possible, whatever the ROM holds.
- **fp16, bf16:** `gen.py` asserts the precondition `max(|w|, |b|) <= 256`. Then
  `|acc| <= 256 + 9*256*15 = 34816`, plus at most 18 half-ulp roundings, which stays below 65504 (fp16) and far
  below bf16's maximum. With the trained parameters, `|acc| <= 21`.
- **Integers:** widths in 4.2 hold any ROM content.
- **Hence:** no operation overflows, so no Inf is ever produced. Without Inf operands, `Inf - Inf` and `0 * Inf`
  cannot happen, so NaN is unreachable. The RTL needs **no** Inf/NaN logic, rounding-overflow-to-Inf logic, or
  subnormal logic. `golden.py` raises if any of these ever occurs.

---

## 5. Stream interface (identical to the other engines)

### 5.1 Pins (24)

The pins are those of `designs/vision_block/rtl/vision_block.v`:

```
input  clk, rst (synchronous, active high), s_valid, [7:0] s_data, s_last, m_ready
output s_ready, m_valid, [7:0] m_data, m_last
```

The top module is `prec_<fmt>` in `designs/prec_<fmt>/rtl/prec_<fmt>.v`. It instantiates `prec_<fmt>_rom` (generated).

### 5.2 Beats

- **In:** 9 beats, one pixel each in `s_data[3:0]`, raster order, `s_last` on the 9th. `s_data[7:4]` must be 0.
- **Out:** 2 beats; `m_last` on beat 1 only.
  - **beat 0** = `{6'b0, error, class}`.
  - **beat 1** = `fold8(acc)`: the final accumulator register's raw bits, zero-extended to whole bytes, all bytes
    XORed. Every accumulator bit reaches beat 1, so the testbench checks the whole accumulator: any single-bit
    error changes beat 1.

    | fmt | acc bits W | beat 1 |
    |---|---|---|
    | bin  | 4  | `{4'b0, acc[3:0]}` (the match count) |
    | tern | 10 | `acc[7:0] ^ {6'b0, acc[9:8]}` |
    | int4 | 12 | `acc[7:0] ^ {4'b0, acc[11:8]}` |
    | int8 | 17 | `acc[7:0] ^ acc[15:8] ^ {7'b0, acc[16]}` |
    | fp8, fp16, bf16 | 16 | `acc[15:8] ^ acc[7:0]` (fp8: the fp16 accumulator) |

### 5.3 Errors (same rules as `vision_block`)

- A beat with `s_data[7:4] != 0` sets `error`. That item is **unused**: no MAC step, the accumulator is
  unchanged. Its frame position still counts.
- A frame that is not exactly 9 beats sets `error`:
  - `s_last` before the 9th beat ends the frame early, and the missing items are unused;
  - beats after the 9th are accepted, ignored, and set `error`; the frame ends at `s_last`.
- The result is still produced: `class` comes from the accumulator built from the used items.
- In integer and float formats, an unused item gives the same accumulator as pixel 0. In `bin` it does not: pixel 0
  would be the input bit 0 and could count a match, but an unused item counts nothing.

## 6. Cycle-exact schedule and latency

**Latency** follows the convention of `model/tiny_ai/spec.json`: count the clock edges from the edge that accepts the
last input beat to the edge that raises `m_valid`, counting both edges.

**Canonical micro-architecture.** It is recommended. Only port behaviour, latency and bits are checked.

```
registers: state {LOAD, DRAIN, OUT0, OUT1}; count[3:0] (beats accepted, saturates at 9); error;
           x_vld, x_pix[3:0] (bin: x_pix[3] only), x_idx[3:0]; acc[W-1:0]
every rising edge (not in reset):
  if (x_vld) acc <= MAC(acc, rom.weight(addr = x_idx), x_pix)        -- the ONE MAC unit, combinational, 1 step/cycle
  x_vld <= beat_in & (count < 9) & (s_data[7:4] == 0);  x_pix <= s_data[3:0];  x_idx <= count
  LOAD : s_ready = 1. On beat_in: count <= min(count+1, 9); error |= bad item | (count >= 9) | (s_last & count != 8);
         if s_last: state <= DRAIN
  DRAIN: s_ready = 0. state <= OUT0              -- this edge performs the MAC of the last item
  OUT0 : m_valid = 1, m_data = {6'b0, error, class(acc)}.            On beat_out: state <= OUT1
  OUT1 : m_valid = 1, m_data = fold8(acc), m_last = 1. On beat_out: state <= LOAD, count <= 0, error <= 0,
         acc <= BIAS (bin: 0)
reset: state <= LOAD, count <= 0, error <= 0, x_vld <= 0, acc <= BIAS (bin: 0)
```

The MAC of item k overlaps the arrival of item k+1. Input gaps (`s_valid` low) simply leave `x_vld` low. The
bias is pre-loaded: the bias-first order costs no cycle.

**Latency = 2 for all seven formats** (edge E accepts the last beat into `x_*`; edge E+1 does its MAC and raises
`m_valid`). This is `golden.LATENCY` and is written into every vector record.

**Timing.**
- At 25 ns (`CLOCK_PERIOD` of the other engines), the integer and binary MACs are trivially fast.
- The fp16 and bf16 MACs are long register-to-register paths, through the ROM mux, the significand multiply, the
  product rounder, alignment, the adder, the leading-zero count, normalisation, the second rounder, and FTZ, into
  `acc`. They are expected to fit at the typical corner. The RTL agent must confirm the slow corner (ss, 100C, 1.6 V).
- **Allowed fallback** (only if a float format cannot close timing): add a product register (multiply and round in
  one cycle, add and round in the next). That format's latency becomes **3** (DRAIN lasts 2 cycles). Request
  `LATENCY[fmt] = 3` in `golden.py` and re-run `gen.py`, because the vectors carry the latency. The bits do not change.

## 7. Generated ROM interface (`designs/prec_<fmt>/rtl/prec_<fmt>_rom.v`)

| fmt | ports |
|---|---|
| bin  | `input [3:0] addr; output weight; output [3:0] threshold` |
| tern | `input [3:0] addr; output signed [1:0] weight; output signed [8:0] bias` |
| int4 | `input [3:0] addr; output signed [3:0] weight; output signed [10:0] bias` |
| int8 | `input [3:0] addr; output signed [7:0] weight; output signed [15:0] bias` |
| fp8  | `input [3:0] addr; output [7:0] weight; output [7:0] bias` (E4M3 bits) |
| fp16 | `input [3:0] addr; output [15:0] weight; output [15:0] bias` (binary16 bits) |
| bf16 | `input [3:0] addr; output [15:0] weight; output [15:0] bias` (bfloat16 bits) |

- `addr` 0..8 = pixel index in raster order; `addr >= 9` reads 0 (all-zero bits).
- Constants are written with continuous `assign` statements only, never `always @(*)`.
- Each file carries the sha256 of `golden.py` and `gen.py` in its header. Never edit it; change `golden.py` and
  re-run `gen.py`.

## 8. Test vectors (`designs/prec_<fmt>/tb/vectors.hex`)

**Format:** the format of `model/tiny_ai/gen_rom.py`, for `shared/tb/stream_tb.vh` unchanged:
- 16-byte records;
- header `A5, count_hi, count_lo`;
- per case: `[0]` beats, `[1..12]` s_data, `[13]` beat 0, `[14]` beat 1, `[15]` latency.

`stream_tb.vh` holds at most **1023** cases (`MAXREC = 1024`), and `gen.py` asserts that limit.

The **same 931 inputs** are used for every format; only the expected values differ:

| group | cases |
|---|---|
| held-out test images 0..599 | 600 |
| further test images where a non-binary format disagrees with fp32 | 37 |
| all pixels = v for v = 0..15 (all-zero, all-15, the 7/8 binarisation edge) | 16 |
| single bright pixel (15 on 0), single dark pixel (0 on 15), 8 on a background of 7, for each of the 9 positions | 27 |
| ideal vertical bar, ideal horizontal bar, two checkerboards | 4 |
| sum-maximising and sum-minimising image for fp32 and each format (15 where the weight is positive, else 0, and the opposite) | 16 |
| uniform-random images with the smallest \|fp32 sum\| (decision boundary, from 20000 with LCG seed 7) | 120 |
| uniform-random images | 60 |
| short frames, 1..8 beats | 8 |
| long frames, 10, 11, 12 beats (one with a bad beat past 9) | 3 |
| out-of-range item at each of the 9 positions with s_data = 0x10, 0x1F, 0x80, 0xFF | 36 |
| short frame with a bad item; a 1-beat bad frame; 9 bad beats; 12 bad beats | 4 |

Every format sees 51 error cases and 10 (bin) to 249 (bf16) distinct beat-1 values.

## 9. Results (`python3 model/precision_hw/report.py`, 2000 held-out images)

| fmt | bits/weight | parameter bits (9 w + bias/T) | accumulator | test accuracy | same decision as fp32 | bits moved / inference |
|---|---|---|---|---|---|---|
| fp32 (reference) | 32 | 320 | exact | 94.05 % | 100.00 % | 356 |
| bin  | 1  | 13  | 4-bit count | 88.95 % | 90.30 % | 22 |
| tern | 2  | 27  | 10-bit int  | 94.15 % | 98.80 % | 63 |
| int4 | 4  | 47  | 12-bit int  | 94.25 % | 98.90 % | 83 |
| int8 | 8  | 88  | 17-bit int  | 94.05 % | 100.00 % | 124 |
| fp8  | 8  | 80  | fp16        | 94.15 % | 98.30 % | 116 |
| fp16 | 16 | 160 | fp16        | 94.05 % | 100.00 % | 196 |
| bf16 | 16 | 160 | bf16        | 94.00 % | 99.95 % | 196 |

- **Bits moved** = input bits into the MAC (9 x 1 for bin, 9 x 4 otherwise) plus parameter bits read once. The
  pins carry 72 bits in and 16 bits out for every format.
- Float roundings that changed a value: about 0.31 per inference for fp8 (sums only, since its products are
  exact) and about 10.9 for fp16 and bf16.
- Differences of a few tenths of a point in accuracy are noise at 2000 images. The "same decision" column is the
  sharper measure.
- **Only bin loses real accuracy.** A sign-only weight cannot express "this pixel hardly matters", so the pixels
  with near-zero weight (corners, centre) vote with full strength.

## 10. Expected hardware (EXPECTATION, not measurement)

**Datapath.**
- `bin` should be the smallest: an XNOR, a 4-bit counter and a 4-bit compare, comparable to `vision_block`.
- `tern` needs only a 10-bit add/subtract.
- `int4` and `int8` add a 4x4 and an 8x4 signed-by-unsigned multiplier, plus 12- and 17-bit adders. int8 is
  maybe 2x int4.
- `fp8` stores 8-bit weights like int8, and its multiplier is tiny (4x4 significands, no rounding). It still pays
  for a full fp16 adder (exponent compare, an alignment shifter, a 13+ bit add with guard/round/sticky,
  leading-zero count, normalising shifter, RNE incrementer). It is expected to be clearly larger than int8: the
  accumulator, not the weight, sets float cost.
- `fp16` adds an 11x4 multiplier and a product rounder on top of that, and should be the largest.
- `bf16` has the same storage as fp16 but 8-bit significands (narrower multiplier and shifters) and wider exponent
  logic. It is expected to be somewhat smaller than fp16.

**Flip-flops.** Roughly the accumulator width (4 to 17) plus the input stage (6 to 9) plus about 8 of control.
The flip-flop count tracks the accumulator width, not the weight width.

**The weights do not appear as flip-flops or memory.** They are constants, and synthesis folds them into logic,
often simplifying the multiplier per constant. Measured area therefore reflects hard-wired weights, not an SRAM.
The "parameter bits" column is the memory a programmable version would need.

**Wiring and timing.** Routed wirelength and vias should scale with datapath width, ordered roughly as the
datapath area. All formats should meet 25 ns at the typical corner. fp16 and bf16 carry the only paths worth
watching (section 6).

---

## 11. Checklist for the RTL agents

1. Pins and protocol exactly as `vision_block` (section 5). Ports of `prec_<fmt>_rom` as in section 7. The
   testbench is `designs/prec_<fmt>/tb/prec_<fmt>_tb.v` with ``` `define DUT prec_<fmt> ``` and
   ``` `include "stream_tb.vh" ```, run with `+VEC=designs/prec_<fmt>/tb/vectors.hex -I shared/tb`.
2. Bias first, raster order, one MAC step per used item, unused items skipped. Accumulator reloads `BIAS`
   (bin: 0) after the result is taken and on reset.
3. Integers: zero-extend the pixel; accumulator widths 10 / 12 / 17; `class = ~acc[MSB]`.
4. Floats: FTZ on inputs; product rounded to the accumulator format (fp8: exact in fp16); sum RNE with
   guard/round/sticky; results below the smallest normal become `+0`; exact zero is `+0`; no Inf/NaN logic.
   `class = ~acc[15]`.
5. beat 1 = XOR-fold of the accumulator (section 5.2). Latency 2.
6. If a vector fails, compare with `python3 model/precision_hw/golden.py --trace <fmt> p0 .. p8`.
