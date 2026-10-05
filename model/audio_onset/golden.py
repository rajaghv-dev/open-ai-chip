"""golden.py -- bit-exact reference of designs/audio_onset/rtl/audio_onset.v. Python 3.9, standard library only.

Two views, which gen_rom.py cross-checks against each other:
  beat() / run_beats() : function view. One accepted input beat -> zero or one output beat; no timing.
  Engine               : cycle view. A register-for-register, handshake-for-handshake model of the RTL (one clock
                         per clock() call), used to prove that gaps and stalls change only WHEN beats move.

Behaviour (spec.json): the engine keeps the last three energies and a count of samples seen since the last s_last
or reset. From the 4th sample on, every accepted input beat produces one output beat
  m_data = {error, class, sum[5:0]},  sum = w0*e[n-3] + w1*e[n-2] + w2*e[n-1] + w3*e[n],  class = (sum >= threshold),
  m_last = s_last of that input beat.
`error` is sticky inside a stream: any beat with s_data[7:4] != 0 sets it, s_last clears it for the next stream. The low
nibble of a bad beat is still used as the energy. A stream shorter than 4 samples produces no output at all."""
from common import weights

WARMUP = 3


def window_sum(w, e):
    """Exact integer weighted sum of a window (oldest first)."""
    return sum(a * b for a, b in zip(w, e))


def pack(error, cls, s):
    return (error & 1) << 7 | (cls & 1) << 6 | (s & 63)


def params():
    p = weights()
    return p["weights"], p["threshold"]


class State:
    def __init__(self):
        self.hist, self.count, self.err = [0, 0, 0], 0, 0


def beat(st, w, T, s_data, s_last):
    """One accepted input beat. Returns (m_data, m_last) or None. Updates the state."""
    bad = int((s_data >> 4) != 0)
    x = s_data & 15
    e = st.err | bad
    out = None
    if st.count == WARMUP:
        s = window_sum(w, st.hist + [x])
        assert -32 <= s <= 31, "sum does not fit 6 bits"
        out = (pack(e, int(s >= T), s), int(bool(s_last)))
    st.hist = st.hist[1:] + [x]
    if s_last:
        st.count, st.err = 0, 0
    else:
        st.count, st.err = min(st.count + 1, WARMUP), e
    return out


def run_beats(beats):
    """beats: list of (s_data, s_last, reset_before). reset_before clears the state (a reset between beats; any
    result still waiting at the output is the testbench's business, see gen_rom.py). Returns one entry per beat:
    (m_data, m_last) or None."""
    w, T = params()
    st, res = State(), []
    for d, l, r in beats:
        if r:
            st = State()
        res.append(beat(st, w, T, d, l))
    return res


class Engine:
    """Cycle-level model. Registers match the RTL: history, count, err, and the output register
    (mv = m_valid, md = m_data, ml = m_last). s_ready = !m_valid | m_ready (combinational in m_ready)."""
    def __init__(self):
        self.w, self.T = params()
        self.reset()

    def reset(self):
        self.st = State()
        self.mv, self.md, self.ml = 0, 0, 0

    def s_ready(self, m_ready):
        return int((not self.mv) or bool(m_ready))

    def clock(self, rst, s_valid, s_data, s_last, m_ready):
        """One rising edge. Returns (out, beat_in, beat_out): out = (m_valid, m_data, m_last) as shown BEFORE the
        edge, beat_in / beat_out = whether an input / output beat transferred at this edge."""
        out = (self.mv, self.md, self.ml)
        if rst:
            self.reset()
            return out, 0, 0
        beat_in = int(bool(s_valid) and bool(self.s_ready(m_ready)))
        beat_out = int(bool(self.mv) and bool(m_ready))
        if beat_out:
            self.mv = 0
        if beat_in:
            r = beat(self.st, self.w, self.T, s_data, s_last)
            if r is not None:
                self.mv, (self.md, self.ml) = 1, r
        return out, beat_in, beat_out
