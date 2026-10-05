#!/usr/bin/env python3
"""audio.py -- computed, doc-level examples of AI on an audio stream (plan items 4.3 audio_pitch and 4.4
audio_onset in ../open-ai-silicon/docs/ARCH_STUDY_PLAN.md). Python 3.9 standard library only, fixed seed, no
downloads. These are NOT built as chips yet: the numbers below come from this script alone.
Run: python3 model/examples/audio.py"""
import itertools, random
from collections import deque

SEED = 12345
rng = random.Random(SEED)


def bits_unsigned(n):
    return max(1, n.bit_length())


def bits_signed(lo, hi):
    b = 1
    while not (-(1 << (b - 1)) <= lo and hi <= (1 << (b - 1)) - 1):
        b += 1
    return b


# ---------------------------------------------------------------- audio_pitch
HIGH_HALF = (2, 3)      # high tone: square wave, half-period 2 or 3 samples (period 4 or 6)
LOW_HALF = (8, 12)      # low tone: half-period 8 or 12 samples (period 16 or 24)
NOISE_VARIANTS = 3      # per (period, phase): the clean window plus 3 windows with ONE flipped sample


def square(half, phase, n):
    return [1 if ((i + phase) // half) % 2 == 0 else 0 for i in range(n)]


def changes(samples):
    """[+1,-1]-style detector: 1 where a sample differs from the one before it (len = len(samples)-1)."""
    return [samples[i] ^ samples[i - 1] for i in range(1, len(samples))]


def make_pitch_data(W):
    data = []
    for label, halves in ((1, HIGH_HALF), (0, LOW_HALF)):
        for half in halves:
            for phase in range(2 * half):
                w = square(half, phase, W)
                data.append((w, label))
                for _ in range(NOISE_VARIANTS):
                    n = list(w)
                    n[rng.randrange(W)] ^= 1
                    data.append((n, label))
    return data


def train_pitch(data):
    """Exhaustive search of the threshold T (decide 'high' when count >= T); best accuracy, smallest T on ties."""
    best = None
    maxc = max(sum(changes(s)) for s, _ in data)
    for T in range(0, maxc + 2):
        acc = sum(int(sum(changes(s)) >= T) == y for s, y in data)
        if best is None or acc > best[0]:
            best = (acc, T)
    return best[1], best[0] / len(data)


class PitchStream:
    """Streaming form: one decision per new sample. Memory = last sample (1 bit) + W-1 change bits
    (the delay line) + the running count."""
    def __init__(self, W, T):
        self.W, self.T = W, T
        self.prev = None
        self.line = deque([0] * (W - 1), maxlen=W - 1)
        self.count = 0
        self.seen = 0

    def push(self, s):
        c = 0 if self.prev is None else s ^ self.prev
        leaving = self.line[0]
        self.line.append(c)                 # the oldest change falls out of the line
        self.count += c - leaving           # running count: + entering, - leaving
        self.prev = s
        self.seen += 1
        warm = self.seen >= self.W
        return c, self.count, (int(self.count >= self.T) if warm else None)


def pitch_section():
    print("== audio_pitch (plan 4.3) ==")
    print("data: square waves, high tone half-period %s samples, low tone half-period %s samples," % (HIGH_HALF, LOW_HALF))
    print("      every phase, plus %d single-flipped-sample copies of each; label 1 = high tone" % NOISE_VARIANTS)
    print("%3s %8s %6s %8s %12s %11s %10s" % ("W", "examples", "T", "accuracy", "delay-line", "count-bits", "state-bits"))
    trained = {}
    for W in (8, 16, 32):
        data = make_pitch_data(W)
        T, acc = train_pitch(data)
        trained[W] = T
        dl = W - 1
        cb = bits_unsigned(W - 1)
        print("%3d %8d %6d %7.2f%% %9d bit %8d bit %7d bit" % (W, len(data), T, 100 * acc, dl, cb, dl + 1 + cb))
        wrong = [(sum(changes(x)), y) for x, y in data if int(sum(changes(x)) >= T) != y]
        print("      wrong: %d high-tone windows below T, %d low-tone windows at/above T; high-tone counts seen %s, low-tone counts seen %s"
              % (sum(1 for c, y in wrong if y == 1), sum(1 for c, y in wrong if y == 0),
                 sorted(set(sum(changes(x)) for x, y in data if y == 1)), sorted(set(sum(changes(x)) for x, y in data if y == 0))))
    print("state bits = (W-1) delay-line change bits + 1 last-sample bit + count register")
    # worked example at W=8
    W, T = 8, trained[8]
    stream = square(8, 0, 10) + square(2, 0, 14)       # low tone, then high tone
    print("\nworked example, W=%d, threshold T=%d (count of changes >= T means high tone)" % (W, T))
    ps = PitchStream(W, T)
    print("%4s %7s %7s %6s %9s" % ("t", "sample", "change", "count", "decision"))
    for t, s in enumerate(stream):
        c, cnt, d = ps.push(s)
        print("%4d %7d %7s %6d %9s" % (t, s, "-" if t == 0 else c, cnt, "warm-up" if d is None else ("HIGH" if d else "low")))
    # streaming equals recompute-from-scratch window
    ok = True
    ps = PitchStream(W, T)
    for t, s in enumerate(stream):
        _, cnt, d = ps.push(s)
        if t >= W - 1 and cnt != sum(changes(stream[t - W + 1:t + 1])):
            ok = False
    print("streaming running count equals recount of the last %d samples at every step: %s" % (W, ok))
    print("decisions made for %d samples: %d (one per sample after the %d-sample warm-up); memory held: %d bits, "
          "independent of stream length" % (len(stream), len(stream) - W + 1, W, W - 1 + 1 + bits_unsigned(W - 1)))


# ---------------------------------------------------------------- audio_onset
MARGIN = 4              # label 1 ("got louder") if (e2+e3) - (e0+e1) >= MARGIN, i.e. newest-two mean exceeds older-two mean by 2
LABEL_NOISE = 0.05      # fraction of training labels flipped
TRAIN_N, TEST_N = 3000, 3000


def clean_label(w):
    return int((w[2] + w[3]) - (w[0] + w[1]) >= MARGIN)


def make_onset(n, noise):
    rows = []
    for _ in range(n):
        w = [rng.randrange(16) for _ in range(4)]
        y = clean_label(w)
        if rng.random() < noise:
            y ^= 1
        rows.append((w, y))
    return rows


def train_onset(rows, wr=range(-2, 3)):
    """Search all 5^4 = 625 weight vectors and every threshold; score >= T -> 1. Best accuracy, then smallest
    sum|w|, then lexicographically smallest w, then smallest T."""
    best = None
    for w in itertools.product(wr, repeat=4):
        pos, tot = {}, {}
        for x, y in rows:
            s = sum(a * b for a, b in zip(w, x))
            tot[s] = tot.get(s, 0) + 1
            pos[s] = pos.get(s, 0) + y
        scores = sorted(tot)
        npos = sum(pos.values())
        # T just above score list; correct = positives with s>=T + negatives with s<T
        for T in [scores[0]] + [s + 1 for s in scores]:
            ge_pos = sum(pos[s] for s in scores if s >= T)
            ge_tot = sum(tot[s] for s in scores if s >= T)
            correct = ge_pos + ((len(rows) - npos) - (ge_tot - ge_pos))
            key = (-correct, sum(abs(a) for a in w), w, T)
            if best is None or key < best:
                best = key
    return list(best[2]), best[3], -best[0] / len(rows)


def onset_section():
    print("\n== audio_onset (plan 4.4) ==")
    print("data: 4 energies, each 0..15 (4 bit). label 1 (got louder) if (e2+e3)-(e0+e1) >= %d," % MARGIN)
    print("      i.e. mean of the newest two exceeds mean of the older two by >= %d. %d%% of training labels flipped."
          % (MARGIN // 2, int(LABEL_NOISE * 100)))
    train = make_onset(TRAIN_N, LABEL_NOISE)
    test = make_onset(TEST_N, 0.0)
    print("train windows: %d (%d labelled 1), test windows: %d (clean labels)"
          % (len(train), sum(y for _, y in train), len(test)))
    print("weight vectors searched: %d" % (5 ** 4))
    w, T, tacc = train_onset(train)
    print("trained weights (each searched in -2..2): %s, threshold: score >= %d" % (w, T))
    print("rediscovered [-1,-1,+1,+1]: %s" % (w == [-1, -1, 1, 1]))
    print("train accuracy: %.2f%%" % (100 * tacc))

    def acc(rows):
        return sum(int(sum(a * b for a, b in zip(w, x)) >= T) == y for x, y in rows) / len(rows)
    print("test accuracy (fresh, clean labels): %.2f%%" % (100 * acc(test)))
    allw = list(itertools.product(range(16), repeat=4))
    full = sum(int(sum(a * b for a, b in zip(w, x)) >= T) == clean_label(x) for x in allw) / len(allw)
    print("accuracy over all %d possible windows against the clean rule: %.2f%%" % (len(allw), 100 * full))
    # bit widths
    lo = sum(min(0, a) * 15 for a in w)
    hi = sum(max(0, a) * 15 for a in w)
    sb = bits_signed(lo, hi)
    print("\nbit widths with the trained weights %s:" % w)
    print("  input energy: 4 bit; signed sum range %d..%d -> %d bits (two's complement)" % (lo, hi, sb))
    print("  memory: 3 previous energies x 4 bit = 12 bits")
    print("  audio_pitch W=4 for comparison: samples 1 bit, count range 0..3 -> %d bits, delay line 3 bits + 1 last sample"
          % bits_unsigned(3))
    print("  same 4-sample window: onset stores %d bits of history vs pitch's %d (%dx), sum is %d bits vs %d"
          % (12, 4, 12 // 4, sb, bits_unsigned(3)))
    # worked example
    ex = [3, 4, 9, 12]
    print("\nworked example, window (oldest to newest) = %s" % ex)
    tot = 0
    for e, a in zip(ex, w):
        print("  energy %2d x weight %+d = %+d" % (e, a, e * a))
        tot += e * a
    print("  sum = %+d; %+d >= %d ? %s -> %s" % (tot, tot, T, "yes" if tot >= T else "no", "LOUDER" if tot >= T else "not louder"))
    ex2 = [9, 10, 8, 9]
    t2 = sum(a * e for a, e in zip(w, ex2))
    print("  second window %s: sum = %+d; %+d >= %d ? %s" % (ex2, t2, t2, T, "yes" if t2 >= T else "no"))


if __name__ == "__main__":
    pitch_section()
    onset_section()
