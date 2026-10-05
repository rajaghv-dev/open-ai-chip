#!/usr/bin/env python3
"""train.py -- fit the smallest exact integer parameters of the three tiny examples by exhaustive search over
their complete truth tables, and write weights.json. Deterministic: fixed search order, fixed seed (no randomness
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
    for t in range(0, 6):
        for w in range(16):
            if all(int(popcount(~(sum(p << i for i, p in enumerate(x)) ^ w) & 0xF) >= t) == y for x, y in rows):
                return {"weights": [w >> i & 1 for i in range(4)], "threshold": t}
    return None


def fit_vision_block(rows):
    for t in range(0, 6):
        for k in range(16):
            if all(int(any(popcount(~(win ^ k) & 0xF) >= t for win in windows(x))) == y for x, y in rows):
                return {"kernel": [k >> i & 1 for i in range(4)], "threshold": t}
    return None


def fit_text_sentiment(rows, lo, hi):
    best = None
    for e in itertools.product(range(lo, hi + 1), repeat=4):
        if all(int(sum(e[t] for t in x) > 0) == y for x, y in rows):
            key = (sum(abs(v) for v in e), e)
            if best is None or key < best:
                best = key
    return None if best is None else {"embedding": list(best[1]), "threshold": 0}


def main():
    s = spec()
    random.seed(s["seed"])
    out = {"_generated": "by model/tiny_ai/train.py from model/tiny_ai/spec.json; do not edit",
           "source_sha256": sha256_of(SPEC_PATH, os.path.abspath(__file__))}
    for d in DESIGNS:
        rows = truth_table(d)
        if d == "vision_all_lit":
            p = fit_vision_all_lit(rows)
        elif d == "vision_block":
            p = fit_vision_block(rows)
        else:
            p = fit_text_sentiment(rows, *s["designs"][d]["embedding_range"])
        if p is None:
            sys.exit("train: no exact solution for %s" % d)
        p["cases"] = len(rows)
        out[d] = p
        print("train: %-15s %s (%d cases, exact)" % (d, {k: v for k, v in p.items() if k != "cases"}, len(rows)))
    with open(WEIGHTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
