"""Shared helpers for model/tiny_ai: paths, the spec, source hashes, truth tables and labels.

Machine-learning view of this file: truth_table() builds the LABELLED DATASET (every possible input paired with
the answer a human wants). train.py fits a model to that dataset; golden.py runs the fitted model and is checked
against the same dataset. The label rules below are NOT the model: they are the ground truth, given as examples.
The model (a neuron, a kernel, an embedding table) never contains these rules; its numbers are found by training."""
import hashlib, itertools, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SPEC_PATH = os.path.join(HERE, "spec.json")
WEIGHTS_PATH = os.path.join(HERE, "weights.json")
DESIGNS = ("vision_all_lit", "vision_block", "text_sentiment")


def spec():
    return json.load(open(SPEC_PATH))


def sha256_of(*paths):
    h = hashlib.sha256()
    for p in paths:
        h.update(os.path.relpath(p, REPO).encode() + b"\0" + open(p, "rb").read())
    return h.hexdigest()


def popcount(x):
    return bin(x).count("1")


def windows(frame):
    """The four 2x2 windows of a 3x3 frame (list of 9 bits, raster order), each a 4-bit value with
    bit 0 = top-left, bit 1 = top-right, bit 2 = bottom-left, bit 3 = bottom-right; window order (0,0), (0,1), (1,0), (1,1).
    These are the convolution's receptive fields: the small patches the same 2x2 kernel is applied to
    (slid one pixel at a time, no padding). Used by the label rule, by train.py and by golden.py."""
    out = []
    for r in (0, 1):
        for c in (0, 1):
            b = r * 3 + c
            out.append(frame[b] | frame[b + 1] << 1 | frame[b + 3] << 2 | frame[b + 4] << 3)
    return out


def truth_table(design):
    """Every valid input of the design, with its label, in a fixed order.
    This is the labelled dataset: a list of (input, label) examples, i.e. the full truth table (2^4 = 16 rows,
    2^9 = 512 rows, 4^4 = 256 rows). The label is the target answer, the "ground truth" a human supplies; the
    expressions below only generate that answer key and are never copied into the model's parameters.
    Because the table lists EVERY possible input, training on it leaves no unseen test inputs."""
    s = spec()["designs"][design]
    n, vmax = s["inputs"], s["input_max"]
    rows = []
    for items in itertools.product(range(vmax + 1), repeat=n):
        # Labels: the answer a human wants for each input (the examples), not the learned rule.
        if design == "vision_all_lit":
            label = int(all(items))
        elif design == "vision_block":
            label = int(any(w == 0xF for w in windows(list(items))))
        else:
            label = int(items.count(1) > items.count(3))   # GOOD = 1, BAD = 3
        rows.append((list(items), label))
    return rows
