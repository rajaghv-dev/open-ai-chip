#!/usr/bin/env python3
"""train.py -- fit the single neuron of audio_onset by exhaustive search. Python 3.9, standard library only.

Dataset (deterministic, seed in spec.json): random windows of four 4-bit energies, labelled 1 ("it just got louder")
when (e2+e3) - (e0+e1) >= margin, where e0 is the OLDEST and e3 the newest sample. LABEL_NOISE of the training labels
are flipped, as in model/examples/audio.py, so the fit is not trivially perfect. A separate clean test set and the
full set of all 65,536 windows are used to judge the result.

Model: sum = w0*e0 + w1*e1 + w2*e2 + w3*e3, class = (sum >= T). Search: every integer weight vector in the search
range (5^4 = 625 for -2..2) and every threshold; best training accuracy wins, ties broken by smallest sum|w|, then
lexicographically smallest w, then smallest T. Writes weights.json only when its content changes.
Run: python3 model/audio_onset/train.py"""
import itertools, json, random
from common import SPEC_PATH, WEIGHTS_PATH, clean_label, sha256_of, spec, write_if_changed


def make_rows(rng, n, noise, margin):
    rows = []
    for _ in range(n):
        w = [rng.randrange(16) for _ in range(4)]
        y = clean_label(w, margin)
        if rng.random() < noise:
            y ^= 1
        rows.append((w, y))
    return rows


def train(rows, wr):
    best = None
    for w in itertools.product(wr, repeat=4):
        pos, tot = {}, {}
        for x, y in rows:
            s = sum(a * b for a, b in zip(w, x))
            tot[s] = tot.get(s, 0) + 1
            pos[s] = pos.get(s, 0) + y
        scores = sorted(tot)
        npos = sum(pos.values())
        for T in [scores[0]] + [s + 1 for s in scores]:
            ge_pos = sum(pos[s] for s in scores if s >= T)
            ge_tot = sum(tot[s] for s in scores if s >= T)
            correct = ge_pos + ((len(rows) - npos) - (ge_tot - ge_pos))
            key = (-correct, sum(abs(a) for a in w), w, T)
            if best is None or key < best:
                best = key
    return list(best[2]), best[3], -best[0] / len(rows)


def main():
    s = spec()
    d, wr = s["data"], range(s["network"]["weight_search_range"][0], s["network"]["weight_search_range"][1] + 1)
    rng = random.Random(d["seed"])
    train_rows = make_rows(rng, d["train_windows"], d["label_noise"], d["margin"])
    test_rows = make_rows(rng, d["test_windows"], 0.0, d["margin"])
    w, T, tacc = train(train_rows, wr)
    acc = lambda rows: sum(int(sum(a * b for a, b in zip(w, x)) >= T) == y for x, y in rows) / len(rows)
    full = sum(int(sum(a * b for a, b in zip(w, x)) >= T) == clean_label(x, d["margin"])
               for x in itertools.product(range(16), repeat=4)) / 65536
    print("train: %d windows (%d labelled 1), %.0f%% labels flipped; test: %d clean windows"
          % (len(train_rows), sum(y for _, y in train_rows), 100 * d["label_noise"], len(test_rows)))
    print("weight vectors searched: %d (range %d..%d)" % (len(wr) ** 4, wr[0], wr[-1]))
    print("learned weights (oldest..newest): %s, threshold: sum >= %d" % (w, T))
    print("rediscovered [-1,-1,+1,+1]: %s" % (w == [-1, -1, 1, 1]))
    print("train accuracy %.2f%%, test accuracy %.2f%%, all 65536 windows vs clean rule %.2f%%"
          % (100 * tacc, 100 * acc(test_rows), 100 * full))
    out = {"_generated": "by model/audio_onset/train.py from model/audio_onset/spec.json; do not edit",
           "source_sha256": sha256_of(SPEC_PATH), "weights": w, "threshold": T,
           "train_accuracy": round(tacc, 4), "test_accuracy": round(acc(test_rows), 4),
           "all_windows_accuracy": round(full, 4)}
    print("weights.json %s" % ("updated" if write_if_changed(WEIGHTS_PATH, json.dumps(out, indent=2) + "\n") else "unchanged"))


if __name__ == "__main__":
    main()
