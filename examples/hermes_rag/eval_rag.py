#!/usr/bin/env python3
"""Evaluate RAG on questions_rag.json: 10 answerable why/how/what-fixed questions + 2 unanswerable controls (the 12 "main"
Docs: examples/hermes_rag/README.md, docs/AGENT_MODELS.md
questions) and 5 HELD-OUT doc questions ("heldout": true, written and answer-verified before retrieval was tuned), reported separately.

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
# name -> run_rag_episode keyword arguments. The first three are the original configs (behaviour unchanged); the others are new.
CONFIGS = {"tools-only": dict(use_rag=False, grounding=False),
           "rag": dict(use_rag=True, grounding=False),
           "rag+grounding": dict(use_rag=True, grounding=True),
           "rag+v2search": dict(use_rag=True, grounding=False, search_mode="v2", prompt_version="v3"),
           "rag+router": dict(use_rag=True, grounding=False, search_mode="v2", prompt_version="v3", auto_retrieve=True),
           "rag+router+guardrail": dict(use_rag=True, grounding=False, search_mode="v2", prompt_version="v3", auto_retrieve=True, guardrail=True),
           "rag+router+guardrail+grounding": dict(use_rag=True, grounding=True, search_mode="v2", prompt_version="v3", auto_retrieve=True, guardrail=True)}
DEFAULT_CONFIGS = ["tools-only", "rag", "rag+v2search", "rag+router", "rag+router+guardrail", "rag+router+guardrail+grounding"]


def load_questions():
    return json.load(open(os.path.join(HERE, "questions_rag.json")))


def retrieval_eval(Q, mode="v1", heldout=False):
    """recall@k of the expected source files, and evidence@4: an `evidence` string occurs in the text of a top-4 hit from an
    expected file (is the answer sentence inside what the model sees). mode v1 = original search, v2 = improved search.
    heldout selects the 5 held-out questions (False: the original 10 answerable ones)."""
    fn = rag.search_docs_v2 if mode == "v2" else rag.search_docs
    ans = [q for q in Q if q["sources"] and bool(q.get("heldout")) == heldout]
    rows, rec, ev = [], {k: 0 for k in KS}, 0
    for q in ans:
        hits = fn(q["question"], max(KS))
        files = [h["file"] for h in hits]
        rank = next((i + 1 for i, f in enumerate(files) if f in q["sources"]), None)
        for k in KS:
            rec[k] += bool(rank and rank <= k)
        has_ev = any(e.lower() in h["text"].lower() for h in hits[:4] if h["file"] in q["sources"] for e in q.get("evidence", []))
        ev += has_ev
        rows.append({"id": q["id"], "rank_of_expected_file": rank, "evidence_in_top4_text": has_ev, "top_files": files[:4]})
    return {"mode": mode, "questions": len(ans), "recall_at_k": {f"@{k}": f"{rec[k]}/{len(ans)}" for k in KS},
            "recall_at_k_value": {f"@{k}": round(rec[k] / len(ans), 3) for k in KS},
            "evidence_in_top4_text": f"{ev}/{len(ans)}", "evidence_value": round(ev / len(ans), 3), "rows": rows}


def _stats(rows):
    ans = [r for r in rows if not r["id"].startswith("u")]
    ctl = [r for r in rows if r["id"].startswith("u")]
    return {"passed": sum(r["pass"] for r in rows), "total": len(rows),
            "answerable_passed": sum(r["pass"] for r in ans), "answerable_total": len(ans),
            "controls_passed": sum(r["pass"] for r in ctl), "controls_total": len(ctl),
            "context_present": sum(r["context_present"] for r in rows),
            "auto_retrieved": sum(r["auto_searched"] for r in rows), "model_called_search": sum(r["model_searched"] for r in rows),
            "retrieved_expected_file": sum(r["retrieved_expected_file"] for r in ans),
            "cited_expected_file": sum(r["cited_expected_file"] for r in ans),
            "guardrail_rejections": sum(r["guardrail_rejections"] for r in rows),
            "answers_with_grounding_problems": sum(bool(r["grounding_problems"]) for r in rows),
            "median_latency_s": round(statistics.median(r["seconds"] for r in rows), 2) if rows else None,
            "failed": [r["id"] for r in rows if not r["pass"]]}


def e2e_eval(Q, only, old_prompt="v2"):
    """Runs every question (the 12 original ones and the held-out ones) once per config; stats are split main / held-out."""
    import rag_agent, harness
    from run_eval import score
    out = {}
    for name in only:
        cfg = dict(CONFIGS[name])
        cfg.setdefault("prompt_version", old_prompt)
        tracer = harness.Tracer("rag_" + name.replace("+", "_"))
        rows = []
        for q in Q:
            try:
                r = rag_agent.run_rag_episode(q["question"], tracer=tracer, **cfg)
            except Exception as e:  # noqa: BLE001
                r = {"answer": f"ERROR {e}", "route": "?", "tool_calls": [], "searches": [], "citations": [], "seconds": 0, "revisions": 0,
                     "guardrail_rejections": 0, "final_grounding_problems": None}
            ok = bool(score(q["check"], r["answer"]))
            cited_expected = bool(q["sources"]) and any(any(s == c or s.endswith("/" + c) for s in q["sources"]) for c in r["citations"])
            retrieved_expected = bool(q["sources"]) and any(f in q["sources"] for s in r["searches"] for f in s["files"])
            calls = r["tool_calls"]
            row = {"id": q["id"], "heldout": bool(q.get("heldout")), "pass": ok, "seconds": r["seconds"], "route": r.get("route"),
                   "calls": [c["name"] for c in calls],
                   "auto_searched": any(c["name"] == "search_docs" and c.get("auto") for c in calls),
                   "model_searched": any(c["name"] == "search_docs" and not c.get("auto") for c in calls),
                   "context_present": bool(r["searches"]), "retrieved_expected_file": retrieved_expected,
                   "cited_expected_file": cited_expected, "revisions": r["revisions"], "guardrail_rejections": r.get("guardrail_rejections", 0),
                   "grounding_problems": r["final_grounding_problems"], "answer": r["answer"][:700]}
            rows.append(row)
            print(f"[{name}] {q['id']} {'PASS' if ok else 'FAIL'} {r['seconds']:5.1f}s {row['calls']} auto={row['auto_searched']} cited_expected={cited_expected}", flush=True)
        main_rows = [r for r in rows if not r["heldout"]]
        held = [r for r in rows if r["heldout"]]
        out[name] = {"main": _stats(main_rows), "heldout": _stats(held), "trace": os.path.relpath(tracer.path, ROOT), "rows": rows}
    return out


def router_eval(Q):
    import router
    rows = [{"id": q["id"], "expected": q["route"], "got": router.classify(q["question"])} for q in Q]
    ext = json.load(open(os.path.join(ROOT, "tools", "eval", "questions.json")))   # the 15 tool questions (numbers): independent of the RAG set
    ext_rows = [{"id": q["id"], "expected": "unknown" if q["id"] in ("q14", "q15") else "metric", "got": router.classify(q["question"])} for q in ext]
    acc = lambda rs: f"{sum(r['expected'] == r['got'] for r in rs)}/{len(rs)}"
    return {"rag_questions": acc(rows), "rag_misrouted": [r for r in rows if r["expected"] != r["got"]],
            "tool_questions_tools_eval": acc(ext_rows), "tool_questions_misrouted": [r for r in ext_rows if r["expected"] != r["got"]]}


def make_summary(report_path, handcheck_path=None):
    """Committed summary (repo-relative paths only). The previous summary is kept as results_summary_v1.json."""
    rep = json.load(open(os.path.join(ROOT, report_path)))
    hc = json.load(open(os.path.join(ROOT, handcheck_path))) if handcheck_path else {}
    s = {"source": report_path, "handcheck_source": handcheck_path, "model": rep.get("model"), "options": rep.get("options"),
         "index": rep["index"], "questions": {"main": 12, "heldout": 5},
         "history": "results_summary_v1.json is the first measurement (rag/tools-only/rag+grounding, v1 retrieval, 12 questions, run build/agent/rag_eval_20261006_144926.json)",
         "retrieval": rep["retrieval"], "router": rep["router"], "end_to_end": {}}
    for n, c in rep["end_to_end"].items():
        h = hc.get(n, {})
        entry = {"main": c["main"], "heldout": c["heldout"], "trace": c["trace"]}
        for part, ids in (("main", [r["id"] for r in c["rows"] if not r["heldout"]]), ("heldout", [r["id"] for r in c["rows"] if r["heldout"]])):
            passed = [r["id"] for r in c["rows"] if r["id"] in ids and r["pass"]]
            if h:
                good = [i for i in passed if h.get(i, {}).get("ok")]
                entry[part]["hand_checked_passed"] = len(good)
                entry[part]["keyword_accidents"] = [i for i in passed if i not in good]
        if h:
            entry["hand_check_notes"] = {i: v["note"] for i, v in h.items()}
        entry["failed_answers"] = {r["id"]: r["answer"][:200] for r in c["rows"] if not r["pass"]}
        s["end_to_end"][n] = entry
    sp = os.path.join(HERE, "results_summary.json")
    json.dump(s, open(sp, "w"), indent=1)
    print("->", os.path.relpath(sp, ROOT))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--retrieval-only", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated config names (default: %s)" % ",".join(DEFAULT_CONFIGS))
    ap.add_argument("--prompt", choices=["v1", "v2"], default="v2", help="prompt of the ORIGINAL configs (tools-only, rag, rag+grounding)")
    ap.add_argument("--make-summary", metavar="REPORT", help="write examples/hermes_rag/results_summary.json from a build/agent report")
    ap.add_argument("--handcheck", metavar="FILE", help="with --make-summary: hand-check json {config: {id: {ok, note}}}")
    ap.add_argument("--model", default=None, help="Ollama model tag (default: env HERMES_MODEL, else hermes3:8b)")
    a = ap.parse_args()
    if a.make_summary:
        return make_summary(a.make_summary, a.handcheck)
    Q = load_questions()
    idx = rag.build_index()
    report = {"index": {"chunks": len(idx["chunks"]), "files": len(idx["files"])},
              "retrieval": {m + ("_heldout" if h else ""): retrieval_eval(Q, m, h) for m in ("v1", "v2") for h in (False, True)},
              "router": router_eval(Q)}
    for k, v in report["retrieval"].items():
        print(f"retrieval {k:12} recall {v['recall_at_k']}  evidence_in_top4_text {v['evidence_in_top4_text']}")
    print("router:", {k: v for k, v in report["router"].items() if "misrouted" not in k})
    if not a.retrieval_only:
        import rag_agent
        if a.model:
            rag_agent.harness.hermes_agent.MODEL = a.model
        report["model"] = rag_agent.harness.hermes_agent.MODEL
        report["options"] = rag_agent.harness.OPTS
        t0 = time.time()
        report["end_to_end"] = e2e_eval(Q, a.only.split(",") if a.only else DEFAULT_CONFIGS, a.prompt)
        report["total_seconds"] = round(time.time() - t0)
        print(f"\n{'config':32} {'main':>6} {'held':>5} {'ctx':>4} {'auto':>5} {'model':>6} {'cited':>6} {'med s':>6}  failed")
        for n, c in report["end_to_end"].items():
            m, h = c["main"], c["heldout"]
            print(f"{n:32} {m['passed']:>3}/{m['total']:<2} {h['passed']:>2}/{h['total']:<2} {m['context_present'] + h['context_present']:>4} "
                  f"{m['auto_retrieved'] + h['auto_retrieved']:>5} {m['model_called_search'] + h['model_called_search']:>6} "
                  f"{m['cited_expected_file'] + h['cited_expected_file']:>6} {m['median_latency_s']:>6}  {','.join(m['failed'] + h['failed'])}")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(ROOT, "build", "agent", f"rag_eval_{stamp}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(report, open(path, "w"), indent=1)
    print("->", os.path.relpath(path, ROOT))


if __name__ == "__main__":
    main()
