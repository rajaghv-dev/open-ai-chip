#!/usr/bin/env python3
"""golden.py -- bit-exact, cycle-visible reference of the three engines (model/tiny_ai/spec.json, weights.json).

  run(design, beats) -> (beat0, beat1, latency)   beats: the s_data value of every beat of one frame (s_last on the last)
  golden.py --check     every truth-table input: the reference's class equals the label (zero mismatches), every
                        score fits its stated range, and no valid frame raises error; exit 1 otherwise
  golden.py <design> <item> ...   print one result, e.g.  golden.py text_sentiment 1 1 3 0
"""
# Machine-learning view: this file is the reference INFERENCE (forward pass) of the trained models. It takes
# the learned parameters from weights.json (written by train.py) and computes the answer for an input; nothing
# is learned here. The RTL in designs/ must reproduce these results bit for bit, and --check confirms the
# reference agrees with every label of the dataset.
import json, sys
from common import DESIGNS, WEIGHTS_PATH, popcount, spec, truth_table, windows


def params():
    return json.load(open(WEIGHTS_PATH))


def run(design, beats, w=None):
    # The network itself is: weighted sum (or XNOR match count) -> activation (threshold step) -> pooling
    # (vision_block) or embedding lookup + sum (text_sentiment). The `error` flag and the `latency` value
    # belong to the hardware stream protocol (bad beat values / wrong frame length, clock cycles), not to the
    # network; they are reported so the RTL can be compared with this model.
    w = w or params()
    s = spec()["designs"][design]
    n, vmax = s["inputs"], s["input_max"]
    error = len(beats) != n or any(b > vmax for b in beats)
    used = [(i, b) for i, b in enumerate(beats) if i < n and b <= vmax]     # beats that reach the datapath
    if design == "vision_all_lit":
        p = w[design]
        score = sum(1 for i, b in used if b == p["weights"][i])     # weighted sum: count of pixels matching weight
        cls = int(score >= p["threshold"])                          # activation: threshold step
        beat1 = score
    elif design == "vision_block":
        p = w[design]
        frame = [0] * 9
        for i, b in used:
            frame[i] = b
        k = sum(v << i for i, v in enumerate(p["kernel"]))
        counts = [popcount(~(win ^ k) & 0xF) for win in windows(frame)]   # XNOR + popcount per window (same kernel)
        cls = int(any(c >= p["threshold"] for c in counts))               # threshold each window, then max-pool (OR)
        score = max(counts)
        beat1 = score
    else:
        e = w[design]["embedding"]
        score = sum(e[b] for _, b in used)                  # embedding lookup per word, summed
        assert -16 <= score <= 15, "accumulator overflow"
        cls = int(score > w[design]["threshold"])           # activation: threshold step (sum > 0)
        beat1 = score & 0xFF
    return (int(error) << 1 | cls, beat1, s["latency"])


def core_run(design, items, w=None):
    """tiny_ai_core: (mode, class, score byte, CYCLES) for one valid input set pushed through the Wishbone interface.
    The prediction (class, score) comes from run(), i.e. the network; mode and CYCLES are SoC/protocol details.
    The core streams the buffer into the engine one item per clock and takes its two result beats, so
    CYCLES = number of inputs + engine latency + 1 (register map: designs/tiny_ai_core/rtl/tiny_ai_core.v)."""
    s = spec()["designs"][design]
    b0, b1, lat = run(design, items, w)
    assert b0 >> 1 == 0, "core inputs are range-checked on entry: an engine error is unreachable"
    return s["mode"], b0 & 1, b1, len(items) + lat + 1


def check():
    # Evaluate the model on the whole labelled dataset: count disagreements with the labels (the same loss
    # train.py drove to zero) plus out-of-range scores. Every input is covered, so 0 means correct everywhere.
    w = params()
    bad = 0
    for d in DESIGNS:
        rows = truth_table(d)
        mism = 0
        for x, y in rows:
            b0, b1, _ = run(d, x, w)
            if b0 != y:                         # class bit and no error on a valid frame
                mism += 1
            if d != "text_sentiment" and not 0 <= b1 <= 4:
                mism += 1
        print("golden: %-15s %4d cases, %d mismatches" % (d, len(rows), mism))
        bad += mism
    return bad


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(1 if check() else 0)
    if len(sys.argv) >= 3 and sys.argv[1] in DESIGNS:
        print("beat0=0x%02x beat1=0x%02x latency=%d" % run(sys.argv[1], [int(v, 0) for v in sys.argv[2:]]))
        sys.exit(0)
    sys.exit(__doc__)
