#!/usr/bin/env python3
"""gen.py -- generate, from golden.py, for each format fmt in golden.FMTS (bin tern int4 int8 fp8 fp16 bf16):
  designs/prec_<fmt>/rtl/prec_<fmt>_rom.v   the 9 weights + bias (bin: threshold), continuous assigns only
  designs/prec_<fmt>/tb/vectors.hex         test vectors for shared/tb/stream_tb.vh (unchanged)
Every generated file carries the sha256 of its inputs (golden.py, gen.py). Files are written only when their
content changes, so a second run writes nothing. Never edit the outputs; change golden.py and re-run:
  python3 model/precision_hw/gen.py

Vector file (same layout as model/tiny_ai/gen_rom.py): $readmemh of 8-bit words in 16-word records. Record 0 is the
header A5, count high, count low. Each case record: [0] number of beats (1..12), [1..12] s_data of each beat (s_last
on the last), [13] expected beat 0, [14] expected beat 1, [15] expected latency. stream_tb.vh holds at most 1023
cases (MAXREC = 1024), so the case list is capped below that. The SAME case list (inputs) is used for all seven
formats; only the expected values differ."""
import hashlib
import os
import sys
sys.dont_write_bytecode = True     # keep the repository free of __pycache__
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import golden as g   # noqa: E402

MAX_BEATS = 12
MAX_CASES = 1023            # stream_tb.vh: nvec < MAXREC = 1024
N_TEST_CASES = 600          # first images of the held-out test set
N_MARGIN, N_RANDOM = 120, 60


def sha256_of(*paths):
    h = hashlib.sha256()
    for p in paths:
        h.update(os.path.relpath(p, REPO).encode() + b"\0" + open(p, "rb").read())
    return h.hexdigest()


def write_if_changed(path, text):
    try:
        if open(path).read() == text:
            return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return True

# ------------------------------------------------------------------------------------------------ cases
def cases(p):
    """[(label, beats)] -- identical for every format."""
    (_, _), (tex, _) = g.dataset()
    out = [("test%d" % i, list(x)) for i, x in enumerate(tex[:N_TEST_CASES])]
    # held-out images beyond the first 600 on which some non-binary format disagrees with fp32
    for i, x in enumerate(tex[N_TEST_CASES:], N_TEST_CASES):
        r = g.ref_fp32(x, p)[0]
        if any(g.infer(f, x, p)[0] != r for f in g.FMTS if f != "bin"):
            out.append(("test%d-disagree" % i, list(x)))
    # edge cases
    out += [("all%d" % v, [v] * 9) for v in range(16)]                       # all-0, all-15, 7/8 boundary
    out += [("bright%d" % i, [15 * (j == i) for j in range(9)]) for i in range(9)]
    out += [("dark%d" % i, [0 if j == i else 15 for j in range(9)]) for i in range(9)]
    out += [("eight%d" % i, [8 if j == i else 7 for j in range(9)]) for i in range(9)]
    out += [("vbar", [15 if j % 3 == 1 else 0 for j in range(9)]), ("hbar", [15 if j // 3 == 1 else 0 for j in range(9)]),
            ("checker0", [15 * ((j + 1) % 2) for j in range(9)]), ("checker1", [15 * (j % 2) for j in range(9)])]
    for f in ("fp32",) + g.FMTS:                                              # sum maximum / minimum per format
        if f == "fp32":
            pos = [v > 0 for v in p["fp32"]["w"]]
        elif f == "bin":
            pos = [v == 1 for v in p["bin"]["w"]]
        elif f in g.INT_QMAX:
            pos = [v > 0 for v in p[f]["w"]]
        else:
            pos = [g.f_decode(g.FLOAT_FMT[f][0], c) > 0 for c in p[f]["w"]]
        out.append(("max-%s" % f, [15 if s else 0 for s in pos]))
        out.append(("min-%s" % f, [0 if s else 15 for s in pos]))
    # near the decision boundary: uniform random images with the smallest |fp32 sum|, plus plain random images
    rng = g.LCG(7)
    pool = [[int(rng.u() * 16) for _ in range(9)] for _ in range(20000)]
    ranked = sorted(range(len(pool)), key=lambda k: (abs(g.ref_fp32(pool[k], p)[1]), k))
    out += [("margin%d" % k, pool[k]) for k in ranked[:N_MARGIN]]
    out += [("random%d" % k, pool[k]) for k in range(N_RANDOM)]
    # protocol cases
    base = list(tex[0])
    out += [("short%d" % n, base[:n]) for n in range(1, 9)]
    out += [("long10", base + [5]), ("long11", base + [0, 15]), ("long12", base + [0xFF, 1, 2])]
    for i in range(9):
        for bad in (0x10, 0x1F, 0x80, 0xFF):
            out.append(("bad%d=%02x" % (i, bad), base[:i] + [bad] + base[i + 1:]))
    out += [("short3-bad1", [base[0], 0x40, base[2]]), ("single-bad", [0xF0]), ("all-bad", [0xFF] * 9),
            ("long12-bad", [0x20] * 12)]
    assert all(1 <= len(b) <= MAX_BEATS for _, b in out)
    assert all(0 <= v <= 0xFF for _, b in out for v in b)
    assert len(out) <= MAX_CASES, "too many cases for stream_tb.vh: %d" % len(out)
    return out

# ------------------------------------------------------------------------------------------------ ROM
def header(h):
    return ("// Generated by model/precision_hw/gen.py from model/precision_hw/golden.py; do not edit.\n"
            "// source sha256 %s\n`timescale 1ns/1ps\n" % h)


def hexlit(width, code, signed):
    return "%d'%sh%0*X" % (width, "s" if signed else "", (width + 3) // 4, code & ((1 << width) - 1))


def rom_verilog(fmt, p, h):
    q = p[fmt]
    fw, fb = p["fp32"]["w"], p["fp32"]["b"]
    wb, bb = g.WBITS[fmt], g.BIAS_BITS[fmt]
    signed = fmt in g.INT_QMAX
    sw = "signed " if signed else ""
    rng = lambda w: ("[%d:0]" % (w - 1)) if w > 1 else ""
    lines = []
    if fmt == "bin":
        what = ("1-bit weights (1 = +1, 0 = -1: the sign of the fp32 weight, w >= 0 -> 1) and the integer threshold "
                "T of the XNOR-popcount neuron: class = (matches >= T)")
        for i, c in enumerate(q["w"]):
            lines.append("    localparam       W%d = 1'b%d;   // fp32 %+.6f" % (i, c, fw[i]))
        lines.append("    localparam [3:0] T  = 4'd%d;   // fitted on the binarised training set" % q["b"])
        ports = ("    input  wire [3:0] addr,        // pixel index 0..8, raster order (>= 9 reads 0)\n"
                 "    output wire       weight,\n"
                 "    output wire [3:0] threshold")
        body = "    assign threshold = T;\n"
        zero = "1'b0"
    elif signed:
        what = {"tern": "ternary weights {-1,0,+1} (2-bit two's complement; 2'b10 never stored)",
                "int4": "int4 weights (two's complement, -7..+7)",
                "int8": "int8 weights (two's complement, -127..+127)"}[fmt]
        what += ", integer bias in the same units, %d-bit two's complement. Real value = code * scale, scale = %.9g" % (
            bb, float(q["scale"]))
        for i, c in enumerate(q["w"]):
            lines.append("    localparam signed %-6s W%d = %s;   // %+d  (fp32 %+.6f)" % (rng(wb), i, hexlit(wb, c, True), c, fw[i]))
        lines.append("    localparam signed %-6s B  = %s;   // %+d  (fp32 bias %+.6f)" % (rng(bb), hexlit(bb, q["b"], True), q["b"], fb))
        ports = ("    input  wire        [3:0]  addr,     // pixel index 0..8, raster order (>= 9 reads 0)\n"
                 "    output wire signed %-6s weight,\n"
                 "    output wire signed %-6s bias") % (rng(wb), rng(bb))
        body = "    assign bias = B;\n"
        zero = hexlit(wb, 0, True)
    else:
        wf = g.FLOAT_FMT[fmt][0]
        what = {"fp8": "OCP fp8 E4M3 bit patterns (bias 7, no Inf; subnormals flushed to +0 at quantisation)",
                "fp16": "IEEE binary16 bit patterns (subnormals flushed to +0 at quantisation)",
                "bf16": "bfloat16 bit patterns (subnormals flushed to +0 at quantisation)"}[fmt]
        what += "; the bias is in the same format"
        for i, c in enumerate(q["w"]):
            lines.append("    localparam %-6s W%d = %s;   // %+.9g  (fp32 %+.6f)" % (rng(wb), i, hexlit(wb, c, False),
                                                                             float(g.f_decode(wf, c)), fw[i]))
        lines.append("    localparam %-6s B  = %s;   // %+.9g  (fp32 bias %+.6f)" % (rng(bb), hexlit(bb, q["b"], False),
                                                                           float(g.f_decode(wf, q["b"])), fb))
        ports = ("    input  wire        [3:0]  addr,     // pixel index 0..8, raster order (>= 9 reads 0)\n"
                 "    output wire        %-6s weight,\n"
                 "    output wire        %-6s bias") % (rng(wb), rng(bb))
        body = "    assign bias = B;\n"
        zero = hexlit(wb, 0, False)
    sel = "    assign weight = " + "\n                    ".join("(addr == 4'd%d) ? W%d :" % (i, i) for i in range(9)) + " %s;\n" % zero
    return header(h) + """// prec_%s parameter ROM (model/precision_hw/spec.md): %s.
// Constants are continuous assignments (no always block): read by the one serial MAC, weight[addr] per input.
module prec_%s_rom (
%s
);
%s
%s%sendmodule
""" % (fmt, what, fmt, ports, "\n".join(lines), sel, body)

# ------------------------------------------------------------------------------------------------ vectors
def vectors(fmt, cs, p, h):
    lines = ["// Generated by model/precision_hw/gen.py from model/precision_hw/golden.py; do not edit.",
             "// source sha256 %s" % h,
             "// prec_%s: %d cases (record layout in model/precision_hw/gen.py; case list identical for all formats)"
             % (fmt, len(cs)),
             " ".join("%02x" % v for v in [0xA5, len(cs) >> 8, len(cs) & 0xFF] + [0] * 13)]
    for _, beats in cs:
        b0, b1, lat = g.run(fmt, beats, p)
        rec = [len(beats)] + beats + [0] * (MAX_BEATS - len(beats)) + [b0, b1, lat]
        lines.append(" ".join("%02x" % v for v in rec))
    return "\n".join(lines) + "\n"


def main():
    p = g.params()
    for f in ("fp8", "fp16", "bf16"):          # precondition of the range proof (spec.md 4.6)
        assert max(abs(v) for v in p["fp32"]["w"] + [p["fp32"]["b"]]) <= 256
    h = sha256_of(os.path.join(HERE, "golden.py"), os.path.abspath(__file__))
    cs = cases(p)
    for fmt in g.FMTS:
        rom = os.path.join(REPO, "designs", "prec_%s" % fmt, "rtl", "prec_%s_rom.v" % fmt)
        vec = os.path.join(REPO, "designs", "prec_%s" % fmt, "tb", "vectors.hex")
        w1 = write_if_changed(rom, rom_verilog(fmt, p, h))
        w2 = write_if_changed(vec, vectors(fmt, cs, p, h))
        print("gen: %-5s %s %s, %s %s (%d cases)" % (fmt, os.path.relpath(rom, REPO), "written" if w1 else "unchanged",
                                                  os.path.relpath(vec, REPO), "written" if w2 else "unchanged", len(cs)))


if __name__ == "__main__":
    main()
