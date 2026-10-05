#!/usr/bin/env python3
"""A tiny transformer, computed step by step (Python 3.9 standard library only, deterministic).

Not built as chips: this script computes the examples quoted in docs/parts/transformer.md.
Run: python3 model/examples/transformer.py
"""
import itertools
import math

TOK = ["PAD", "GOOD", "FINE", "BAD"]   # tokens 0..3, same as model/tiny_ai
PAD, GOOD, FINE, BAD = range(4)


def show(sent):
    return " ".join(TOK[t] for t in sent)


def fmt(v):
    return "[" + " ".join("%6.3f" % x for x in v) + "]"


# ---------------------------------------------------------------- 1. bigram
TEXT = "ABCABCABDABCABCD"   # committed training string, alphabet A B C D
ALPHA = "ABCD"


def part1():
    print("=" * 70)
    print("1. text_bigram: count pairs, predict the next token, generate in a loop")
    print("=" * 70)
    print("training string:", TEXT)
    n = len(ALPHA)
    table = [[0] * n for _ in range(n)]
    for a, b in zip(TEXT, TEXT[1:]):
        table[ALPHA.index(a)][ALPHA.index(b)] += 1
    print("pair counts (row = current token, column = next token):")
    print("      " + "  ".join(ALPHA))
    for i, row in enumerate(table):
        print("  %s   %s" % (ALPHA[i], "  ".join(str(c) for c in row)))
    print("  total pairs counted:", sum(map(sum, table)), "(= len(string) - 1 = %d)" % (len(TEXT) - 1))

    def nxt(i):
        row = table[i]
        return row.index(max(row))      # argmax, lowest index wins a tie

    print("argmax next token (lowest index wins ties):")
    for i in range(n):
        print("  after %s -> %s   (score %d)" % (ALPHA[i], ALPHA[nxt(i)], table[i][nxt(i)]))
    cur, out = 0, [0]
    for _ in range(11):
        cur = nxt(cur)
        out.append(cur)
    print("generated, 12 tokens starting from A, each output fed back as the next input:")
    print("  ", "".join(ALPHA[i] for i in out))
    print("  calls to the predictor: 11, one token per call, strictly one after another")
    cur, seen, k = 0, {}, 0
    while cur not in seen:
        seen[cur] = k
        cur = nxt(cur)
        k += 1
    print("  the loop repeats after %d tokens (cycle length %d), because argmax is deterministic"
          % (seen[cur] + (k - seen[cur]), k - seen[cur]))
    print("  with argmax the whole model collapses to the %d-entry answer list above;" % n)
    print("  the scores are kept because real models sample from them")


# ------------------------------------------------------- 2. hard attention
KEYS = [0b1010, 0b0110, 0b1111, 0b0001]
VALS = [3, 7, 12, 5]


def hard_attention(q):
    scores = [4 - bin(k ^ q).count("1") for k in KEYS]    # number of matching bits
    best = scores.index(max(scores))                       # lowest index wins ties
    return scores, best, VALS[best]


def part2():
    print()
    print("=" * 70)
    print("2. hard attention: 4 stored key/value pairs, 4-bit query")
    print("=" * 70)
    for i, (k, v) in enumerate(zip(KEYS, VALS)):
        print("  slot %d: key %s  value %d" % (i, format(k, "04b"), v))
    q = 0b1011
    scores, best, val = hard_attention(q)
    print("worked example, query %s:" % format(q, "04b"))
    for i, k in enumerate(KEYS):
        print("  key %s vs query %s -> %d bits match" % (format(k, "04b"), format(q, "04b"), scores[i]))
    print("  scores %s -> best slot %d -> output value %d" % (scores, best, val))
    ties, bad = 0, 0
    print("all 16 queries (query: scores -> slot, value):")
    for q in range(16):
        scores, best, val = hard_attention(q)
        ref = min(range(4), key=lambda i: (bin(KEYS[i] ^ q).count("1"), i))  # independent check
        bad += (ref != best)
        t = scores.count(max(scores)) > 1
        ties += t
        print("  %s: %s -> slot %d, value %2d%s" % (format(q, "04b"), scores, best, val, "  (tie)" if t else ""))
    print("check against minimum-Hamming-distance reference: %d disagreements out of 16" % bad)
    print("queries with a tie: %d of 16" % ties)


# ------------------------------------------------ 3. self-attention + order
S_DEFAULT = 8.0


def softmax(v):
    m = max(v)
    e = [math.exp(x - m) for x in v]
    s = sum(e)
    return [x / s for x in e]


def embed(sent):
    """8-number embedding per position: token one-hot (4) then position one-hot (4)."""
    rows = []
    for p, t in enumerate(sent):
        rows.append([1.0 if t == j else 0.0 for j in range(4)] + [1.0 if p == j else 0.0 for j in range(4)])
    return rows


def matvec_rows(X, W):
    return [[sum(x[i] * W[i][j] for i in range(len(x))) for j in range(len(W[0]))] for x in X]


def make_weights(S, use_pos=True):
    """Built BY HAND (not learned).
    Key  of a position j  = its position one-hot (4 numbers).
    Query of a position i = S x (one-hot of position i-1; position 0 looks at itself).
    Value of a position   = 1 if its token is GOOD else 0 (1 number)."""
    Wk = [[0.0] * 4 for _ in range(8)]
    Wq = [[0.0] * 4 for _ in range(8)]
    Wv = [[0.0] for _ in range(8)]
    if use_pos:
        for j in range(4):
            Wk[4 + j][j] = 1.0
            Wq[4 + j][max(j - 1, 0)] = S
    Wv[GOOD][0] = 1.0
    return Wq, Wk, Wv


def attention(sent, S, use_pos=True):
    X = embed(sent)
    Wq, Wk, Wv = make_weights(S, use_pos)
    Q, K, V = matvec_rows(X, Wq), matvec_rows(X, Wk), matvec_rows(X, Wv)
    scores = [[sum(q[d] * k[d] for d in range(4)) for k in K] for q in Q]   # 4 x 4
    W = [softmax(r) for r in scores]
    out = [sum(W[i][j] * V[j][0] for j in range(4)) for i in range(4)]
    return X, Q, K, V, scores, W, out


def label_order(sent):
    """1 iff some GOOD is immediately followed by BAD."""
    return int(any(sent[i] == GOOD and sent[i + 1] == BAD for i in range(3)))


def readout(sent, out, th):
    """Each position i scores  (is BAD) + (attention output = was the previous token GOOD);
    answer 1 if the largest such score reaches th."""
    return int(max((1.0 if t == BAD else 0.0) + o for t, o in zip(sent, out)) >= th)


def part3():
    print()
    print("=" * 70)
    print("3. one-head self-attention with position, and an order-dependent label")
    print("=" * 70)
    sents = list(itertools.product(range(4), repeat=4))
    pos = [s for s in sents if label_order(s)]
    print("label: positive iff a GOOD is immediately followed by BAD (e.g. GOOD BAD FINE FINE)")
    print("sentences: %d, positive: %d, negative: %d" % (len(sents), len(pos), len(sents) - len(pos)))

    sent = (FINE, GOOD, BAD, FINE)
    print()
    print("worked sentence:", show(sent), "  (label %d)" % label_order(sent))
    X, Q, K, V, scores, W, out = attention(sent, S_DEFAULT)
    print("embeddings (token one-hot PAD GOOD FINE BAD | position one-hot 0 1 2 3):")
    for p, x in enumerate(X):
        print("  pos %d %-4s %s | %s" % (p, TOK[sent[p]], " ".join("%d" % v for v in x[:4]), " ".join("%d" % v for v in x[4:])))
    print("Q (query = %g x one-hot of the PREVIOUS position; position 0 looks at itself):" % S_DEFAULT)
    for p, q in enumerate(Q):
        print("  pos %d  %s" % (p, fmt(q)))
    print("K (key = own position one-hot):")
    for p, k in enumerate(K):
        print("  pos %d  %s" % (p, fmt(k)))
    print("V (value = 1 if the token is GOOD):", " ".join("%d" % v[0] for v in V))
    print("scores = Q . K  (row = asking position, column = position looked at):")
    for r in scores:
        print("  " + fmt(r))
    print("softmax weights (each row sums to 1):")
    for r in W:
        print("  " + fmt(r) + "  sum %.3f" % sum(r))
    print("output = weights x V, per position:", fmt(out))
    print("  so position 2 (BAD) sees 'previous token is GOOD' as %.3f" % out[2])
    feats = [(1.0 if t == BAD else 0.0) + o for t, o in zip(sent, out)]
    print("readout feature per position (is BAD + attention output):", fmt(feats))
    print("  max = %.3f; with threshold 1.5 -> answer %d" % (max(feats), readout(sent, out, 1.5)))

    print()
    print("search over the attention sharpness S and the readout threshold, on ALL 256 sentences")
    print("(S = 0 means position information switched off: attention becomes uniform)")
    print("  S     thr   mismatches / 256")
    best = None
    for S in (0.0, 1.0, 2.0, 4.0, 8.0):
        for th in (0.5, 1.0, 1.5, 2.0):
            outs = [attention(s, S)[6] for s in sents]
            mm = sum(readout(s, o, th) != label_order(s) for s, o in zip(sents, outs))
            if best is None or mm < best[0]:
                best = (mm, S, th)
            print("  %-5g %-5g %d" % (S, th, mm))
    print("best in the grid: S=%g, threshold=%g, mismatches %d / 256" % (best[1], best[2], best[0]))
    mm0 = min(sum(readout(s, attention(s, 0.0)[6], th) != label_order(s) for s in sents) for th in (0.5, 1.0, 1.5, 2.0))
    print("best with position switched off (S=0): %d mismatches / 256" % mm0)
    # the weights are constructed, the two numbers (S, threshold) are searched
    return sents


def part3b(sents):
    print()
    print("bag of words (sum of per-token weights, positive if sum >= threshold), exhaustive search:")
    print("weights for PAD GOOD FINE BAD each in -3..3, threshold -6..6")
    labels = [label_order(s) for s in sents]
    best, nbest, first = -1, 0, None
    for w in itertools.product(range(-3, 4), repeat=4):
        sums = [sum(w[t] for t in s) for s in sents]
        for th in range(-6, 7):
            c = sum((su >= th) == bool(l) for su, l in zip(sums, labels))
            if c > best:
                best, nbest, first = c, 1, (w, th)
            elif c == best:
                nbest += 1
    print("  settings tried: %d" % (7 ** 4 * 13))
    print("  best accuracy: %d / 256 correct = %.2f%% (%d wrong)" % (best, 100.0 * best / 256, 256 - best))
    print("  %d settings reach that best; first found: weights %s, threshold %d" % (nbest, list(first[0]), first[1]))
    print("  for scale: always answering 0 gets %d / 256 correct" % (256 - sum(labels)))
    a, b = (GOOD, BAD, FINE, FINE), (BAD, GOOD, FINE, FINE)
    print("  why: %s (label %d) and %s (label %d) contain the same words,"
          % (show(a), label_order(a), show(b), label_order(b)))
    print("  so any sum of per-token weights gives them the same total")
    print("  (the labels differ, so one of them is always wrong)")


def main():
    part1()
    part2()
    sents = part3()
    part3b(sents)
    print()
    print("=" * 70)
    print("4. what this adds up to")
    print("=" * 70)
    for n in (4, 8, 16, 32):
        print("  sequence length N=%2d: attention score matrix has %4d entries; KV cache holds %3d key+value pairs"
              % (n, n * n, n))
    print("  (entries = N x N; cache = one key and one value per token seen so far)")
    print("  parallel hard attention: N comparators, answer in ~1-2 cycles; serial: 1 comparator, N cycles")


if __name__ == "__main__":
    main()
