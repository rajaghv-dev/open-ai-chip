#!/usr/bin/env python3
"""golden.py -- bit-exact reference of designs/audio_pitch/rtl/audio_pitch.v (python3 standard library only).

Cycle-level in the sense that matters for a stream engine: one input beat -> at most one result beat, with the exact
state (last sample, W-1 change bits, running count, warm-up counter, pending error) the RTL keeps. Handshake timing
(gaps, stalls) does not change the values, so the testbench compares the ORDER and VALUE of the result beats."""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
WEIGHTS_PATH = os.path.join(HERE, "weights.json")


def params():
    return json.load(open(WEIGHTS_PATH))


class Pitch:
    def __init__(self, W, T):
        self.W, self.T = W, T
        self.clear()

    def clear(self):
        self.prev = 0
        self.line = [0] * (self.W - 1)      # line[0] = newest change, line[-1] = oldest (about to leave)
        self.count = 0
        self.seen = 0                       # samples of this recording seen, saturating at W-1
        self.err = 0

    def push(self, data, last):
        """One accepted input beat. Returns (m_data, m_last) or None (warm-up: no result beat)."""
        bad = int((data >> 1) != 0)
        s = data & 1
        chg = int(self.seen != 0 and s != self.prev)
        leaving = self.line[-1]
        count = self.count + chg - leaving
        err = self.err | bad
        out = None
        if self.seen == self.W - 1:
            out = (err << 7 | int(count >= self.T) << 6 | count, int(last))
        if last:
            self.clear()
        else:
            self.line = [chg] + self.line[:-1]
            self.count, self.prev = count, s
            self.seen = min(self.seen + 1, self.W - 1)
            self.err = 0 if out else err
        return out


def run(beats, W=None, T=None):
    """beats: list of (data, last). Returns list of per-beat outputs (None or (m_data, m_last))."""
    p = params()
    g = Pitch(W or p["window"], p["threshold"] if T is None else T)
    return [g.push(d, l) for d, l in beats]


def recount(samples, W):
    """independent check: sign changes among the last W samples."""
    w = samples[-W:]
    return sum(w[i] ^ w[i - 1] for i in range(1, len(w)))
