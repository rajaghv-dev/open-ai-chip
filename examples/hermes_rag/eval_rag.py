#!/usr/bin/env python3
"""Evaluate RAG on questions_rag.json (10 answerable why/how/what-fixed questions + 2 unanswerable controls).

  python3 examples/hermes_rag/eval_rag.py --retrieval-only            # (a) recall@k, deterministic, no LLM
  build/agent/venv/bin/python examples/hermes_rag/eval_rag.py         # (a) + (b) end-to-end Hermes, 3 configs

(a) For each answerable question, the question text is the query; recall@k = the share of questions with an expected
    source file among the top-k hits (rank is by file; the first hit of an expected file counts).
(b) tools-only (no search_docs), rag (search_docs, no check), rag+grounding (search_docs + citation/number check with
    one revision). Scoring: tools/eval/run_eval.score keyword checks on the answer (the "Source" tail is stripped);
    unanswerable controls pass when the answer says unknown. Extra columns: did the agent call search_docs, did the
    answer cite an expected source file, did the final answer pass the RAG grounding check.
Writes build/agent/rag_eval_<ts>.json and, with --summary, examples/hermes_rag/results_summary.json (repo-relative paths).
"""
import argparse, json, os, statistics, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "eval"))
import rag  # noqa: E402

KS = (1, 2, 4, 8)
CONFIGS = {"tools-only": dict(use_rag=False, grounding=False),
           "rag": dict(use_rag=True, grounding=False),
           "rag+grounding": dict(use_rag=True, grounding=True)}


def load_questions():
    return json.load(open(os.path.join(HERE, "questions_rag.json")))


def retrieval_eval(Q):
    ans = [q for q in Q if q["sources"]]
    rows, rec = [], {k: 0 for k in KS}
    for q in ans:
        hits = rag.search_docs(q["question"], max(KS))
        files = [h["file"] for h in hits]
        rank = next((i + 1 for i, f in enumerate(files) if f in q["sources"]), None)
        for k in KS:
            rec[k] += bool(rank and rank <= k)
        rows.append({"id": q["id"], "rank_of_expected_file": rank, "top_files": files[:4]})
    return {"questions": len(ans), "recall_at_k": {f"@{k}": f"{rec[k]}/{len(ans)}" for k in KS},
            "recall_at_k_value": {f"@{k}": round(rec[k] / len(ans), 3) for k in KS}, "rows": rows}


def e2e_eval(Q, only):
    import rag_agent, harness
    from run_eval import score
    out = {}
    for name in only:
        cfg = CONFIGS[name]
        tracer = harness.Tracer("rag_" + name.replace("+", "_"))
        rows = []
        for q in Q:
            try:
                r = rag_agent.run_rag_episode(q["question"], tracer=tracer, **cfg)
            except Exception as e:  # noqa: BLE001
                r = {"answer": f"ERROR {e}", "tool_calls": [], "searches": [], "citations": [], "seconds": 0, "revisions": 0,
                     "final_grounding_problems": None}
            ok = bool(score(q["check"], r["answer"]))
            cited_expected = bool(q["sources"]) and any(any(s == c or s.endswith("/" + c) for s in q["sources"]) for c in r["citations"])
            retrieved_expected = bool(q["sources"]) and any(f in q["sources"] for s in r["searches"] for f in s["files"])
            row = {"id": q["id"], "pass": ok, "seconds": r["seconds"], "calls": [c["name"] for c in r["tool_calls"]],
                   "searched": any(c["name"] == "search_docs" for c in r["tool_calls"]), "retrieved_expected_file": retrieved_expected,
                   "cited_expected_file": cited_expected, "revisions": r["revisions"],
                   "grounding_problems": r["final_grounding_problems"], "answer": r["answer"][:500]}
            rows.append(row)
            print(f"[{name}] {q['id']} {'PASS' if ok else 'FAIL'} {r['seconds']:5.1f}s {row['calls']} cited_expected={cited_expected}", flush=True)
        ans = [r for r in rows if not r["id"].startswith("u")]
        ctl = [r for r in rows if r["id"].startswith("u")]
        out[name] = {"passed": sum(r["pass"] for r in rows), "total": len(rows),
                     "answerable_passed": sum(r["pass"] for r in ans), "answerable_total": len(ans),
                     "controls_passed": sum(r["pass"] for r in ctl), "controls_total": len(ctl),
                     "searched": sum(r["searched"] for r in rows), "retrieved_expected_file": sum(r["retrieved_expected_file"] for r in ans),
                     "cited_expected_file": sum(r["cited_expected_file"] for r in ans),
                     "answers_with_grounding_problems": sum(bool(r["grounding_problems"]) for r in rows),
                     "median_latency_s": round(statistics.median(r["seconds"] for r in rows), 2),
                     "failed": [r["id"] for r in rows if not r["pass"]],
                     "trace": os.path.relpath(tracer.path, ROOT), "rows": rows}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--retrieval-only", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated config names")
    ap.add_argument("--prompt", choices=["v1", "v2"], default="v2", help="RAG prompt version in rag_agent.py")
    ap.add_argument("--summary", action="store_true", help="also write examples/hermes_rag/results_summary.json")
    a = ap.parse_args()
    Q = load_questions()
    idx = rag.build_index()
    report = {"index": {"chunks": len(idx["chunks"]), "files": len(idx["files"])}, "retrieval": retrieval_eval(Q)}
    print("retrieval recall:", report["retrieval"]["recall_at_k"])
    if not a.retrieval_only:
        import rag_agent
        rag_agent.PROMPT["version"] = a.prompt
        report["prompt"] = a.prompt
        harness = rag_agent.harness
        report["model"] = harness.hermes_agent.MODEL
        report["options"] = harness.OPTS
        t0 = time.time()
        report["end_to_end"] = e2e_eval(Q, a.only.split(",") if a.only else list(CONFIGS))
        report["total_seconds"] = round(time.time() - t0)
        print(f"\n{'config':15} {'score':>6} {'answerable':>11} {'controls':>9} {'searched':>9} {'cited src':>10} {'median s':>9}  failed")
        for n, c in report["end_to_end"].items():
            print(f"{n:15} {c['passed']:>3}/{c['total']:<2} {c['answerable_passed']:>6}/{c['answerable_total']:<4} {c['controls_passed']:>4}/{c['controls_total']:<4}"
                  f" {c['searched']:>9} {c['cited_expected_file']:>10} {c['median_latency_s']:>9}  {','.join(c['failed'])}")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(ROOT, "build", "agent", f"rag_eval_{stamp}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(report, open(path, "w"), indent=1)
    print("->", os.path.relpath(path, ROOT))
    if a.summary:
        s = {"source": os.path.relpath(path, ROOT), "prompt": report.get("prompt"), "model": report.get("model"), "options": report.get("options"),
             "index": report["index"], "questions": len(Q),
             "retrieval_recall_at_k": report["retrieval"]["recall_at_k"],
             "retrieval_rank_of_expected_file": {r["id"]: r["rank_of_expected_file"] for r in report["retrieval"]["rows"]}}
        if "end_to_end" in report:
            s["end_to_end"] = {n: {k: v for k, v in c.items() if k not in ("rows", "trace")} for n, c in report["end_to_end"].items()}
            s["end_to_end_failed_answers"] = {n: {r["id"]: r["answer"][:160] for r in c["rows"] if not r["pass"]} for n, c in report["end_to_end"].items()}
        sp = os.path.join(HERE, "results_summary.json" if a.prompt == "v2" else f"results_summary_prompt_{a.prompt}.json")
        json.dump(s, open(sp, "w"), indent=1)
        print("->", os.path.relpath(sp, ROOT))


if __name__ == "__main__":
    main()
