#!/usr/bin/env python3
"""Compute eval ground truth straight from repo files (metrics.json, LEF, config.json, reports, precheck TSV).
Independent of tools/eda_tools.py. Writes tools/eval/questions.json.  Never hand-type numbers here.
Docs: tools/README.md, docs/HERMES_AGENT.md"""
import glob, json, os, re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
D = lambda *p: os.path.join(ROOT, "designs", *p)


def metrics(d):
    return json.load(open(D(d, "output", "metrics.json")))


def hardened():
    return sorted(os.path.basename(os.path.dirname(os.path.dirname(p))) for p in glob.glob(D("*", "output", "metrics.json")))


def stdcells(d): return metrics(d)["design__instance__count__stdcell"]
def flops(d): return metrics(d).get("design__instance__count__class:sequential_cell")
def stdarea(d): return metrics(d)["design__instance__area__stdcell"]


# Worst setup slack = the minimum over every STA corner key 'timing__setup__ws__corner:<corner>' in metrics.json.
def worst_setup(d):
    m = metrics(d)
    cs = {k.split("corner:")[1]: v for k, v in m.items() if k.startswith("timing__setup__ws__corner:")}
    c = min(cs, key=cs.get)
    return cs[c], c


def die(d):
    x0, y0, x1, y1 = map(float, metrics(d)["design__die__bbox"].split())
    return x1 - x0, y1 - y0


# Counts bus bits (PIN name[n]) in the macro LEF; independent of eda_tools.find_pins so the eval is not self-grading.
def lef_pins(d, prefix):
    top = d
    txt = open(D(d, "output", top + ".lef")).read()
    return len(re.findall(r"^\s*PIN %s\[\d+\]" % re.escape(prefix), txt, re.M))


def wrapper_macros(w):
    c = json.load(open(D(w, "config.json")))
    out = []
    for name, spec in c.get("MACROS", {}).items():
        out += [(name, inst) for inst in spec.get("instances", {})]
    return out


# Clean = Magic DRC count 0 AND netgen LVS 'Circuits match uniquely'; read from the committed reports, not re-run.
def signoff_clean(d):
    r = D(d, "output", "reports")
    drc = open(os.path.join(r, "drc_magic.rpt")).read()
    magic_ok = re.search(r"COUNT:\s*0\b", drc) is not None
    lvs_ok = "Circuits match uniquely" in open(os.path.join(r, "lvs_netgen.rpt")).read()
    return magic_ok and lvs_ok


def precheck_counts():
    runs = sorted(glob.glob(os.path.join(ROOT, "build", "precheck", "results_2*")))
    # latest full-run summary with 14 checks, falls back to latest
    best = None
    for r in runs:
        f = os.path.join(r, "summary.tsv")
        if os.path.exists(f):
            rows = [l.split("\t") for l in open(f).read().splitlines() if l.strip()]
            if len(rows) >= 14:
                best = rows
    p = sum(1 for r in best if r[1] == "PASS")
    return p, len(best)


def build():
    Q = []
    add = lambda id, cat, q, check, expected_text: Q.append(
        {"id": id, "category": cat, "question": q, "check": check, "expected": expected_text})
    n = lambda vals, tol=0.01, extra=None: {"type": "numbers", "values": vals, "tol_rel": tol, **({"all_words": extra} if extra else {})}

    v = stdcells("vision_block")
    add("q01", "lookup", "How many standard cells does vision_block have?", n([v]), str(v))
    ws, corner = worst_setup("prec_fp16")
    short = re.search(r"(ss|tt|ff)_\w+", corner).group(0)
    add("q02", "lookup", "What is the worst setup slack of prec_fp16 in ns, and in which timing corner?",
        {"type": "numbers", "values": [round(ws, 4)], "tol_rel": 0.02, "tol_abs": 0.002, "any_words": [corner, short.split("_")[0] + "_" + short.split("_")[1]]},
        f"{ws:.4f} ns at {corner}")
    hs = [d for d in hardened() if flops(d) is not None]
    top = max(hs, key=flops)
    add("q03", "comparison", "Which hardened design has the most flip-flops (sequential cells)?",
        {"type": "words", "any_words": [top]}, f"{top} ({flops(top)})")
    sd = [d for d in hardened() if stdcells(d) > 0]
    small = min(sd, key=stdarea)
    add("q04", "comparison", "Among the designs that contain standard cells, which has the smallest standard-cell area?",
        {"type": "words", "any_words": [small]}, f"{small} ({stdarea(small)} um2)")
    precs = [d for d in hardened() if d.startswith("prec_")]
    pm = max(precs, key=stdcells)
    add("q05", "comparison", "Which number-format variant (prec_*) has the most standard cells?",
        {"type": "words", "any_words": [pm]}, f"{pm} ({stdcells(pm)})")
    w, h = die("tiny_ai_core")
    add("q06", "layout", "What is the die size of tiny_ai_core in micrometres?", n([w, h], 0.001), f"{w:g} x {h:g} um")
    ms = wrapper_macros("user_project_wrapper")
    names = sorted({m[0] for m in ms})
    add("q07", "layout", "How many macro instances does user_project_wrapper contain, and which macro is it?",
        {"type": "numbers", "values": [len(ms)], "tol_rel": 0, "all_words": names}, f"{len(ms)}: {', '.join(names)}")
    wp = lef_pins("tiny_ai_core", "wbs_dat_i")
    add("q08", "layout", "How many wbs_dat_i pins does tiny_ai_core have?", n([wp], 0), str(wp))
    ok = signoff_clean("image_text_match")
    add("q09", "signoff", "Is image_text_match DRC clean and LVS clean?",
        {"type": "yesno", "expect": ok}, "yes" if ok else "no")
    sl = metrics("soc_image_text_match")["design__max_slew_violation__count"]
    add("q10", "signoff", "How many max-slew violations does soc_image_text_match report in total?", n([sl], 0), str(sl))
    p, t = precheck_counts()
    add("q11", "precheck", "How many precheck checks pass in the latest precheck run?", n([p], 0), f"{p} of {t}")
    a, b = stdcells("prec_fp16"), stdcells("prec_int8")
    add("q12", "multi-step", "How many more standard cells does prec_fp16 have than prec_int8?", n([a - b], 0), str(a - b))
    add("q13", "multi-step", "What is the ratio of standard cells of prec_fp16 to prec_int8 (give 2 decimals)?",
        n([a / b], 0.01), f"{a/b:.3f}")
    add("q14", "unanswerable", "What is the selling price per chip of the tiny_ai_core design?",
        {"type": "unknown"}, "unknown")
    add("q15", "unanswerable", "What was the measured silicon yield of the fabricated chips?",
        {"type": "unknown"}, "unknown")
    return Q


if __name__ == "__main__":
    Q = build()
    out = os.path.join(os.path.dirname(__file__), "questions.json")
    json.dump(Q, open(out, "w"), indent=1)
    for q in Q:
        print(q["id"], q["expected"], "|", q["question"])
