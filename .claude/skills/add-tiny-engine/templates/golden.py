#!/usr/bin/env python3
"""golden.py -- bit-exact, cycle-visible reference of <eng> (model/<eng>/spec.json, weights.json).
  run(beats) -> (beat0, beat1, latency)   beats: s_data of every beat of one frame (s_last on the last)
  golden.py --check     every truth-table input: class == label, score in range, no error on a valid frame; exit 1 otherwise
  golden.py <item> ...  print one result
Standard library only. Pattern from model/tiny_ai/golden.py."""
import itertools, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SPEC = json.load(open(os.path.join(HERE, "spec.json")))["designs"]["<eng>"]
WEIGHTS_PATH = os.path.join(HERE, "weights.json")


def params():
    return json.load(open(WEIGHTS_PATH))


def label(items):                               # ground truth, NOT copied into the parameters
    return int(all(items))


def truth_table():                              # every valid input: no unseen data, so 0 mismatches = correct everywhere
    n, vmax = SPEC["inputs"], SPEC["input_max"]
    return [(list(x), label(x)) for x in itertools.product(range(vmax + 1), repeat=n)]


def run(beats, w=None):
    w = w or params()
    n, vmax = SPEC["inputs"], SPEC["input_max"]
    error = len(beats) != n or any(b > vmax for b in beats)
    used = [(i, b) for i, b in enumerate(beats) if i < n and b <= vmax]      # beats that reach the datapath
    score = sum(1 for i, b in used if b == w["weights"][i])                  # the network: match count ...
    cls = int(score >= w["threshold"])                                       # ... then threshold step
    return (int(error) << 1 | cls, score & 0xFF, SPEC["latency"])


def check():
    w, bad = params(), 0
    rows = truth_table()
    for x, y in rows:
        b0, b1, _ = run(x, w)
        bad += (b0 != y) + (not 0 <= b1 <= SPEC["inputs"])
    print("golden: %-15s %4d cases, %d mismatches" % ("<eng>", len(rows), bad))   # run_tests.sh greps ' 0 mismatches'
    return bad == 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--check"]:
        sys.exit(0 if check() else 1)
    b0, b1, lat = run([int(v, 0) for v in sys.argv[1:]])
    print("beat0=%02x beat1=%02x latency=%d" % (b0, b1, lat))
