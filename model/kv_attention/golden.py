#!/usr/bin/env python3
"""golden.py -- bit-exact, cycle-exact reference of the tiny_kv_attention engines (see spec.md, which is the contract;
if spec.md and this file disagree this file is the reference and the disagreement is a bug to report).

  python3 model/kv_attention/golden.py --check            self-checks + task metrics (accuracy, int4 agreement)
  python3 model/kv_attention/golden.py --trace [variant]  cache contents, scores and the response per step (default kv_attn_n8)

Python 3.9 standard library only, deterministic (own LCG, no hash/set ordering, no clock, no network)."""
import sys
sys.dont_write_bytecode = True    # keep the repository free of __pycache__

D = 4                 # model dimension
VOCAB = 16            # tokens 0..15; token t = 4*a + b : key id a = t >> 2, value b = t & 3
PMAX = 31             # position saturates here (5-bit counter)
HIT_MIN = 32          # score >= 32  <=>  the key ids are equal (see check_score_range)

OPC_RESET, OPC_PREFILL, OPC_DECODE = 1, 2, 3
E_OK, E_OPCODE, E_TOKEN, E_FULL, E_FRAME = 0, 1, 2, 3, 4
ST_HIT = 0x10         # status bit 4 on a successful DECODE: the best entry has the same key id as the query
NO_INDEX = 0xFF       # index beat of a DECODE on an empty cache

# name -> (N cache entries, kv bits, ring)
VARIANTS = {
    "kv_attn_n8":      dict(N=8,  bits=8, ring=0),
    "kv_attn_n4":      dict(N=4,  bits=8, ring=0),
    "kv_attn_n16":     dict(N=16, bits=8, ring=0),
    "kv_attn_n8_int4": dict(N=8,  bits=4, ring=0),
    "kv_attn_n8_ring": dict(N=8,  bits=8, ring=1),
}

# ---- fixed, hand-picked parameters (no training; rationale in spec.md section 2) --------------------------------------
def _embed(t):
    a, b = t >> 2, t & 3
    return [1 if a & 2 else -1, 1 if a & 1 else -1, 2 * b - 3, 0]      # [key bit1, key bit0, value level, 0]
EMB = [_embed(t) for t in range(VOCAB)]
# matrices are indexed [row][col]; out_r = sum_c W[r][c] * x[c] + b[r]
WQ = [[4, 0, 0, 0], [0, 4, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
WK = [[4, 0, 0, 0], [0, 4, 0, 0], [0, 0, 0, 0], [0, 0, 0, 1]]
WV = [[0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1]]
BQ = [0, 0, 0, 1]
BK = [0, 0, 0, 0]
BV = [0, 0, 0, 0]


def matvec(w, x, b):
    return [sum(w[r][c] * x[c] for c in range(D)) + b[r] for r in range(D)]


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def s8(v):
    assert -128 <= v <= 127, v
    return v


def u8(v):
    return s8(v) & 0xFF


def to_s8(b):
    return b - 256 if b >= 128 else b


def project(tok, pos):
    """token and position -> (q, k, v), int8 vectors. x = EMB[tok] + (0, 0, 0, pos)."""
    x = list(EMB[tok]); x[3] += pos
    return ([s8(c) for c in matvec(WQ, x, BQ)], [s8(c) for c in matvec(WK, x, BK)], [s8(c) for c in matvec(WV, x, BV)])


# ---- KV storage format -------------------------------------------------------------------------------------------------
def store_k(k, bits):
    """int8: stored as is. int4: s4 = clamp((k + 2) >> 2, -8, 7) (scale 4, round half up, arithmetic shift)."""
    return list(k) if bits == 8 else [clamp((c + 2) >> 2, -8, 7) for c in k]


def deq_k(ks, bits):
    """the key the dot-product unit sees: int8 as is; int4: stored << 2"""
    return list(ks) if bits == 8 else [c << 2 for c in ks]


def store_v(v, bits):
    """int8: as is. int4: saturate to -8..7 (scale 1; the value level -3..3 and the key bits +-1 are exact)."""
    return list(v) if bits == 8 else [clamp(c, -8, 7) for c in v]


def key_of(t):
    return t >> 2


def val_of(t):
    return t & 3


def level_to_val(level):
    return (level + 3) // 2


class Engine:
    """State and behaviour of one engine. command(beats) -> (out_beats, latency, info)."""

    def __init__(self, name):
        p = VARIANTS[name]
        self.name, self.N, self.bits, self.ring = name, p["N"], p["bits"], p["ring"]
        self.reset()

    def reset(self):
        self.ks = [[0] * D for _ in range(self.N)]     # stored keys (int8, or int4 values -8..7)
        self.vs = [[0] * D for _ in range(self.N)]
        self.kpos = [0] * self.N                       # trace only: position each slot was written with
        self.count, self.wp, self.pos = 0, 0, 0

    def _write(self, tok):
        _, k, v = project(tok, self.pos)
        s = self.wp
        self.ks[s], self.vs[s], self.kpos[s] = store_k(k, self.bits), store_v(v, self.bits), self.pos
        self.wp = (self.wp + 1) % self.N
        self.count = min(self.count + 1, self.N)
        self.pos = min(self.pos + 1, PMAX)

    def _full(self):
        return (not self.ring) and self.count == self.N

    def scores(self, q):
        return [sum(q[i] * deq_k(self.ks[j], self.bits)[i] for i in range(D)) for j in range(self.count)]

    def command(self, beats):
        """beats: input beats of one frame (s_last on the last). Returns (out_beats, latency, info)."""
        info = {}
        op = beats[0]
        if op not in (OPC_RESET, OPC_PREFILL, OPC_DECODE):
            return [E_OPCODE, self.count], 2, info
        if op == OPC_RESET:
            if len(beats) != 1:
                return [E_FRAME, self.count], 2, info
            self.reset()
            return [E_OK, 0], 2, info
        if op == OPC_PREFILL:
            err = E_OK
            for t in beats[1:]:
                if err:
                    continue                            # tokens after the first error are consumed and ignored
                if t > VOCAB - 1:
                    err = E_TOKEN
                elif self._full():
                    err = E_FULL
                else:
                    self._write(t)
            return [err, self.count], 2, info
        # DECODE
        if len(beats) != 2:
            return [E_FRAME, self.count], 2, info
        t = beats[1]
        if t > VOCAB - 1:
            return [E_TOKEN, self.count], 2, info
        if self._full():
            return [E_FULL, self.count], 2, info
        n = self.count
        q, _, _ = project(t, self.pos)
        sc = self.scores(q)
        for s in sc:
            assert -128 <= s <= 127
        best, bi = None, NO_INDEX
        for j, s in enumerate(sc):
            if best is None or s > best:               # strictly greater: the lowest index wins a tie
                best, bi = s, j
        info.update(q=q, scores=sc, n=n, index=bi)
        if n == 0:
            out_tail = [NO_INDEX, 0, 0, 0, 0, 0]
            status = E_OK
        else:
            status = E_OK | (ST_HIT if best >= HIT_MIN else 0)
            out_tail = [bi, u8(best)] + [u8(c) for c in self.vs[bi]]
        self._write(t)
        return [status, self.count] + out_tail, n + 3, info


def decode_response(resp):
    """-> dict for a successful DECODE response (8 beats)"""
    return dict(status=resp[0], hit=bool(resp[0] & ST_HIT), count=resp[1], index=resp[2], score=to_s8(resp[3]),
                v=[to_s8(b) for b in resp[4:8]])


# ---- deterministic random numbers ---------------------------------------------------------------------------------------
class LCG:
    def __init__(self, seed):
        self.s = seed & 0x7FFFFFFF

    def next(self):
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s

    def below(self, n):
        return (self.next() >> 8) % n


# ---- the task: recall the value last stored under the same key ----------------------------------------------------------
def run_episode(eng, prompt, queries):
    """prefill `prompt`, then decode each query token. Returns the per-decode records.
    oracle_all: value of the most recent earlier token with the same key (unbounded memory);
    oracle_win: same, among the last min(count, N) tokens only (what a ring of size N can hold)."""
    eng.reset()
    hist = []
    r, _, _ = eng.command([OPC_PREFILL] + list(prompt))
    hist.extend(prompt[:r[1]] if not eng.ring else prompt)
    out = []
    for qt in queries:
        n_before = eng.count
        resp, lat, info = eng.command([OPC_DECODE, qt])
        assert resp[0] & 0x0F == E_OK and len(resp) == 8 and lat == n_before + 3
        d = decode_response(resp)
        a = key_of(qt)
        o_all = next((val_of(t) for t in reversed(hist) if key_of(t) == a), None)
        win = hist[-n_before:] if n_before else []
        o_win = next((val_of(t) for t in reversed(win) if key_of(t) == a), None)
        got = level_to_val(d["v"][0]) if d["hit"] else None
        out.append(dict(q=qt, d=d, o_all=o_all, o_win=o_win, got=got, n=n_before))
        hist.append(qt)
    return out


def make_episodes(n_ep, N, ring, total, seed):
    rng = LCG(seed)
    eps = []
    for _ in range(n_ep):
        if ring:
            p = N
        else:
            p = 1 + rng.below(N - 1)
        t = (total if ring else N)
        prompt = [rng.below(VOCAB) for _ in range(p)]
        queries = [rng.below(VOCAB) for _ in range(t - p)]
        eps.append((prompt, queries))
    return eps


def task_metrics(name, n_ep=400, total=32, seed=7):
    N = VARIANTS[name]["N"]
    eng = Engine(name)
    steps = []
    for prompt, queries in make_episodes(n_ep, N, VARIANTS[name]["ring"], total, seed + N):
        steps += run_episode(eng, prompt, queries)
    m = dict(steps=len(steps))
    have = [s for s in steps if s["o_all"] is not None]
    m["with_match"] = len(have)
    m["acc_all"] = sum(1 for s in have if s["got"] == s["o_all"])
    havew = [s for s in steps if s["o_win"] is not None]
    m["with_match_win"] = len(havew)
    m["acc_win"] = sum(1 for s in havew if s["got"] == s["o_win"])
    m["false_hit"] = sum(1 for s in steps if s["d"]["hit"] and s["o_win"] is None)
    m["missed_hit"] = sum(1 for s in steps if (not s["d"]["hit"]) and s["o_win"] is not None)
    return m, steps


def agreement(name_a, name_b, n_ep=400, seed=7):
    """same episodes (generated for N of name_a) on two variants: how often index and recalled value agree."""
    N = VARIANTS[name_a]["N"]
    assert VARIANTS[name_b]["N"] == N and VARIANTS[name_a]["ring"] == VARIANTS[name_b]["ring"]
    ea, eb = Engine(name_a), Engine(name_b)
    n = same_idx = same_val = same_all = 0
    for prompt, queries in make_episodes(n_ep, N, VARIANTS[name_a]["ring"], 32, seed + N):
        ra, rb = run_episode(ea, prompt, queries), run_episode(eb, prompt, queries)
        for x, y in zip(ra, rb):
            n += 1
            same_idx += x["d"]["index"] == y["d"]["index"]
            same_val += x["got"] == y["got"]
            same_all += (x["d"]["index"], x["d"]["v"][0]) == (y["d"]["index"], y["d"]["v"][0])
    return dict(steps=n, same_index=same_idx, same_value=same_val)


# ---- self-checks --------------------------------------------------------------------------------------------------------
def check_score_range():
    """exhaustive over (query token, entry token, entry position, query position): range, HIT iff same key."""
    lo = {8: 10 ** 9, 4: 10 ** 9}; hi = {8: -10 ** 9, 4: -10 ** 9}
    for bits in (8, 4):
        for tq in range(VOCAB):
            for pq in (0, PMAX):
                q, _, _ = project(tq, pq)
                for te in range(VOCAB):
                    for pe in range(PMAX + 1):
                        _, k, _ = project(te, pe)
                        kh = deq_k(store_k(k, bits), bits)
                        s = sum(q[i] * kh[i] for i in range(D))
                        lo[bits], hi[bits] = min(lo[bits], s), max(hi[bits], s)
                        assert (s >= HIT_MIN) == (key_of(tq) == key_of(te)), (bits, tq, te, pe, s)
    assert lo[8] == -32 and hi[8] == 63, (lo, hi)
    assert -128 <= lo[4] and hi[4] <= 60, (lo, hi)
    return lo, hi


def pct(a, b):
    return "%6.2f %%" % (100.0 * a / b) if b else "   n/a  "


def check():
    ok = 0

    def req(c, msg):
        nonlocal ok
        if not c:
            raise AssertionError("FAIL " + msg)
        ok += 1

    lo, hi = check_score_range()
    req(True, "score range")
    print("score range int8 K: %d .. %d (fits int8, no saturation needed); int4 K: %d .. %d; HIT (score >= 32) <=> same key id: exhaustive OK"
          % (lo[8], hi[8], lo[4], hi[4]))
    # protocol spot checks (also covered exhaustively by gen.py vectors)
    e = Engine("kv_attn_n4")
    req(e.command([0])[0] == [E_OPCODE, 0], "bad opcode")
    req(e.command([OPC_RESET, 0])[0] == [E_FRAME, 0], "reset frame")
    req(e.command([OPC_DECODE])[0] == [E_FRAME, 0], "decode no token")
    req(e.command([OPC_DECODE, 16])[0] == [E_TOKEN, 0], "decode bad token")
    r, lat, _ = e.command([OPC_DECODE, 5])
    req(r == [0, 1, NO_INDEX, 0, 0, 0, 0, 0] and lat == 3, "decode on empty cache")
    r, lat, _ = e.command([OPC_PREFILL, 1, 2, 99, 3, 4])
    req(r == [E_TOKEN, 3] and lat == 2, "prefill stops at the bad token (1 from the decode + 2 from the frame)")
    r, lat, _ = e.command([OPC_PREFILL, 7])
    req(r == [0, 4], "prefill fills the cache")
    req(e.command([OPC_PREFILL, 7])[0] == [E_FULL, 4], "cache full (prefill)")
    req(e.command([OPC_DECODE, 7])[0] == [E_FULL, 4], "cache full (decode)")
    req(e.command([OPC_RESET])[0] == [0, 0], "reset")
    # worked example: A1 B3 A2 C0, query B -> value 3 (index 1); query A -> value 2 (index 2, most recent)
    T = lambda k, v: 4 * k + v
    e = Engine("kv_attn_n8")
    e.command([OPC_PREFILL, T(0, 1), T(1, 3), T(0, 2), T(2, 0)])
    r, lat, _ = e.command([OPC_DECODE, T(1, 0)])
    d = decode_response(r)
    req(d["hit"] and d["index"] == 1 and level_to_val(d["v"][0]) == 3 and lat == 7, "worked example B")
    r, lat, _ = e.command([OPC_DECODE, T(0, 0)])
    d = decode_response(r)
    req(d["hit"] and d["index"] == 2 and level_to_val(d["v"][0]) == 2 and lat == 8, "latest write wins (A -> 2)")
    # order matters: the same two tokens in the opposite order give the other value
    for first, second in ((T(0, 1), T(0, 2)), (T(0, 2), T(0, 1))):
        e = Engine("kv_attn_n8"); e.command([OPC_PREFILL, first, second])
        d = decode_response(e.command([OPC_DECODE, T(0, 0)])[0])
        req(level_to_val(d["v"][0]) == val_of(second), "order matters")
    # tie rule: int4 cannot tell positions 0 and 1 apart -> the lowest index (oldest) wins
    e = Engine("kv_attn_n8_int4"); e.command([OPC_PREFILL, T(0, 1), T(0, 2)])
    r, _, info = e.command([OPC_DECODE, T(0, 0)])
    req(info["scores"][0] == info["scores"][1] and decode_response(r)["index"] == 0, "int4 tie -> lowest index")
    e = Engine("kv_attn_n8"); e.command([OPC_PREFILL, T(0, 1), T(0, 2)])
    r, _, info = e.command([OPC_DECODE, T(0, 0)])
    req(info["scores"][0] != info["scores"][1] and decode_response(r)["index"] == 1, "int8 resolves it")
    # ring: wraps, window = last N, latency saturates at N+3
    e = Engine("kv_attn_n8_ring")
    e.command([OPC_PREFILL] + [T(0, i % 4) for i in range(11)])
    req(e.count == 8 and e.wp == 3, "ring wrap pointers")
    r, lat, _ = e.command([OPC_DECODE, T(0, 0)])
    req(lat == 11 and r[1] == 8, "ring decode latency N+3, count stays N")
    # latency formula n+3 on every fill level, every variant
    for name, p in VARIANTS.items():
        for n in range(p["N"] + (1 if p["ring"] else 0)):
            e = Engine(name); e.command([OPC_PREFILL] + [0] * min(n, p["N"]))
            _, lat, _ = e.command([OPC_DECODE, 0])
            req(lat == min(n, p["N"]) + 3, "latency " + name)
    # task metrics
    print("\nTask: recall the value last stored under the same key (token = 4*key + value; 400 episodes, seed 7)")
    print("%-16s %5s %-5s %4s %8s | %-26s | %-26s | false/missed HIT" % ("variant", "N", "bits", "ring", "steps", "accuracy vs unbounded oracle",
                                                                      "accuracy vs window oracle"))
    results = {}
    for name, p in VARIANTS.items():
        m, _ = task_metrics(name)
        results[name] = m
        print("%-16s %5d %-5d %4d %8d | %5d / %5d  %s | %5d / %5d  %s | %d / %d" % (
            name, p["N"], p["bits"], p["ring"], m["steps"], m["acc_all"], m["with_match"], pct(m["acc_all"], m["with_match"]),
            m["acc_win"], m["with_match_win"], pct(m["acc_win"], m["with_match_win"]), m["false_hit"], m["missed_hit"]))
        if not p["ring"] and p["bits"] == 8:
            req(m["acc_all"] == m["with_match"] and m["false_hit"] == 0 and m["missed_hit"] == 0, "int8 non-ring is exact: " + name)
    m48, _ = task_metrics("kv_attn_n8_ring", total=48)
    print("kv_attn_n8_ring, 48-token episodes (position saturates at 31): window-oracle accuracy %d / %d  %s;  unbounded oracle %s"
          % (m48["acc_win"], m48["with_match_win"], pct(m48["acc_win"], m48["with_match_win"]), pct(m48["acc_all"], m48["with_match"])))
    req(results["kv_attn_n8_ring"]["acc_win"] == results["kv_attn_n8_ring"]["with_match_win"], "ring exact against the window while pos < 32")
    a = agreement("kv_attn_n8", "kv_attn_n8_int4")
    print("\nint4 vs int8 (same 400 kv_attn_n8 episodes, %d decode steps): same selected index %d (%s), same recalled value %d (%s)"
          % (a["steps"], a["same_index"], pct(a["same_index"], a["steps"]).strip(), a["same_value"], pct(a["same_value"], a["steps"]).strip()))
    i4 = results["kv_attn_n8_int4"]
    print("int4 task accuracy (unbounded oracle): %s;  int8: %s" % (pct(i4["acc_all"], i4["with_match"]).strip(),
          pct(results["kv_attn_n8"]["acc_all"], results["kv_attn_n8"]["with_match"]).strip()))
    # determinism
    m2, _ = task_metrics("kv_attn_n8_int4")
    req(m2 == i4, "deterministic")
    print("\nCache size = N x 2 x d x bits ; DECODE latency = n + 3 cycles (n cached entries scanned), other commands 2")
    print("%-16s %4s %-4s %10s %22s %s" % ("variant", "N", "bits", "cache bits", "decode latency (cycles)", "(worst)"))
    for name, p in VARIANTS.items():
        worst = p["N"] + 3 if p["ring"] else p["N"] + 2
        print("%-16s %4d %-4d %10d %22s %d" % (name, p["N"], p["bits"], p["N"] * 2 * D * p["bits"],
              "n+3 (n=0..%d)" % (p["N"] if p["ring"] else p["N"] - 1), worst))
    print("\nPASS golden: %d checks" % ok)


# ---- trace --------------------------------------------------------------------------------------------------------------
def tname(t):
    return "%s%d" % ("ABCD"[t >> 2], t & 3)


def dump(e):
    for j in range(e.N):
        tag = "  " if j < e.count else "--"
        extra = "  <- next write" if j == e.wp and (e.ring or e.count < e.N) else ""
        print("   slot %2d %s K=%-18s V=%-18s pos=%2d%s" % (j, tag, e.ks[j], e.vs[j], e.kpos[j], extra))


def trace(name):
    e = Engine(name)
    p = VARIANTS[name]
    print("%s: N=%d, KV %d-bit, %s, cache %d bits" % (name, p["N"], p["bits"], "ring" if p["ring"] else "no ring",
                                                      p["N"] * 2 * D * p["bits"]))
    T = lambda k, v: 4 * k + v
    script = [("PREFILL", [T(0, 1), T(1, 3), T(0, 2), T(2, 0)]), ("DECODE", [T(1, 0)]), ("DECODE", [T(0, 0)]),
              ("DECODE", [T(3, 1)]), ("DECODE", [T(2, 2)]), ("PREFILL", [T(0, 3), T(0, 0)]), ("DECODE", [T(0, 1)]),
              ("DECODE", [T(1, 1)]), ("DECODE", [T(0, 2)]), ("DECODE", [T(2, 1)])]
    for op, toks in script:
        beats = [OPC_PREFILL if op == "PREFILL" else OPC_DECODE] + toks
        resp, lat, info = e.command(beats)
        print("\n%s %s  -> response %s  latency %d cycles" % (op, " ".join(tname(t) for t in toks),
                                                              " ".join("%02x" % b for b in resp), lat))
        if info.get("n"):
            print("   q=%s  scores over slots 0..%d: %s" % (info["q"], info["n"] - 1, info["scores"]))
            d = decode_response(resp)
            print("   selected slot %d score %d V=%s  HIT=%s  recalled value %s" % (
                d["index"], d["score"], d["v"], d["hit"], level_to_val(d["v"][0]) if d["hit"] else "-"))
        elif op == "DECODE":
            print("   (response status %s)" % resp[0])
        dump(e)


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--check" in a:
        check()
    elif "--trace" in a:
        i = a.index("--trace")
        trace(a[i + 1] if i + 1 < len(a) else "kv_attn_n8")
    else:
        print(__doc__)
