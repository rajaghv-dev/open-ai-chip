#!/usr/bin/env python3
"""Run the 15 tools/eval questions under several harness configurations and compare.

  build/agent/venv/bin/python examples/hermes_harness/eval_harness.py [--only baseline,all] [--gate 13]

Scoring is tools/eval/run_eval.score (imported, not copied). Writes build/agent/harness_eval_<timestamp>.json.
--gate N: exit 1 if the all-features score is below N (use as a CI regression gate).
"""
import argparse, json, os, statistics, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "eval"))
import harness                      # noqa: E402
from run_eval import score          # noqa: E402  (the repo's scorer)

CONFIGS = {
    "baseline":      harness.Config(),
    "+guardrails":   harness.Config(guardrails=True),
    "+grounding":    harness.Config(grounding=True),
    "+pick_extreme": harness.Config(pick_extreme=True),
    "all":           harness.Config(guardrails=True, grounding=True, pick_extreme=True),
    "all+plan":      harness.Config(guardrails=True, grounding=True, pick_extreme=True, plan=True),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated config names")
    ap.add_argument("--gate", type=int, default=None, help="exit 1 if the 'all' score < N")
    a = ap.parse_args()
    Q = json.load(open(os.path.join(ROOT, "tools", "eval", "questions.json")))
    names = a.only.split(",") if a.only else list(CONFIGS)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    report = {"model": harness.hermes_agent.MODEL, "options": harness.OPTS, "configs": {}}
    t_start = time.time()
    for name in names:
        tracer = harness.Tracer(name.replace("+", "plus_"))
        rows = []
        for q in Q:
            try:
                r = harness.run_episode(q["question"], CONFIGS[name], tracer)
            except Exception as e:
                r = {"answer": f"ERROR {e}", "tool_calls": [], "seconds": 0, "retries": 0, "steps": 0, "grounding": None}
            r["pass"] = bool(score(q["check"], r["answer"]))
            rows.append({"id": q["id"], "pass": r["pass"], "seconds": r["seconds"], "calls": [c["name"] for c in r["tool_calls"]],
                         "retries": r["retries"], "answer": r["answer"][:300], "expected": q["expected"]})
            print(f"[{name}] {q['id']} {'PASS' if r['pass'] else 'FAIL'} {r['seconds']:5.1f}s {rows[-1]['calls']}", flush=True)
        report["configs"][name] = {
            "passed": sum(r["pass"] for r in rows), "total": len(rows),
            "median_latency_s": round(statistics.median(r["seconds"] for r in rows), 2),
            "tool_calls_per_q": round(sum(len(r["calls"]) for r in rows) / len(rows), 2),
            "retries_total": sum(r["retries"] for r in rows),
            "failed": [r["id"] for r in rows if not r["pass"]], "trace": os.path.relpath(tracer.path, ROOT), "rows": rows}
    report["total_seconds"] = round(time.time() - t_start)
    out = os.path.join(ROOT, "build", "agent", f"harness_eval_{stamp}.json")
    json.dump(report, open(out, "w"), indent=1)
    print(f"\n{'config':14} {'score':>7} {'median s':>9} {'calls/q':>8} {'retries':>8}  failed")
    for n, c in report["configs"].items():
        print(f"{n:14} {c['passed']:>3}/{c['total']:<3} {c['median_latency_s']:>9} {c['tool_calls_per_q']:>8} {c['retries_total']:>8}  {','.join(c['failed'])}")
    print("->", out)
    if a.gate is not None and "all" in report["configs"] and report["configs"]["all"]["passed"] < a.gate:
        print(f"GATE FAILED: all-features score {report['configs']['all']['passed']} < {a.gate}")
        sys.exit(1)


if __name__ == "__main__":
    main()
