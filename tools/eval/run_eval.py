#!/usr/bin/env python3
"""Run tools/hermes_agent.py on tools/eval/questions.json, score automatically, write build/agent/eval_<ts>.json.
  build/agent/venv/bin/python tools/eval/run_eval.py [--mode native|prompt] [--only q01,q02] [--tag name]
Regenerate questions first if repo results changed:  python3 tools/eval/ground_truth.py"""
import argparse, json, os, re, statistics, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hermes_agent  # noqa: E402

UNKNOWN_RE = re.compile(r"\bunknown\b|cannot (be )?(answer|determin|find)|can't|not available|no tool|unable|do not know|don't know|not provide", re.I)


def numbers_in(text):
    return [float(x) for x in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", text.replace(",", ""))]


def strip_source(ans):
    """Drop the 'Source: ...' citation tail so tool names/args cited there cannot satisfy a check."""
    return re.split(r"\n?\s*\(?Source", ans, maxsplit=1, flags=re.I)[0]


def score(check, ans):
    if check["type"] != "unknown":
        ans = strip_source(ans)
    low = ans.lower()
    t = check["type"]
    if t == "unknown":
        return bool(UNKNOWN_RE.search(ans))
    if t == "yesno":
        yes = re.search(r"\b(yes|clean|pass(es|ed)?)\b", low) and not re.search(r"\b(not clean|no,|not drc|violations? found)\b", low)
        no = re.search(r"\b(no|not clean|fail)", low)
        return bool(yes) if check["expect"] else bool(no) and not yes
    ok = True
    if t == "numbers":
        have = numbers_in(ans)
        for v in check["values"]:
            tol = max(abs(v) * check.get("tol_rel", 0), check.get("tol_abs", 0)) + 1e-12
            ok &= any(abs(h - v) <= tol for h in have)
    for w in check.get("all_words", []):
        ok &= w.lower() in low
    if check.get("any_words"):
        ok &= any(w.lower() in low for w in check["any_words"])
    return bool(ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="prompt")
    ap.add_argument("--model", default=hermes_agent.MODEL)
    ap.add_argument("--only", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--system-file", default="", help="override system prompt from a file")
    a = ap.parse_args()
    Q = json.load(open(os.path.join(HERE, "questions.json")))
    if a.only:
        Q = [q for q in Q if q["id"] in a.only.split(",")]
    system = open(a.system_file).read() if a.system_file else None
    rows = []
    for q in Q:
        try:
            r = hermes_agent.ask(q["question"], a.mode, a.model, system=system, verbose=False)
        except Exception as e:
            r = {"answer": f"ERROR {e}", "tool_calls": [], "seconds": 0, "eval_tokens": 0, "eval_seconds": 0}
        ok = score(q["check"], r["answer"])
        rows.append({**q, **r, "pass": ok})
        print(f"{q['id']} {'PASS' if ok else 'FAIL'} {r['seconds']:6.1f}s tools={[c['name'] for c in r['tool_calls']]}\n    exp={q['expected']}\n    got={r['answer'][:200]!r}", flush=True)
    npass = sum(r["pass"] for r in rows)
    lat = [r["seconds"] for r in rows]
    tok = sum(r["eval_tokens"] for r in rows); ts = sum(r["eval_seconds"] for r in rows)
    summary = {"model": a.model, "mode": a.mode, "tag": a.tag, "passed": npass, "total": len(rows),
               "median_latency_s": round(statistics.median(lat), 1) if lat else 0,
               "tokens_per_s": round(tok / ts, 1) if ts else 0}
    ts_s = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(ROOT, "build", "agent", f"eval_{ts_s}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"summary": summary, "results": rows}, open(out, "w"), indent=1)
    print("\n%-5s %-13s %-5s %7s  %s" % ("id", "category", "pass", "sec", "tools"))
    for r in rows:
        print("%-5s %-13s %-5s %7.1f  %s" % (r["id"], r["category"], "PASS" if r["pass"] else "FAIL", r["seconds"], ",".join(c["name"] for c in r["tool_calls"])))
    print(json.dumps(summary), "->", out)


if __name__ == "__main__":
    main()
