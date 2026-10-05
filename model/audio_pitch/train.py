#!/usr/bin/env python3
"""train.py -- fit the audio_pitch threshold. Python 3.9 standard library only, deterministic.

Machine-learning view: the DATASET is generated labelled windows (square waves of long and short period at every
phase, plus single-bit noise); the MODEL is "count the sign changes in the window (a [+1,-1] kernel slid over time),
fire when count >= T"; TRAINING is an exhaustive search over T for the best accuracy (smallest T on ties).
Same idea as model/examples/audio.py. Writes weights.json only when the content changes."""
import json, os, random

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = json.load(open(os.path.join(HERE, "spec.json")))
WEIGHTS_PATH = os.path.join(HERE, "weights.json")


def square(half, phase, n):
    return [1 if ((i + phase) // half) % 2 == 0 else 0 for i in range(n)]


def changes(samples):
    return [samples[i] ^ samples[i - 1] for i in range(1, len(samples))]


def make_data(W, rng):
    t = SPEC["training"]
    data = []
    for label, halves in ((1, t["high_half_periods"]), (0, t["low_half_periods"])):
        for half in halves:
            for phase in range(2 * half):
                w = square(half, phase, W)
                data.append((w, label))
                for _ in range(t["noise_variants"]):
                    n = list(w)
                    n[rng.randrange(W)] ^= 1
                    data.append((n, label))
    return data


def fit(data, W):
    best = None
    for T in range(0, W + 1):
        acc = sum(int(sum(changes(s)) >= T) == y for s, y in data)
        if best is None or acc > best[0]:
            best = (acc, T)
    return best[1], best[0]


def main():
    W = SPEC["window"]
    data = make_data(W, random.Random(SPEC["seed"]))
    T, correct = fit(data, W)
    hi = sorted(set(sum(changes(s)) for s, y in data if y == 1))
    lo = sorted(set(sum(changes(s)) for s, y in data if y == 0))
    wrong1 = sum(1 for s, y in data if y == 1 and sum(changes(s)) < T)
    wrong0 = sum(1 for s, y in data if y == 0 and sum(changes(s)) >= T)
    out = {"_what": "written by train.py; read by golden.py and gen_rom.py", "window": W, "threshold": T,
           "train_examples": len(data), "train_correct": correct,
           "train_accuracy_percent": round(100.0 * correct / len(data), 2),
           "high_tone_counts_seen": hi, "low_tone_counts_seen": lo,
           "wrong_high_below_threshold": wrong1, "wrong_low_at_or_above_threshold": wrong0}
    text = json.dumps(out, indent=2) + "\n"
    try:
        same = open(WEIGHTS_PATH).read() == text
    except OSError:
        same = False
    if not same:
        open(WEIGHTS_PATH, "w").write(text)
    print("train: W=%d examples=%d threshold T=%d accuracy %d/%d = %.2f%%" % (W, len(data), T, correct, len(data), 100.0 * correct / len(data)))
    print("train: counts seen: high tone %s, low tone %s; errors: %d high windows below T, %d low windows at/above T" % (hi, lo, wrong1, wrong0))
    print("train: weights.json %s" % ("unchanged" if same else "written"))


if __name__ == "__main__":
    main()
