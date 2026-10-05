#!/usr/bin/env python3
"""golden.py -- the precision study in silicon: data, fp32 training, quantisation and the BIT-EXACT reference of the
seven engines designs/prec_<fmt> (fmt in FMTS). The contract is model/precision_hw/spec.md; this file implements it.

  params()                    -> dict fmt -> quantised parameters (integer codes / bit patterns), plus 'fp32'
  infer(fmt, pixels, p)       -> (cls, acc_code, acc_value)   one inference on 9 pixels (0..15) -- the datapath
  run(fmt, beats, p)          -> (beat0, beat1, latency)      one stream frame (s_data of every beat) -- the engine
  golden.py --check           self-checks (float rounding vs struct 'e' / bf16 bit trick, integer reference,
                              range proofs on every value the datapath sees); exit 1 on any failure
  golden.py <fmt> s0 s1 ...   print one stream result, e.g.  golden.py fp16 3 12 2 1 13 4 0 15 3
  golden.py --trace <fmt> p0 .. p8   print every MAC step (operands, accumulator bits) of one inference

Python 3.9 standard library only. Deterministic: integer LCG data, training with + - * / only (own exp), all
quantisation and all hardware arithmetic in exact rationals (fractions.Fraction) with explicit RNE rounding.
"""
import math
import struct
import sys
sys.dont_write_bytecode = True     # keep the repository free of __pycache__
from fractions import Fraction as F

FMTS = ("bin", "tern", "int4", "int8", "fp8", "fp16", "bf16")
N = 9                  # inputs (3x3 image, raster order)
PIX_MAX = 15           # 4-bit unsigned pixel
LATENCY = {f: (3 if f in ("fp8", "fp16", "bf16") else 2) for f in FMTS}   # spec.md section 6: float MAC = 2 stages

# ------------------------------------------------------------------------------------------------ data
SEED_TRAIN, SEED_TEST = 1, 2
N_TRAIN, N_TEST = 400, 2000
LEVEL_ON, LEVEL_OFF, P_FLIP, NOISE_SD = 12.0, 3.0, 0.15, 2.0


class LCG:
    """31-bit linear congruential generator (same constants as model/examples/precision.py)."""
    def __init__(self, seed):
        self.s = seed

    def u(self):
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s / 0x80000000          # exact: 31-bit integer / 2^31

    def gauss(self):                        # approx. N(0,1): Irwin-Hall sum of 12 uniforms
        return sum(self.u() for _ in range(12)) - 6.0


def make_image(rng, label):
    """3x3 image of 4-bit pixels. label 1: vertical bar (centre column), 0: horizontal bar (centre row).
    Each pixel: level 12 if on the bar else 3; the on/off state is flipped with probability 0.15; Gaussian-like
    noise (sd 2.0) is added; rounded half-up and clamped to 0..15."""
    img = []
    for i in range(N):
        r, c = divmod(i, 3)
        on = (c == 1) if label else (r == 1)
        if rng.u() < P_FLIP:
            on = not on
        v = (LEVEL_ON if on else LEVEL_OFF) + NOISE_SD * rng.gauss()
        img.append(max(0, min(PIX_MAX, int(math.floor(v + 0.5)))))
    return img


def make_set(seed, n):
    rng = LCG(seed)
    xs, ys = [], []
    for i in range(n):
        y = i % 2                           # alternating labels: exactly balanced
        xs.append(make_image(rng, y))
        ys.append(y)
    return xs, ys


def dataset():
    return make_set(SEED_TRAIN, N_TRAIN), make_set(SEED_TEST, N_TEST)

# ------------------------------------------------------------------------------------------------ fp32 training
LN2 = 0.6931471805599453


def det_exp(z):
    """exp(z) with + - * / and ldexp only (bit-identical on every IEEE-754 platform, unlike libm)."""
    k = int(math.floor(z / LN2 + 0.5))
    r = z - k * LN2
    term, s = 1.0, 1.0
    for i in range(1, 22):
        term = term * r / i
        s += term
    return math.ldexp(s, k)


def sigmoid(z):
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + det_exp(-z))


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def train(xs, ys, epochs=1500, lr=0.5):
    """Logistic regression, full-batch gradient descent from zero, on x/16 (an exact power-of-two input scale,
    folded back exactly into the weights afterwards). Computed in double, final parameters rounded to fp32: the
    fp32 reference model."""
    X = [[v / 16.0 for v in x] for x in xs]
    w, b, n = [0.0] * N, 0.0, len(xs)
    for _ in range(epochs):
        gw, gb = [0.0] * N, 0.0
        for x, y in zip(X, ys):
            e = sigmoid(sum(wi * xi for wi, xi in zip(w, x)) + b) - y
            for i in range(N):
                gw[i] += e * x[i]
            gb += e
        for i in range(N):
            w[i] -= lr * gw[i] / n
        b -= lr * gb / n
    return [f32(v / 16.0) for v in w], f32(b)

# ------------------------------------------------------------------------------------------------ float formats
# name: (exponent bits, mantissa bits, exponent bias, finite_only)   finite_only = OCP E4M3 (no Inf, S.1111.111 = NaN)
FLOATS = {"fp16": (5, 10, 15, False), "bf16": (8, 7, 127, False), "e4m3": (4, 3, 7, True)}


def fparams(name):
    E, M, B, fo = FLOATS[name]
    emin = 1 - B                                        # exponent of the smallest normal
    emax = ((1 << E) - 1 - B) if fo else ((1 << E) - 2 - B)
    maxfin = F(2 ** (M + 1) - (2 if fo else 1), 2 ** M) * F(2) ** emax
    return E, M, B, emin, emax, maxfin


def ilog2(q):
    """floor(log2(q)) for a positive Fraction, exactly."""
    n, d = q.numerator, q.denominator
    e = n.bit_length() - d.bit_length()
    if (n << max(0, -e)) < (d << max(0, e)):
        e -= 1
    return e


def rne_int(q):
    """Round a Fraction to the nearest integer, ties to even."""
    f = q.numerator // q.denominator
    r = q - f
    if r > F(1, 2) or (r == F(1, 2) and f & 1):
        f += 1
    return f


def round_ftz(name, q, stats=None):
    """THE float rounding rule of spec.md 4.4: round the exact value q to the format's precision (M+1 significant
    bits) with round-to-nearest-even and an unbounded exponent; a result below the smallest normal becomes +0
    (flush to zero, sign dropped); an exact zero is +0. Overflow beyond the largest finite value is an error here
    (spec.md 4.6 proves it cannot happen) and raises."""
    E, M, B, emin, emax, maxfin = fparams(name)
    if q == 0:
        return F(0)
    a = abs(q)
    e = ilog2(a)
    n = rne_int(a / F(2) ** (e - M))            # integer significand in [2^M, 2^(M+1)]
    if n == 1 << (M + 1):
        n, e = n >> 1, e + 1
    r = F(n) * F(2) ** (e - M)
    if stats is not None:
        stats["inexact"] += (r != a)
    if e < emin:
        if stats is not None:
            stats["ftz"] += 1
        return F(0)
    if r > maxfin:
        raise OverflowError("%s overflow: %s" % (name, float(q)))
    return r if q > 0 else -r


def f_encode(name, v):
    """Bit pattern of v, which must already be a normal value of the format or zero (zero encodes as +0)."""
    E, M, B, emin, emax, maxfin = fparams(name)
    if v == 0:
        return 0
    s = 1 if v < 0 else 0
    a = abs(v)
    e = ilog2(a)
    assert emin <= e <= emax and a <= maxfin, "not a normal value"
    m = a / F(2) ** (e - M) - (1 << M)
    assert m.denominator == 1, "not representable"
    return (s << (E + M)) | ((e + B) << M) | int(m)


def f_decode(name, bits):
    """Value of a bit pattern with FTZ on inputs: exponent field 0 (zero or subnormal) reads as +0. Inf/NaN
    patterns raise (spec.md 4.6: unreachable)."""
    E, M, B, emin, emax, maxfin = fparams(name)
    s, ef, mf = bits >> (E + M) & 1, bits >> M & ((1 << E) - 1), bits & ((1 << M) - 1)
    if ef == 0:
        return F(0)
    if (FLOATS[name][3] and ef == (1 << E) - 1 and mf == (1 << M) - 1) or (not FLOATS[name][3] and ef == (1 << E) - 1):
        raise ValueError("Inf/NaN pattern %x" % bits)
    v = F((1 << M) + mf) * F(2) ** (ef - B - M)
    return -v if s else v


FLOAT_FMT = {"fp8": ("e4m3", "fp16"), "fp16": ("fp16", "fp16"), "bf16": ("bf16", "bf16")}  # (weight fmt, acc fmt)

# ------------------------------------------------------------------------------------------------ integer formats
INT_QMAX = {"tern": 1, "int4": 7, "int8": 127}
WBITS = {"bin": 1, "tern": 2, "int4": 4, "int8": 8, "fp8": 8, "fp16": 16, "bf16": 16}


def int_smax(fmt):
    return N * PIX_MAX * INT_QMAX[fmt]           # largest |sum of products|


def sbits(lo, hi):
    """Width of the smallest two's-complement integer holding lo..hi."""
    w = 1
    while not (-(1 << (w - 1)) <= lo and hi <= (1 << (w - 1)) - 1):
        w += 1
    return w


def int_widths(fmt):
    """(bias bits, accumulator bits) of spec.md 4.2: bias clamped to [-Smax-1, Smax]; accumulator holds
    bias + any sum of products, i.e. [-2*Smax-1, 2*Smax]."""
    s = int_smax(fmt)
    return sbits(-s - 1, s), sbits(-2 * s - 1, 2 * s)


BIN_T_BITS = 4      # binary threshold 0..10
ACC_BITS = {"bin": 4, "fp8": 16, "fp16": 16, "bf16": 16}
for _f in INT_QMAX:
    ACC_BITS[_f] = int_widths(_f)[1]
BIAS_BITS = {"bin": BIN_T_BITS, "fp8": 8, "fp16": 16, "bf16": 16}
for _f in INT_QMAX:
    BIAS_BITS[_f] = int_widths(_f)[0]

# ------------------------------------------------------------------------------------------------ quantisation
_CACHE = {}


def quantise(w, b, train_xs, train_ys):
    """All seven parameter sets from the fp32 weights (spec.md section 3). Integer codes; float bit patterns."""
    W = [F(v) for v in w]
    B = F(b)
    P = {"fp32": {"w": list(w), "b": b}}
    # binary: sign bits; threshold fitted on the binarised TRAINING set
    sb = [1 if v >= 0 else 0 for v in W]
    best = None
    for T in range(0, N + 2):
        acc = sum(int(sum(1 for i in range(N) if (x[i] >= 8) == bool(sb[i])) >= T) == y
                  for x, y in zip(train_xs, train_ys))
        if best is None or acc > best[0]:       # strict: the smallest T among the best wins
            best = (acc, T)
    P["bin"] = {"w": sb, "b": best[1]}
    # ternary (TWN): delta = 0.7 * mean|w|; alpha = mean |w| of the kept weights; bias in units of alpha
    delta = F(7, 10) * sum(abs(v) for v in W) / N
    qt = [1 if v > delta else (-1 if v < -delta else 0) for v in W]
    kept = [abs(v) for v, q in zip(W, qt) if q]
    alpha = sum(kept) / len(kept)
    s = int_smax("tern")
    P["tern"] = {"w": qt, "b": max(-s - 1, min(s, rne_int(B / alpha))), "scale": alpha, "delta": delta}
    # int4 / int8: symmetric per-tensor scale from the 9 weights; bias in the same units
    for f in ("int4", "int8"):
        qm = INT_QMAX[f]
        scale = max(abs(v) for v in W) / qm
        q = [rne_int(v / scale) for v in W]
        assert all(-qm <= v <= qm for v in q)
        s = int_smax(f)
        P[f] = {"w": q, "b": max(-s - 1, min(s, rne_int(B / scale))), "scale": scale, "b_unclamped": rne_int(B / scale)}
    # floats: RNE to the weight format, FTZ (a subnormal result is stored as +0)
    for f, (wf, af) in FLOAT_FMT.items():
        P[f] = {"w": [f_encode(wf, round_ftz(wf, v)) for v in W], "b": f_encode(wf, round_ftz(wf, B))}
    return P


def params():
    if "p" not in _CACHE:
        (trx, try_), _ = dataset()
        w, b = train(trx, try_)
        _CACHE["p"] = quantise(w, b, trx, try_)
    return _CACHE["p"]

# ------------------------------------------------------------------------------------------------ the datapath
def fold8(code, width):
    """beat 1 (spec.md 5.2): the accumulator register's raw W-bit pattern (two's complement for integers, IEEE-style
    bits for floats, the unsigned count for bin), ZERO-extended to whole bytes, all bytes XORed together."""
    nbytes = (width + 7) // 8
    out = 0
    for k in range(nbytes):
        out ^= (code >> (8 * k)) & 0xFF
    return out


def infer(fmt, x, p=None, stats=None):
    """One inference on 9 pixels (0..15; a pixel of None = item not used: missing or out of range). Applies the
    MAC rule in raster order with the bias loaded first. Returns (class, accumulator code, accumulator value)."""
    p = p or params()
    q = p[fmt]
    if fmt == "bin":
        m = 0                                               # accumulator: match count, starts at 0
        for i in range(N):
            if x[i] is None:
                continue                                    # unused item: no MAC
            a = 1 if x[i] >= 8 else 0                       # input binarised: pixel[3]
            m += 1 if a == q["w"][i] else 0                 # XNOR, then count
        return int(m >= q["b"]), m, m
    if fmt in INT_QMAX:
        W = ACC_BITS[fmt]
        acc = q["b"]                                        # bias first
        for i in range(N):
            if x[i] is None:
                continue
            acc += q["w"][i] * x[i]                         # exact
            assert -(1 << (W - 1)) <= acc < (1 << (W - 1))
        return int(acc >= 0), acc & ((1 << W) - 1), acc
    wf, af = FLOAT_FMT[fmt]
    acc = f_decode(wf, q["b"])                              # bias first (fp8: E4M3 -> fp16 is exact)
    for i in range(N):
        if x[i] is None or x[i] == 0:
            continue                                        # zero product: accumulator unchanged (spec.md 4.4)
        wv = f_decode(wf, q["w"][i])
        if wv == 0:
            continue
        prod = round_ftz(af, wv * x[i], stats)              # product rounded to the ACCUMULATOR format
        if stats is not None:
            stats["mul"] += 1
        acc = round_ftz(af, acc + prod, stats)              # sum rounded to the accumulator format
        if stats is not None:
            stats["add"] += 1
    code = f_encode(af, acc)
    assert code >> 15 == (1 if acc < 0 else 0)
    return int(acc >= 0), code, acc


def run(fmt, beats, p=None):
    """One stream frame -> (beat0, beat1, latency). beats: s_data of every beat (s_last on the last).
    error: frame length != 9, or a beat with s_data[7:4] != 0 (that item is not used). Beats past 9: ignored."""
    error = len(beats) != N or any(v > PIX_MAX for v in beats[:N] + beats[N:])
    x = [None] * N
    for i, v in enumerate(beats[:N]):
        if v <= PIX_MAX:
            x[i] = v
    cls, code, _ = infer(fmt, x, p)
    return (int(error) << 1) | cls, fold8(code, ACC_BITS[fmt]), LATENCY[fmt]


def ref_fp32(x, p=None):
    """fp32 reference decision: fp32 weights and bias, exact sum (no rounding at all), class = sum >= 0."""
    q = (p or params())["fp32"]
    s = F(q["b"]) + sum(F(wi) * xi for wi, xi in zip(q["w"], x))
    return int(s >= 0), s

# ------------------------------------------------------------------------------------------------ self-checks
def check():
    ok = True

    def expect(label, cond):
        nonlocal ok
        ok &= bool(cond)
        print("  %-70s %s" % (label, "ok" if cond else "FAIL"))

    p = params()
    # 1. float rounding against independent references
    import random
    rnd = random.Random(12345)
    bad = n = 0
    for _ in range(20000):                       # fp16: round_ftz vs struct 'e' on exact doubles in normal range
        v = rnd.uniform(-1, 1) * 2.0 ** rnd.randint(-13, 15)
        if abs(v) < 2.0 ** -14 or abs(v) > 65504:
            continue
        ref = struct.unpack("<e", struct.pack("<e", v))[0]
        if abs(ref) < 2.0 ** -14:
            continue
        n += 1
        bad += float(round_ftz("fp16", F(v))) != ref
    expect("fp16 RNE vs struct 'e' on %d random doubles" % n, bad == 0)

    def bf16_trick(v):                           # fp32 bits -> top 16 bits, RNE (as model/examples/precision.py)
        u = struct.unpack("<I", struct.pack("<f", v))[0]
        r = (u + 0x7FFF + ((u >> 16) & 1)) >> 16
        return struct.unpack("<f", struct.pack("<I", r << 16))[0]
    bad = n = 0
    for _ in range(20000):
        v = f32(rnd.uniform(-1, 1) * 2.0 ** rnd.randint(-40, 40))
        if v == 0:
            continue
        n += 1
        bad += float(round_ftz("bf16", F(v))) != bf16_trick(v)
    expect("bf16 RNE vs fp32 top-16-bit trick on %d random fp32 values" % n, bad == 0)
    sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "examples"))
    import precision as ex
    bad = n = 0
    for _ in range(20000):
        v = rnd.uniform(-1, 1) * 2.0 ** rnd.randint(-6, 8)
        if not 2.0 ** -6 <= abs(v) <= 448:
            continue
        n += 1
        bad += float(round_ftz("e4m3", F(v))) != ex.q_float("fp8 E4M3", v)
    expect("E4M3 RNE vs model/examples/precision.py encoder on %d values" % n, bad == 0)
    expect("ties to even: fp16(1+2^-11)=1, fp16(1+3*2^-11)=1+2^-9",
           round_ftz("fp16", 1 + F(1, 2 ** 11)) == 1 and round_ftz("fp16", 1 + F(3, 2 ** 11)) == 1 + F(1, 2 ** 9))
    expect("FTZ: fp16(2^-15) = +0, bf16(2^-127) = +0, fp16(-2^-15) = +0",
           round_ftz("fp16", F(1, 2 ** 15)) == 0 and round_ftz("bf16", F(1, 2 ** 127)) == 0
           and round_ftz("fp16", -F(1, 2 ** 15)) == 0)
    expect("encode/decode: fp16 1.0=3c00, -2.0=c000; bf16 1.0=3f80; E4M3 448=7e, 1.0=38",
           f_encode("fp16", F(1)) == 0x3C00 and f_encode("fp16", F(-2)) == 0xC000 and f_encode("bf16", F(1)) == 0x3F80
           and f_encode("e4m3", F(448)) == 0x7E and f_encode("e4m3", F(1)) == 0x38)
    expect("every pixel 0..15 exact in E4M3, fp16, bf16",
           all(round_ftz(f, F(v)) == v for f in ("e4m3", "fp16", "bf16") for v in range(16)))

    # 2. every datapath operation of every format over the test set and edge cases, re-checked independently
    (_, _), (tex, tey) = dataset()
    pool = tex + [[v] * N for v in range(16)] + [[15 * (j == i) for j in range(N)] for i in range(N)]
    bad_int = bad_e = bad_b = n_e = n_b = 0
    for x in pool:
        for f in ("tern", "int4", "int8"):       # integer: straightforward reference (Python ints, no widths)
            c, code, acc = infer(f, x, p)
            ref = p[f]["b"] + sum(a * b for a, b in zip(p[f]["w"], x))
            bad_int += (acc != ref) or (c != int(ref >= 0)) or code != ref % (1 << ACC_BITS[f])
        c, code, m = infer("bin", x, p)
        bad_int += m != sum(1 for i in range(N) if (x[i] >> 3) == p["bin"]["w"][i])
        # fp16 / fp8: replay the MAC in doubles (exact: all values here are short dyadics) rounded by struct 'e'
        for f in ("fp16", "fp8"):
            wf = FLOAT_FMT[f][0]
            acc = float(f_decode(wf, p[f]["b"]))
            for i in range(N):
                wv = float(f_decode(wf, p[f]["w"][i]))
                if x[i] and wv:
                    pr = struct.unpack("<e", struct.pack("<e", wv * x[i]))[0]
                    acc = struct.unpack("<e", struct.pack("<e", acc + pr))[0]
                    acc = 0.0 if abs(acc) < 2.0 ** -14 else acc
                    n_e += 2
            bad_e += float(infer(f, x, p)[2]) != acc
        # bf16: replay in fp32 (exact here) rounded by the top-16-bit trick
        acc = float(f_decode("bf16", p["bf16"]["b"]))
        for i in range(N):
            wv = float(f_decode("bf16", p["bf16"]["w"][i]))
            if x[i] and wv:
                assert f32(wv * x[i]) == wv * x[i]
                pr = bf16_trick(wv * x[i])
                assert f32(acc + pr) == acc + pr
                acc = bf16_trick(acc + pr)
                acc = 0.0 if abs(acc) < 2.0 ** -126 else acc
                n_b += 2
        bad_b += float(infer("bf16", x, p)[2]) != acc
    expect("tern/int4/int8/bin datapath == plain integer reference (%d images)" % len(pool), bad_int == 0)
    expect("fp16 + fp8 datapath == double replay rounded by struct 'e' (%d ops)" % n_e, bad_e == 0)
    expect("bf16 datapath == fp32 replay rounded by top-16-bit trick (%d ops)" % n_b, bad_b == 0)

    # 3. range proofs (spec.md 4.6) evaluated on the actual parameters: worst-case magnitudes, all 16^9 inputs
    for f in ("fp8", "fp16", "bf16"):
        wf, af = FLOAT_FMT[f]
        ws = [abs(f_decode(wf, c)) for c in p[f]["w"]]
        bound = abs(f_decode(wf, p[f]["b"])) + 15 * sum(ws)
        # each rounding adds at most half an ulp relative: bound * (1 + 2^-(M+1))^(2*9) stays far below max
        M = FLOATS[af][1]
        bound_r = bound * (1 + F(1, 2 ** (M + 1))) ** 18
        minp = min([v for v in ws if v] or [F(1)])
        expect("%s: |acc| <= %.4g < max finite %.4g; smallest product %.3g >= min normal" %
               (f, float(bound_r), float(fparams(af)[5]), float(minp)),
               bound_r < fparams(af)[5] and minp >= F(2) ** fparams(af)[3])
    for f in ("tern", "int4", "int8"):
        lo = p[f]["b"] - 15 * sum(-v for v in p[f]["w"] if v < 0)
        hi = p[f]["b"] + 15 * sum(v for v in p[f]["w"] if v > 0)
        W = ACC_BITS[f]
        expect("%s: acc range [%d, %d] fits %d-bit signed (spec worst case %d bits)" % (f, lo, hi, W, W),
               -(1 << (W - 1)) <= lo and hi < (1 << (W - 1)))
    # 4. protocol corner: an unused item equals pixel 0 in every format
    x0 = tex[0]
    same = all(infer(f, [None if i == 4 else v for i, v in enumerate(x0)], p)[1] ==
               infer(f, [0 if i == 4 else v for i, v in enumerate(x0)], p)[1] for f in FMTS if f != "bin")
    expect("an unused item (missing / out of range) == pixel 0 (all formats but bin)", same)
    print("golden: all self-checks passed" if ok else "golden: SELF-CHECK FAILURE")
    return ok


def trace(fmt, x, p=None):
    """Print every MAC step of one inference: the operands and the accumulator code after each step."""
    p = p or params()
    q = p[fmt]
    W = ACC_BITS[fmt]
    hx = lambda c: "%0*x" % ((W + 3) // 4, c)
    print("%s  pixels %s" % (fmt, x))
    if fmt in FLOAT_FMT:
        wf, af = FLOAT_FMT[fmt]
        acc = f_decode(wf, q["b"])
        print("  load bias  %s = %-14.9g acc %s" % ("%02x" % q["b"] if wf == "e4m3" else "%04x" % q["b"], float(acc),
                                                     hx(f_encode(af, acc))))
        for i in range(N):
            wv = f_decode(wf, q["w"][i])
            if not x[i] or wv == 0:
                print("  i=%d  x=%2d  w=%-12.9g zero product: acc unchanged %s" % (i, x[i], float(wv), hx(f_encode(af, acc))))
                continue
            pr = round_ftz(af, wv * x[i])
            acc = round_ftz(af, acc + pr)
            print("  i=%d  x=%2d  w=%-12.9g prod %s = %-12.9g acc %s = %.9g" % (
                i, x[i], float(wv), hx(f_encode(af, pr)), float(pr), hx(f_encode(af, acc)), float(acc)))
    else:
        c, code, acc = infer(fmt, x, p)
        print("  integer: acc = %d (code %s)" % (acc, hx(code)))
    c, code, _ = infer(fmt, x, p)
    print("  class %d  beat1 %02x" % (c, fold8(code, W)))


def main(argv):
    if argv[1:2] == ["--check"]:
        sys.exit(0 if check() else 1)
    if argv[1:2] == ["--trace"] and len(argv) == 12:
        trace(argv[2], [int(v, 0) for v in argv[3:]])
        return
    if len(argv) >= 2 and argv[1] in FMTS:
        beats = [int(v, 0) for v in argv[2:]]
        b0, b1, lat = run(argv[1], beats)
        print("%s: beat0=%02x (error %d, class %d) beat1=%02x latency=%d" % (argv[1], b0, b0 >> 1, b0 & 1, b1, lat))
        return
    print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
