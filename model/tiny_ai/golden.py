#!/usr/bin/env python3
"""golden.py -- bit-exact, cycle-visible reference of the three engines (model/tiny_ai/spec.json, weights.json).

  run(design, beats) -> (beat0, beat1, latency)   beats: the s_data value of every beat of one frame (s_last on the last)
  golden.py --check     every truth-table input: the reference's class equals the label (zero mismatches), every
                        score fits its stated range, and no valid frame raises error; exit 1 otherwise
  golden.py <design> <item> ...   print one result, e.g.  golden.py text_sentiment 1 1 3 0
"""
import json, sys
from common import DESIGNS, WEIGHTS_PATH, popcount, spec, truth_table, windows


def params():
    return json.load(open(WEIGHTS_PATH))


def run(design, beats, w=None):
    w = w or params()
    s = spec()["designs"][design]
    n, vmax = s["inputs"], s["input_max"]
    error = len(beats) != n or any(b > vmax for b in beats)
    used = [(i, b) for i, b in enumerate(beats) if i < n and b <= vmax]     # beats that reach the datapath
    if design == "vision_all_lit":
        p = w[design]
        score = sum(1 for i, b in used if b == p["weights"][i])
        cls = int(score >= p["threshold"])
        beat1 = score
    elif design == "vision_block":
        p = w[design]
        frame = [0] * 9
        for i, b in used:
            frame[i] = b
        k = sum(v << i for i, v in enumerate(p["kernel"]))
        counts = [popcount(~(win ^ k) & 0xF) for win in windows(frame)]
        cls = int(any(c >= p["threshold"] for c in counts))
        score = max(counts)
        beat1 = score
    else:
        e = w[design]["embedding"]
        score = sum(e[b] for _, b in used)
        assert -16 <= score <= 15, "accumulator overflow"
        cls = int(score > w[design]["threshold"])
        beat1 = score & 0xFF
    return (int(error) << 1 | cls, beat1, s["latency"])


def check():
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
