#!/usr/bin/env python3
"""report.py -- the precision study table (model/precision_hw/spec.md).

  python3 model/precision_hw/report.py

Part 1 (always): per format, from golden.py: bits per weight, parameter memory (9 weights + bias or threshold),
accumulator width, test accuracy on the 2000 held-out images, % of decisions equal to the fp32 reference, and bits
moved per inference (input bits into the MAC + parameter bits read). Float formats also show how often a rounding
was inexact and how often FTZ fired.
Part 2 (when present): designs/prec_<fmt>/output/metrics.json (LibreLane) -> std cells, sequential cells, std-cell
area, routed wirelength, vias, worst setup slack, total power. Missing designs/keys print '-'."""
import json
import os
import sys
sys.dont_write_bytecode = True     # keep the repository free of __pycache__

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import golden as g   # noqa: E402

IN_BITS = {"bin": 1, "tern": 4, "int4": 4, "int8": 4, "fp8": 4, "fp16": 4, "bf16": 4}   # per pixel, into the MAC
ACC_DESC = {"bin": "4-bit count", "tern": "10-bit int", "int4": "12-bit int", "int8": "17-bit int",
            "fp8": "fp16", "fp16": "fp16", "bf16": "bf16"}
METRICS = [  # (column, key or list of keys (first present wins), format)
    ("std cells", ["design__instance__count__stdcell"], "%d"),
    ("seq cells", ["design__instance__count__class:sequential_cell"], "%d"),
    ("comb cells", ["design__instance__count__class:multi_input_combinational_cell"], "%d"),
    ("cell area um2", ["design__instance__area__stdcell"], "%.0f"),
    ("wirelength um", ["route__wirelength", "global_route__wirelength"], "%.0f"),
    ("vias", ["route__vias", "global_route__vias"], "%d"),
    ("setup WS ns", ["timing__setup__ws"], "%.2f"),
    ("power mW", ["power__total"], "%.4f"),
]


def model_table():
    p = g.params()
    (trx, try_), (tex, tey) = g.dataset()
    ref = [g.ref_fp32(x, p)[0] for x in tex]
    ref_acc = sum(r == y for r, y in zip(ref, tey)) / len(tey)
    ref_tr = sum(g.ref_fp32(x, p)[0] == y for x, y in zip(trx, try_)) / len(try_)
    print("Precision study: 3x3 image, 4-bit pixels, vertical (1) vs horizontal (0) bar; one neuron, 9 weights + bias")
    print("train %d / test %d images; fp32 reference: train %.2f%%, test %.2f%% (fp32 weights, exact sum)"
          % (len(trx), len(tex), 100 * ref_tr, 100 * ref_acc))
    print()
    hdr = ("format", "bits/w", "param bits", "accumulator", "test acc", "same as fp32", "bits moved", "inexact/inf", "ftz/inf")
    print("%-6s %6s %10s %12s %9s %12s %10s %11s %8s" % hdr)
    print("%-6s %6s %10s %12s %9s %12s %10s %11s %8s" % ("fp32", 32, 32 * 10, "exact", "%.2f%%" % (100 * ref_acc),
                                                       "100.00%", 9 * 4 + 320, "-", "-"))
    rows = {}
    for f in g.FMTS:
        st = {"inexact": 0, "ftz": 0, "mul": 0, "add": 0}
        pred = [g.infer(f, x, p, st)[0] for x in tex]
        acc = sum(c == y for c, y in zip(pred, tey)) / len(tey)
        same = sum(c == r for c, r in zip(pred, ref)) / len(ref)
        pbits = 9 * g.WBITS[f] + g.BIAS_BITS[f]
        moved = 9 * IN_BITS[f] + pbits
        fl = f in g.FLOAT_FMT
        rows[f] = (acc, same, pbits, moved)
        print("%-6s %6d %10d %12s %9s %12s %10d %11s %8s" % (
            f, g.WBITS[f], pbits, ACC_DESC[f], "%.2f%%" % (100 * acc),
            "%.2f%%" % (100 * same), moved, ("%.2f" % (st["inexact"] / len(tex))) if fl else "-",
            ("%.3f" % (st["ftz"] / len(tex))) if fl else "-"))
    print()
    print("param bits  = 9 weights x bits/w + bias (bin: 4-bit threshold; tern/int4/int8: bias at %s bits;"
          % "/".join(str(g.BIAS_BITS[f]) for f in ("tern", "int4", "int8")))
    print("              floats: bias in the weight format). fp32 row: 10 x 32 for comparison only.")
    print("bits moved  = input bits into the MAC (9 x 1 for bin, 9 x 4 otherwise) + parameter bits read once.")
    print("              (The stream pins carry 9 x 8 bits in and 2 x 8 bits out per inference for every format.)")
    print("inexact/inf = float roundings (products + sums) that changed a value, per inference, on the test set;")
    print("ftz/inf     = results flushed to +0 per inference (spec.md 4.4: only exact cancellations can do this).")
    q = p
    print()
    print("quantised parameters (weights in raster order | bias or threshold):")
    print("  %-5s %s | %+.6f" % ("fp32", " ".join("%+.4f" % v for v in q["fp32"]["w"]), q["fp32"]["b"]))
    print("  %-5s %s | T=%d" % ("bin", " ".join("%7s" % ("+1" if v else "-1") for v in q["bin"]["w"]), q["bin"]["b"]))
    for f in ("tern", "int4", "int8"):
        print("  %-5s %s | %+d   (scale %.6g)" % (f, " ".join("%+7d" % v for v in q[f]["w"]), q[f]["b"], float(q[f]["scale"])))
    for f in ("fp8", "fp16", "bf16"):
        wf = g.FLOAT_FMT[f][0]
        print("  %-5s %s | %+.6g" % (f, " ".join("%+.4f" % float(g.f_decode(wf, c)) for c in q[f]["w"]),
                                    float(g.f_decode(wf, q[f]["b"]))))
    return rows


def get(m, keys):
    for k in keys:
        if k in m and m[k] is not None:
            try:
                return float(m[k])
            except (TypeError, ValueError):
                pass
    return None


def hw_table():
    found = {}
    for f in g.FMTS:
        path = os.path.join(REPO, "designs", "prec_%s" % f, "output", "metrics.json")
        if os.path.exists(path):
            try:
                found[f] = json.load(open(path))
            except (OSError, ValueError) as e:
                print("report: cannot read %s: %s" % (os.path.relpath(path, REPO), e))
    print()
    if not found:
        print("hardware: no designs/prec_<fmt>/output/metrics.json yet (run the flows); table skipped.")
        return
    print("hardware (LibreLane metrics.json; setup WS = worst over corners; '-' = not available):")
    print("%-6s " % "format" + " ".join("%13s" % c for c, _, _ in METRICS))
    for f in g.FMTS:
        if f not in found:
            print("%-6s " % f + " ".join("%13s" % "-" for _ in METRICS) + "   (no metrics.json)")
            continue
        cells = []
        for _, keys, fmt in METRICS:
            v = get(found[f], keys)
            if v is not None and keys == ["power__total"]:
                v *= 1e3            # LibreLane reports watts
            cells.append("%13s" % (fmt % v if v is not None else "-"))
        print("%-6s " % f + " ".join(cells))
    print("vision_block (1-bit, 80x80 um die) for scale: see designs/vision_block/output/metrics.json")


def _read(f, *parts):
    try:
        with open(os.path.join(REPO, "designs", "prec_%s" % f, *parts)) as fh:
            return fh.read()
    except OSError:
        return None


def _synth(f):
    """(cell count, {cell type: count}, chip area um2) from output/reports/synth_stat.rpt, or None."""
    txt = _read(f, "output", "reports", "synth_stat.rpt")
    if txt is None:
        return None
    types, total, area = {}, None, None
    for ln in txt.splitlines():
        w = ln.split()
        if len(w) == 3 and w[2] == "cells":
            total = int(w[0])
        elif len(w) == 3 and w[2].startswith("sky130_fd_sc_hd__"):
            types[w[2][len("sky130_fd_sc_hd__"):].rsplit("_", 1)[0]] = int(w[0])
        elif ln.strip().startswith("Chip area for module"):
            area = float(w[-1])
    return total, types, area


def _latency(f):
    txt = _read(f, "README.md")
    if txt:
        for ln in txt.splitlines():
            i = ln.find("Latency")
            if i >= 0:
                w = ln[i:].replace("*", " ").split()
                if len(w) > 1 and w[1].isdigit():
                    return int(w[1])
    return None


def study_table():
    """Combined per-format table: model results (golden.py) + hardware files. Printed as two blocks of columns."""
    p = g.params()
    (_, _), (tex, tey) = g.dataset()
    ref = [g.ref_fp32(x, p)[0] for x in tex]
    rows = []
    for f in g.FMTS:
        pred = [g.infer(f, x, p, {"inexact": 0, "ftz": 0, "mul": 0, "add": 0})[0] for x in tex]
        acc = 100.0 * sum(c == y for c, y in zip(pred, tey)) / len(tey)
        same = 100.0 * sum(c == r for c, r in zip(pred, ref)) / len(ref)
        pbits = 9 * g.WBITS[f] + g.BIAS_BITS[f]
        m = {}
        mt = _read(f, "output", "metrics.json")
        if mt:
            m = json.loads(mt)
        res = {}
        rt = _read(f, "output", "resources.json")
        if rt:
            res = json.loads(rt)
        sy = _synth(f)
        rows.append(dict(f=f, acc=acc, same=same, pbits=pbits, moved=9 * IN_BITS[f] + pbits, m=m,
                         wall=res.get("wall_s_total"), sy=sy, lat=_latency(f)))

    def v(d, k, fmt, scale=1.0):
        x = d.get(k)
        return "-" if x is None else fmt % (x * scale)

    A = [("format", "%-6s"), ("acc %", "%7s"), ("=fp32 %", "%8s"), ("param b", "%8s"), ("moved b", "%8s"),
         ("accum", "%12s"), ("latency", "%8s"), ("flow s", "%7s")]
    print()
    print("Study table A (accuracy, memory, latency; test set of 2000 images)")
    print(" ".join(fm % c for c, fm in A))
    for r in rows:
        print(" ".join(fm % c for (_, fm), c in zip(A, [
            r["f"], "%.2f" % r["acc"], "%.2f" % r["same"], r["pbits"], r["moved"], ACC_DESC[r["f"]],
            "-" if r["lat"] is None else "%d" % r["lat"], "-" if r["wall"] is None else "%d" % r["wall"]])))
    B = [("format", "%-6s"), ("std cells", "%9s"), ("synth cells", "%11s"), ("flops", "%6s"), ("cell um2", "%9s"),
         ("die um", "%8s"), ("util %", "%7s"), ("wire um", "%8s"), ("vias", "%6s"),
         ("setup@ss", "%9s"), ("hold ns", "%8s"), ("power mW", "%9s")]
    print()
    print("Study table B (hardened layout; setup@ss = setup slack at max_ss_100C_1v60, ns; clock 25 ns)")
    print(" ".join(fm % c for c, fm in B))
    for r in rows:
        m = r["m"]
        bb = m.get("design__die__bbox")
        try:
            q = [float(x) for x in (bb.split() if isinstance(bb, str) else bb)]
            die = "%gx%g" % (q[2] - q[0], q[3] - q[1])
        except (AttributeError, TypeError, ValueError, IndexError):
            die = "-"
        vals = [r["f"], v(m, "design__instance__count__stdcell", "%d"),
                "-" if r["sy"] is None else "%d" % r["sy"][0],
                v(m, "design__instance__count__class:sequential_cell", "%d"),
                v(m, "design__instance__area__stdcell", "%.0f"), die,
                v(m, "design__instance__utilization", "%.1f", 100),
                v(m, "route__wirelength", "%.0f"), v(m, "route__vias", "%d"),
                v(m, "timing__setup__ws__corner:max_ss_100C_1v60", "%.3f"),
                v(m, "timing__hold__ws", "%.3f"), v(m, "power__total", "%.3f", 1e3)]
        print(" ".join(fm % c for (_, fm), c in zip(B, vals)))
    base = rows[0]["m"].get("design__instance__area__stdcell")
    if base:
        print()
        print("std-cell area relative to bin: " + "  ".join(
            "%s %.1fx" % (r["f"], r["m"]["design__instance__area__stdcell"] / base) for r in rows
            if r["m"].get("design__instance__area__stdcell")))
    groups = [("xor/xnor", ("xor2", "xor3", "xnor2", "xnor3")), ("mux", ("mux2", "mux4")),
              ("flop", ("dfxtp", "dfrtp")), ("inv/buf", ("inv", "buf", "clkbuf", "clkinv"))]
    print()
    print("Synthesised cell mix (output/reports/synth_stat.rpt, before placement; no full/half-adder cells are used,")
    print("yosys builds adders from xor/xnor + and-or-invert gates)")
    cols = [g_[0] for g_ in groups] + ["other gates", "total"]
    print("%-6s " % "format" + " ".join("%11s" % c for c in cols))
    for r in rows:
        if r["sy"] is None:
            print("%-6s " % r["f"] + " ".join("%11s" % "-" for _ in cols))
            continue
        tot, types, _ = r["sy"]
        cnt, used = [], 0
        for _, names in groups:
            n = sum(types.get(k, 0) for k in names)
            cnt.append(n)
            used += n
        other = sum(types.values()) - used
        print("%-6s " % r["f"] + " ".join("%11d" % c for c in cnt + [other, tot]))


if __name__ == "__main__":
    model_table()
    hw_table()
    study_table()
