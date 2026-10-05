#!/usr/bin/env python3
"""train.py -- fit image_text_match by exhaustive, deterministic search over small integers; write weights.json.

Training set: the full table of 512 images x 4 captions = 2,048 labelled pairs (golden.truth_table); no unseen data.
Model family (fixed structure, free numbers):
  image encoder  two 3-tap binary neurons A and B (kernel 3 bits, threshold 1..3) scanned over the 8 lines of the
                 image, fires sum-pooled per line group -> 6 counts
  text encoder   embedding table: 4 captions x 6 signed integers in [-2, 3]
  similarity     dot(image 6-vector, text 6-vector); class = dot >= T, T in 1..12 (one threshold for all captions)
Loss = number of the 2,048 pairs misclassified; we accept only 0.
Search: neuron candidates are ordered (cost = threshold + number of 1 weights... see key), pairs (A <= B) in
ascending key order. For a pair the image embeddings are computed once; per caption we need weights w with
(dot(e, w) >= T) == label for every image. For each T (ascending) and caption we scan all 6^6 weight vectors in
ascending (sum |w|, tuple) order. The first pair/T for which all four captions are feasible wins.
Mismatch honesty: if no pair is feasible the script prints the best (fewest-mismatch) attempt and exits 1."""
import itertools, json, os, sys
import golden

WR = range(-2, 4)


def candidates():
    c = [([k >> i & 1 for i in range(3)], t) for t in (3, 2, 1) for k in range(8)]
    return c


def fit_pair(pair, rows_by_token, imgs):
    emb = {}
    for p in imgs:
        emb[tuple(p)] = tuple(golden.image_embedding(list(p), pair))
    # unique (embedding -> label) per caption, rejecting conflicts (same embedding, different label)
    tables = []
    for t in range(4):
        m = {}
        for p, y in rows_by_token[t]:
            e = emb[tuple(p)]
            if m.setdefault(e, y) != y:
                return None, 1          # image encoder cannot tell these images apart for this caption
        tables.append(list(m.items()))
    ws = sorted(itertools.product(WR, repeat=golden.NCH), key=lambda w: (sum(abs(v) for v in w), w))
    for T in range(1, 13):
        sol = []
        for tab in tables:
            found = None
            for w in ws:
                if all((sum(a * b for a, b in zip(e, w)) >= T) == bool(y) for e, y in tab):
                    found = w
                    break
            if found is None:
                break
            sol.append(list(found))
        if len(sol) == 4:
            return (T, sol), 0
    return None, 2


def main():
    imgs = [list(p) for p in itertools.product(range(2), repeat=9)]
    rows_by_token = [[(p, golden.label(p, t)) for p in imgs] for t in range(4)]
    cands = candidates()
    pairs = [(a, b) for i, a in enumerate(cands) for b in cands[i:]]
    tried = conflict = 0
    for a, b in pairs:
        tried += 1
        res, why = fit_pair([a, b], rows_by_token, imgs)
        conflict += why == 1
        if res:
            T, emb = res
            out = {"_generated": "by model/image_text_match/train.py from spec.json; do not edit",
                   "source_sha256": golden.sha256_of(golden.SPEC_PATH, os.path.abspath(__file__), os.path.join(golden.HERE, "golden.py")),
                   "channels": ["A_cols", "A_rows", "A_diags", "B_cols", "B_rows", "B_diags"],
                   "image_encoder": [{"kernel": k, "threshold": t} for k, t in (a, b)],
                   "text_embedding": emb, "threshold": T}
            text = json.dumps(out, indent=2) + "\n"
            try:
                same = open(golden.WEIGHTS_PATH).read() == text
            except OSError:
                same = False
            if not same:
                open(golden.WEIGHTS_PATH, "w").write(text)
            mism = golden.check()
            print("train: pair #%d of %d (%d rejected as indistinguishable), T=%d" % (tried, len(pairs), conflict, T))
            print("train: A=%s B=%s" % (a, b))
            for t, e in enumerate(emb):
                print("train: caption %d %-5s %s" % (t, golden.spec()["captions"][t], e))
            sys.exit(1 if mism else 0)
    print("train: NO exact solution in this model family (%d pairs tried, %d with image conflicts)" % (tried, conflict))
    sys.exit(1)


if __name__ == "__main__":
    main()
