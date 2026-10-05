#!/usr/bin/env python3
"""golden.py -- bit-exact, cycle-visible reference of image_text_match (model/image_text_match/spec.json, weights.json).

  run(beats) -> (beat0, beat1, latency)   beats: the s_data value of every beat of one frame (s_last on the last)
  golden.py --check     every one of the 2,048 (image, caption) pairs: class equals the label, no error; exit 1 otherwise
  golden.py <item> ...  print one result, e.g.  golden.py 1 0 0 1 0 0 1 0 0 1   (vertical line, caption VERT)

Machine-learning view: this file is the reference INFERENCE (forward pass): image encoder (two neurons scanned over
8 lines, sum-pooled into a 6-vector), text encoder (embedding lookup into the same 6-vector space), similarity (dot
product) and a threshold. Also holds the labelled dataset (the answer key, never copied into the model)."""
import hashlib, itertools, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SPEC_PATH = os.path.join(HERE, "spec.json")
WEIGHTS_PATH = os.path.join(HERE, "weights.json")

LINES = [[0, 3, 6], [1, 4, 7], [2, 5, 8],      # columns (group 0)
         [0, 1, 2], [3, 4, 5], [6, 7, 8],      # rows    (group 1)
         [0, 4, 8], [2, 4, 6]]                 # diagonals (group 2)
GROUP = [0, 0, 0, 1, 1, 1, 2, 2]
NCH = 6                                        # channels: A cols, A rows, A diags, B cols, B rows, B diags
LATENCY = 10
NBEATS = 10


def spec():
    return json.load(open(SPEC_PATH))


def params():
    return json.load(open(WEIGHTS_PATH))


def sha256_of(*paths):
    h = hashlib.sha256()
    for p in paths:
        h.update(os.path.relpath(p, REPO).encode() + b"\0" + open(p, "rb").read())
    return h.hexdigest()


def label(pix, token):
    """Ground truth (the answer key). Not part of the model."""
    full = lambda line: all(pix[i] for i in line)
    if token == 0:
        return int(not any(pix))
    if token == 1:
        return int(any(full(l) for l in LINES[0:3]))
    if token == 2:
        return int(any(full(l) for l in LINES[3:6]))
    return int(any(full(l) for l in LINES[6:8]))


def truth_table():
    """All 512 images x 4 captions = 2,048 (frame of 10 items, label). Image order: pixel 0 is the slowest digit."""
    return [(list(p) + [t], label(list(p), t)) for p in itertools.product(range(2), repeat=9) for t in range(4)]


def neuron(line_pix, kernel, thr):
    """3-tap binary neuron: XNOR with the kernel, count matches, threshold step."""
    m = sum(1 for p, k in zip(line_pix, kernel) if p == k)
    return int(m >= thr)


def image_embedding(pix, kernels):
    """kernels = [(kernel_bits[3], threshold), (kernel_bits[3], threshold)] -> 6 counts."""
    e = [0] * NCH
    for ni, (k, t) in enumerate(kernels):
        for line, g in zip(LINES, GROUP):
            e[ni * 3 + g] += neuron([pix[i] for i in line], k, t)
    return e


def kernels_of(w):
    return [(n["kernel"], n["threshold"]) for n in w["image_encoder"]]


def score_of(pix, token, w):
    emb = image_embedding(pix, kernels_of(w))
    return sum(c * x for c, x in zip(emb, w["text_embedding"][token]))


def run(beats, w=None):
    # error/latency belong to the stream protocol; the network is encode -> encode -> dot -> threshold.
    w = w or params()
    n = NBEATS
    error = len(beats) != n or any(b > (1 if i < 9 else 3) for i, b in enumerate(beats[:n]))
    pix = [0] * 9
    token = 0
    for i, b in enumerate(beats[:n]):
        if i < 9 and b <= 1:
            pix[i] = b
        elif i == 9 and b <= 3:
            token = b
    s = score_of(pix, token, w)
    assert -64 <= s <= 63, "score overflow"
    cls = int(s >= w["threshold"])
    return (int(error) << 1 | cls, s & 0xFF, LATENCY)


def check():
    w = params()
    rows = truth_table()
    mism = sum(1 for x, y in rows if run(x, w)[0] != y)
    print("golden: image_text_match %d cases, %d mismatches" % (len(rows), mism))
    return mism


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(1 if check() else 0)
    if len(sys.argv) >= 2:
        print("beat0=0x%02x beat1=0x%02x latency=%d" % run([int(v, 0) for v in sys.argv[1:]]))
        sys.exit(0)
    sys.exit(__doc__)
