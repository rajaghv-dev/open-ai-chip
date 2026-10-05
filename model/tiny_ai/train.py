#!/usr/bin/env python3
"""train.py -- fit the smallest exact integer parameters of the three tiny examples by exhaustive search over
their complete truth tables, and write weights.json.

This is MACHINE LEARNING in miniature: the rule is learned from labelled examples, not written by hand.
  training set   the full truth table from common.truth_table(): every possible input with its label
  labels         the target answers a human gives (common.truth_table), e.g. "all four pixels lit -> 1"
  model family   a fixed structure with free numbers: one binary neuron (vision_all_lit), a 2x2 convolution
                 kernel neuron + max-pool (vision_block), an embedding table + sum (text_sentiment)
  parameters     the free numbers: weights and threshold, kernel and threshold, or the four embeddings
  loss           number of training examples the model gets wrong (mismatches); we accept only loss == 0
  training       exhaustive search: try every parameter setting in a fixed order and keep the first exact one
                 (vision) or the smallest-magnitude exact one (text). Large networks, whose parameter space is
                 far too big to enumerate, use gradient descent instead (nudge the numbers to reduce the loss);
                 here the space is tiny (96 / 96 / 4096 settings), so brute force is exact and simple.
  generalisation normally you hold out unseen test examples to check the model works on new data. Here the
                 training set IS every possible input, so there is no unseen data: zero mismatches on the
                 table means the model is correct everywhere.
Nowhere does this file write the rule "all four lit" into a neuron. The label function defines the target; the
search finds the weights. That the weights come out as 1,1,1,1 with threshold 4 is a result, not an input.
The output, weights.json, holds the learned parameters; the RTL stores them in a ROM and golden.py runs them.
 Deterministic: fixed search order, fixed seed (no randomness
is used; the seed is recorded so the rule in SPEC.md holds if a randomized search is ever added).

  vision_all_lit  weights w in {0,1}^4, threshold T in 0..5; first exact (w, T) in ascending (T, w) order
  vision_block    kernel k in {0,1}^4, threshold T in 0..5; same order
  text_sentiment  embeddings e in [-4,3]^4, decision sum > 0; smallest sum |e|, ties broken by the
                  lexicographically smallest (PAD, GOOD, FINE, BAD) tuple
Exits non-zero if any design has no exact solution."""
import itertools, json, random, sys
from common import DESIGNS, SPEC_PATH, WEIGHTS_PATH, popcount, sha256_of, spec, truth_table, windows
import os


def fit_vision_all_lit(rows):
    # Model: one binary neuron. Parameters: 4 one-bit weights (packed in w, bit i = weight i) and threshold t.
    # Forward pass: score = number of pixels equal to their weight (XNOR, then popcount); class = score >= t.
    # Search space: 6 thresholds x 16 weight vectors. Loss is zero iff the neuron matches every label.
    for t in range(0, 6):
        for w in range(16):
            if all(int(popcount(~(sum(p << i for i, p in enumerate(x)) ^ w) & 0xF) >= t) == y for x, y in rows):
                return {"weights": [w >> i & 1 for i in range(4)], "threshold": t}
    return None


def fit_vision_block(rows):
    # Model: one 2x2 kernel neuron (4 one-bit weights, packed in k) applied to each window, then max-pool
    # (any window reaching the threshold fires). The same kernel is reused at every position (convolution).
    # Search space: 6 thresholds x 16 kernels; the first exact one wins.
    for t in range(0, 6):
        for k in range(16):
            if all(int(any(popcount(~(win ^ k) & 0xF) >= t for win in windows(x))) == y for x, y in rows):
                return {"kernel": [k >> i & 1 for i in range(4)], "threshold": t}
    return None


def fit_text_sentiment(rows, lo, hi):
    # Model: an embedding table (one integer per word, e[token]); score = sum of the looked-up embeddings;
    # class = score > 0. Parameters: the four embeddings (PAD, GOOD, FINE, BAD), each in [lo, hi].
    # Search space: (hi-lo+1)^4 = 4096 tables. Many tables are exact, so we keep the one with the smallest
    # total magnitude (a simple preference for small numbers, like a regulariser), ties by smallest tuple.
    best = None
    for e in itertools.product(range(lo, hi + 1), repeat=4):
        if all(int(sum(e[t] for t in x) > 0) == y for x, y in rows):
            key = (sum(abs(v) for v in e), e)
            if best is None or key < best:
                best = key
    return None if best is None else {"embedding": list(best[1]), "threshold": 0}


def main():
    # Fit each design on its full labelled dataset, then save the learned parameters.
    s = spec()
    random.seed(s["seed"])
    out = {"_generated": "by model/tiny_ai/train.py from model/tiny_ai/spec.json; do not edit",
           "source_sha256": sha256_of(SPEC_PATH, os.path.abspath(__file__))}
    for d in DESIGNS:
        rows = truth_table(d)               # the training set: all inputs with their labels
        if d == "vision_all_lit":
            p = fit_vision_all_lit(rows)
        elif d == "vision_block":
            p = fit_vision_block(rows)
        else:
            p = fit_text_sentiment(rows, *s["designs"][d]["embedding_range"])
        if p is None:                       # no setting of the parameters reproduces every label
            sys.exit("train: no exact solution for %s" % d)
        p["cases"] = len(rows)
        out[d] = p
        print("train: %-15s %s (%d cases, exact)" % (d, {k: v for k, v in p.items() if k != "cases"}, len(rows)))
    with open(WEIGHTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
