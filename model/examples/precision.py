#!/usr/bin/env python3
"""Number precision on a real tiny model (standard library only, deterministic).

Formats: fp32 (reference), bf16, fp16, fp8 E4M3, fp8 E5M2, int8, int4, 1-bit.
Run: python3 model/examples/precision.py
"""
import math
import struct

# ---------------------------------------------------------------- encoders
def f32(x):
    """Round a Python float to the nearest fp32 value."""
    return struct.unpack('<f', struct.pack('<f', x))[0]

def _rne(q):
    return int(round(q))          # Python round() on floats is half-to-even

def enc_float(x, E, M, bias, finite_only=False):
    """Encode x into (sign, exp_field, mant_field), round-to-nearest-even.
    finite_only=True is E4M3: no infinity, overflow saturates to 448."""
    if math.isnan(x):
        return (0, (1 << E) - 1, (1 << M) - 1 if finite_only else 1 << (M - 1))
    s = 1 if (x < 0 or (x == 0 and math.copysign(1, x) < 0)) else 0
    a = abs(x)
    if math.isinf(a):
        a = 1e300
    if a == 0:
        return (s, 0, 0)
    ex = math.frexp(a)[1] - 1                 # a = 1.xxx * 2**ex
    ex = max(ex, 1 - bias)                    # below this: subnormal
    n = _rne(a / 2.0 ** (ex - M))             # integer count of quanta
    if n >= 1 << (M + 1):                     # rounding carried into next binade
        ex += 1
        n >>= 1
    if n < 1 << M:                            # subnormal (or zero)
        return (s, 0, n)
    ef, mf = ex + bias, n - (1 << M)
    if finite_only:                           # E4M3: max is S.1111.110 = 448
        if ef > (1 << E) - 1 or (ef == (1 << E) - 1 and mf == (1 << M) - 1):
            return (s, (1 << E) - 1, (1 << M) - 2)
    elif ef >= (1 << E) - 1:                  # IEEE-like: overflow -> infinity
        return (s, (1 << E) - 1, 0)
    return (s, ef, mf)

def dec_float(t, E, M, bias, finite_only=False):
    s, ef, mf = t
    sg = -1.0 if s else 1.0
    top = (1 << E) - 1
    if finite_only:
        if ef == top and mf == (1 << M) - 1:
            return float('nan')
    elif ef == top:
        return sg * float('inf') if mf == 0 else float('nan')
    if ef == 0:
        return sg * mf * 2.0 ** (1 - bias - M)
    return sg * (1 + mf / (1 << M)) * 2.0 ** (ef - bias)

FORMATS = {   # name: (E, M, bias, finite_only)
    'bf16':     (8, 7, 127, False),
    'fp16':     (5, 10, 15, False),
    'fp8 E4M3': (4, 3, 7, True),
    'fp8 E5M2': (5, 2, 15, False),
}

def q_float(name, x):
    E, M, b, fo = FORMATS[name]
    return dec_float(enc_float(x, E, M, b, fo), E, M, b, fo)

def bits(name, x):
    E, M, b, fo = FORMATS[name]
    s, ef, mf = enc_float(x, E, M, b, fo)
    return '%d | %s | %s' % (s, format(ef, '0%db' % E), format(mf, '0%db' % M))

def fp32_bits(x):
    u = struct.unpack('<I', struct.pack('<f', x))[0]
    return '%d | %s | %s' % (u >> 31, format((u >> 23) & 255, '08b'), format(u & 0x7FFFFF, '023b'))

def int_quant(ws, nbits):
    """Symmetric per-tensor: scale = max|w| / (2**(nbits-1)-1)."""
    qmax = (1 << (nbits - 1)) - 1
    scale = max(abs(w) for w in ws) / qmax
    q = [max(-qmax, min(qmax, _rne(w / scale))) for w in ws]
    return q, scale, [v * scale for v in q]

def binarize(ws):
    alpha = sum(abs(w) for w in ws) / len(ws)
    return [1 if w >= 0 else -1 for w in ws], alpha, [alpha if w >= 0 else -alpha for w in ws]

# ------------------------------------------------------------- self-tests
def self_test():
    print('=' * 72)
    print('1. ENCODER SELF-TESTS (bit-exact, round-to-nearest-even)')
    print('=' * 72)
    ok = True
    def check(label, got, want):
        nonlocal ok
        good = (got == want) or (isinstance(got, float) and math.isnan(got) and math.isnan(want))
        ok &= good
        print('  %-44s got %-14r want %-14r %s' % (label, got, want, 'ok' if good else 'FAIL'))
    check('fp16(1.0)', q_float('fp16', 1.0), 1.0)
    check('fp16(0.1) == struct half', q_float('fp16', 0.1), struct.unpack('<e', struct.pack('<e', 0.1))[0])
    check('fp16(65504) max', q_float('fp16', 65504.0), 65504.0)
    check('fp16(70000) overflow', q_float('fp16', 70000.0), float('inf'))
    check('fp16 smallest subnormal 2^-24', q_float('fp16', 2.0 ** -24), 2.0 ** -24)
    check('fp16(2^-25) tie -> 0 (even)', q_float('fp16', 2.0 ** -25), 0.0)
    check('fp16(1+2^-11) tie -> 1.0 (even)', q_float('fp16', 1 + 2.0 ** -11), 1.0)
    check('fp16(1+3*2^-11) tie -> 1+2^-9 (even)', q_float('fp16', 1 + 3 * 2.0 ** -11), 1 + 2.0 ** -9)
    check('bf16(1.0)', q_float('bf16', 1.0), 1.0)
    check('bf16(1+2^-8) tie -> 1.0 (even)', q_float('bf16', 1 + 2.0 ** -8), 1.0)
    check('bf16(70000)', q_float('bf16', 70000.0), 70144.0)
    # bf16 cross-check against "round fp32 bits to top 16" for many values
    bad = 0
    for i in range(1, 2000):
        x = f32(math.sin(i) * 10 ** ((i % 9) - 4))
        u = struct.unpack('<I', struct.pack('<f', x))[0]
        r = (u + 0x7FFF + ((u >> 16) & 1)) >> 16
        ref = struct.unpack('<f', struct.pack('<I', r << 16))[0]
        bad += (q_float('bf16', x) != ref)
    check('bf16 vs fp32-top-16-bit trick (2000 values)', bad, 0)
    bad = 0
    for i in range(1, 2000):
        x = math.sin(i) * 10 ** ((i % 7) - 3)
        if abs(x) < 60000:
            bad += (q_float('fp16', x) != struct.unpack('<e', struct.pack('<e', x))[0])
    check('fp16 vs struct "e" (2000 values)', bad, 0)
    check('E4M3(448) max', q_float('fp8 E4M3', 448.0), 448.0)
    check('E4M3(460) -> 448', q_float('fp8 E4M3', 460.0), 448.0)
    check('E4M3(1000) saturates', q_float('fp8 E4M3', 1000.0), 448.0)
    check('E4M3 smallest subnormal 2^-9', q_float('fp8 E4M3', 2.0 ** -9), 2.0 ** -9)
    check('E4M3(1.0625) tie -> 1.0 (even)', q_float('fp8 E4M3', 1.0625), 1.0)
    check('E4M3 S.1111.111 decodes to NaN', dec_float((0, 15, 7), 4, 3, 7, True), float('nan'))
    check('E5M2(57344) max', q_float('fp8 E5M2', 57344.0), 57344.0)
    check('E5M2(70000) overflow', q_float('fp8 E5M2', 70000.0), float('inf'))
    check('E5M2 smallest subnormal 2^-16', q_float('fp8 E5M2', 2.0 ** -16), 2.0 ** -16)
    check('E5M2(1.125) tie -> 1.0 (even)', q_float('fp8 E5M2', 1.125), 1.0)
    check('E5M2(1.375) tie -> 1.5 (even)', q_float('fp8 E5M2', 1.375), 1.5)
    q, sc, dq = int_quant([0.5, -1.0, 0.25], 8)
    check('int8 [0.5,-1,0.25]: ints', q, [64, -127, 32])
    q, sc, dq = int_quant([0.5, -1.0, 0.25], 4)
    check('int4 [0.5,-1,0.25]: ints', q, [4, -7, 2])
    check('binary [0.5,-1,0.25]: signs', binarize([0.5, -1.0, 0.25])[0], [1, -1, 1])
    print('  all self-tests passed' if ok else '  SELF-TEST FAILURE')
    assert ok

def worked_example():
    print()
    print('Worked example: the weight 0.1 in every format  (sign | exponent | mantissa)')
    print('  %-9s %-34s %s' % ('format', 'bits', 'stored value'))
    print('  %-9s %-34s %.10f' % ('fp32', fp32_bits(0.1), f32(0.1)))
    for n in ('bf16', 'fp16', 'fp8 E4M3', 'fp8 E5M2'):
        print('  %-9s %-34s %.10f' % (n, bits(n, 0.1), q_float(n, 0.1)))
    for nb in (8, 4):
        q, sc, dq = int_quant([0.1, -1.0], nb)
        print('  %-9s %-34s %.10f   (tensor [0.1, -1.0]: scale %.6f, integer %d)'
              % ('int%d' % nb, format(q[0] & ((1 << nb) - 1), '0%db' % nb), dq[0], sc, q[0]))
    q, a, dq = binarize([0.1, -1.0])
    print('  %-9s %-34s %.10f   (tensor [0.1, -1.0]: scale = mean|w| = %.3f)'
          % ('1-bit', '0 (positive)', dq[0], a))

def dynamic_range():
    print()
    print('=' * 72)
    print('2. DYNAMIC RANGE: the same number stored in each float format')
    print('=' * 72)
    print('  largest finite: ' + ', '.join('%s %g' % (n, dec_float((0, (1 << FORMATS[n][0]) - 1 - (0 if FORMATS[n][3] else 1),
          (1 << FORMATS[n][1]) - (2 if FORMATS[n][3] else 1)), *FORMATS[n])) for n in FORMATS))
    print('  smallest subnormal: ' + ', '.join('%s %.3g' % (n, 2.0 ** (1 - FORMATS[n][2] - FORMATS[n][1])) for n in FORMATS))
    print('  %-10s' % 'value' + ''.join('%-14s' % n for n in FORMATS))
    for v in (70000.0, 1e-6, 1e-9):
        print('  %-10g' % v + ''.join('%-14.6g' % q_float(n, v) for n in FORMATS))
    print('  (inf = overflow, 0 = underflow; E4M3 saturates to 448 instead of inf)')

# ------------------------------------------------------------- tiny model
class LCG:
    def __init__(self, seed):
        self.s = seed
    def u(self):
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s / 0x80000000
    def gauss(self):
        return sum(self.u() for _ in range(12)) - 6.0

N = 5
def make_image(rng, label):
    """5x5 grey image. label 1 = vertical bar (column 2), 0 = horizontal bar (row 2).
    Each pixel is flipped with prob 0.20, then Gaussian noise (sd 0.35) is added."""
    img = []
    for r in range(N):
        for c in range(N):
            on = (c == 2) if label else (r == 2)
            v = 1.0 if on else 0.0
            if rng.u() < 0.20:
                v = 1.0 - v
            img.append(v + 0.35 * rng.gauss())
    return img

def make_set(seed, n):
    rng = LCG(seed)
    xs, ys = [], []
    for i in range(n):
        y = i % 2
        xs.append(make_image(rng, y)); ys.append(y)
    return xs, ys

def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))

def train(xs, ys, epochs=400, lr=0.1):
    w = [0.0] * N * N
    b = 0.0
    n = len(xs)
    for _ in range(epochs):
        gw = [0.0] * len(w); gb = 0.0
        for x, y in zip(xs, ys):
            e = sigmoid(sum(wi * xi for wi, xi in zip(w, x)) + b) - y
            for i in range(len(w)):
                gw[i] += e * x[i]
            gb += e
        for i in range(len(w)):
            w[i] -= lr * gw[i] / n
        b -= lr * gb / n
    return w, b

def predict(w, b, xs):
    return [1 if sum(wi * xi for wi, xi in zip(w, x)) + b > 0 else 0 for x in xs]

def show_grid(title, w):
    print('  ' + title)
    for r in range(N):
        print('    ' + ' '.join('%6.2f' % w[r * N + c] for c in range(N)))

def main():
    self_test()
    worked_example()
    dynamic_range()

    print()
    print('=' * 72)
    print('3. A REAL TINY MODEL: 5x5 image, vertical bar (1) or horizontal bar (0)?')
    print('=' * 72)
    trx, try_ = make_set(1, 400)
    tex, tey = make_set(2, 2000)
    w, b = train(trx, try_)
    w = [f32(v) for v in w]; b = f32(b)       # fp32 reference weights
    print('  data: 400 train / 2000 test images, 25 pixels each, pixel flip prob 0.20, noise sd 0.35')
    print('  model: logistic regression, 25 weights + 1 bias, trained in fp32 (gradient descent)')
    print('  only the 25 weights are quantized; bias stays fp32; inputs and sums stay full precision')
    show_grid('trained fp32 weights (rows = image rows):', w)
    print('  bias = %.4f   max|w| = %.4f   mean|w| = %.4f'
          % (b, max(abs(v) for v in w), sum(abs(v) for v in w) / 25))
    ref = predict(w, b, tex)
    ref_acc = sum(p == y for p, y in zip(ref, tey)) / len(tey)
    tr_acc = sum(p == y for p, y in zip(predict(w, b, trx), try_)) / len(try_)
    print('  fp32 train accuracy %.1f%%, test accuracy %.1f%%' % (100 * tr_acc, 100 * ref_acc))

    rows = [('fp32', 32, w, 24)]
    for n in ('bf16', 'fp16', 'fp8 E4M3', 'fp8 E5M2'):
        rows.append((n, 1 + FORMATS[n][0] + FORMATS[n][1], [q_float(n, v) for v in w], FORMATS[n][1] + 1))
    _, s8, d8 = int_quant(w, 8)
    _, s4, d4 = int_quant(w, 4)
    sg, alpha, d1 = binarize(w)
    rows += [('int8', 8, d8, 8), ('int4', 4, d4, 4), ('1-bit', 1, d1, 1)]
    print('  int8 scale = %.6f, int4 scale = %.6f, 1-bit scale = mean|w| = %.6f' % (s8, s4, alpha))

    print()
    print('  %-9s %4s %9s %10s %10s %9s %10s %9s'
          % ('format', 'bits', 'mem bits', 'max|err|', 'mean|err|', 'test acc', 'same as32', 'mult PPs'))
    for name, nb, dq, pp in rows:
        errs = [abs(a - c) for a, c in zip(w, dq)]
        pred = predict(dq, b, tex)
        acc = sum(p == y for p, y in zip(pred, tey)) / len(tey)
        same = sum(p == r for p, r in zip(pred, ref)) / len(ref)
        print('  %-9s %4d %9d %10.6f %10.6f %8.2f%% %9.2f%% %9d'
              % (name, nb, nb * 25, max(errs), sum(errs) / 25, 100 * acc, 100 * same, pp * pp))
    print('  mem bits = 25 weights x bits (bias not counted). mult PPs = partial-product')
    print('  proxy: width^2 for int, (mantissa+1)^2 for float, 1 XNOR for 1-bit.')

    print()
    print('=' * 72)
    print('4. HARDWARE COST (proxy only, not a synthesized area)')
    print('=' * 72)
    print('  Storage bits and multiplier partial products are computed above. Real area also')
    print('  depends on adders, exponent logic, alignment, rounding and routing, none modelled here.')
    print('  Indicative measurement quoted from open-ai-silicon README (different networks!):')
    print('    bnn_mnist (1-bit) ~7,466 std cells;  cnn_fp16 (float16) 531,505 std cells')

if __name__ == '__main__':
    main()
