#!/usr/bin/env python3
"""Retrieval-only evaluation of the hybrid mini RAG (no chat model): recall@1/@4 of the expected source files on questions_rag.json
(10 main answerable + 5 held-out questions) for BM25 v2 (rag.py, its own markdown corpus), BM25 / dense / hybrid on the wider hybrid corpus
(rag_tools.py), hybrid without the automatic design filter; plus index build time and per-query time.

  build/agent/venv/bin/python examples/hermes_rag/eval_hybrid.py [--cold] [--write]

--cold rebuilds the index from scratch (re-chunks, re-embeds everything) and times it; without it the cache is used (build time = cached
load). --write stores examples/hermes_rag/hybrid_results.json (repo-relative paths only). Needs Ollama on 127.0.0.1:11434 with the embedding
model (default qwen3-embedding:0.6b, env RAG_EMBED_MODEL) for the dense and hybrid rows; without it those rows are reported as unavailable.
Docs: examples/hermes_rag/README.md ("Hybrid mini RAG for Hermes Agent")
"""
import argparse, datetime, json, os, statistics, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "examples", "hermes_desktop", "tool_server"))
import rag  # noqa: E402
import rag_tools as rt  # noqa: E402

KS = (1, 4)


def methods():
    def old(q):
        return rag.search_docs_v2(q, 8)
    def mk(mode, auto):
        return lambda q: rt.search(q, 8, mode=mode, auto_design=auto)["hits"]
    return [("bm25_v2 (rag.py, markdown corpus)", old), ("bm25 (hybrid corpus)", mk("bm25", False)), ("dense", mk("dense", False)),
            ("hybrid RRF", mk("hybrid", False)), ("hybrid RRF + design filter", mk("hybrid", True))]


def score(fn, qs):
    rec, ev, times, rows = {k: 0 for k in KS}, 0, [], []
    for q in qs:
        t = time.time()
        hits = fn(q["question"])
        times.append(time.time() - t)
        files = [h["file"] for h in hits]
        rank = next((i + 1 for i, f in enumerate(files) if f in q["sources"]), None)
        for k in KS:
            rec[k] += bool(rank and rank <= k)
        ev += any(e.lower() in h["text"].lower() for h in hits[:4] if h["file"] in q["sources"] for e in q.get("evidence", []))
        rows.append({"id": q["id"], "rank_of_expected_file": rank})
    n = len(qs)
    return {"n": n, **{f"recall@{k}": f"{rec[k]}/{n}" for k in KS}, **{f"recall@{k}_value": round(rec[k] / n, 3) for k in KS},
            "evidence@4": f"{ev}/{n}", "median_query_ms": round(1000 * statistics.median(times), 1), "ranks": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cold", action="store_true")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    Q = json.load(open(os.path.join(HERE, "questions_rag.json")))
    main_q = [q for q in Q if q["sources"] and not q.get("heldout")]
    held_q = [q for q in Q if q["sources"] and q.get("heldout")]
    t = time.time()
    st = rt.build_index(rebuild=a.cold)
    st["wall_seconds"] = round(time.time() - t, 2)
    print("index:", json.dumps(st), flush=True)
    rt.search("warm up", 1, mode="bm25")
    out = {"date": datetime.date.today().isoformat(), "embed_model": rt.EMBED_MODEL, "index": {**st, "cold_build": a.cold},
           "questions": {"main": len(main_q), "heldout": len(held_q)}, "methods": {}}
    for name, fn in methods():
        try:
            out["methods"][name] = {"main": score(fn, main_q), "heldout": score(fn, held_q)}
            m, h = out["methods"][name]["main"], out["methods"][name]["heldout"]
            print(f"{name:38s} main @1 {m['recall@1']} @4 {m['recall@4']} | held-out @1 {h['recall@1']} @4 {h['recall@4']} | {m['median_query_ms']} ms/query", flush=True)
        except Exception as e:  # noqa: BLE001
            out["methods"][name] = {"unavailable": f"{type(e).__name__}: {str(e)[:100]}"}
            print(name, "unavailable:", e)
    # answer level (rag_answer, hybrid + design filter): is a quoted sentence from an expected source file; do the 2 unanswerable controls say not found
    ans = {"answerable": 0, "found": 0, "quote_from_expected_file": 0, "controls": 0, "controls_found": 0, "median_seconds": None}
    secs = []
    for q in Q:
        r = rt.answer(q["question"])
        secs.append(r["seconds"])
        if q["sources"]:
            ans["answerable"] += 1
            ans["found"] += r["found"]
            ans["quote_from_expected_file"] += any(x["file"] in q["sources"] for x in r["quotes"])
        else:
            ans["controls"] += 1
            ans["controls_found"] += r["found"]
    ans["median_seconds"] = round(statistics.median(secs), 3)
    out["rag_answer"] = ans
    print("rag_answer:", json.dumps(ans), flush=True)
    if a.write:
        if not a.cold and os.path.isfile(os.path.join(HERE, "hybrid_results.json")):      # keep the committed cold-build timing
            prev = json.load(open(os.path.join(HERE, "hybrid_results.json")))
            if prev.get("index", {}).get("cold_build"):
                out["index"] = prev["index"]
        json.dump(out, open(os.path.join(HERE, "hybrid_results.json"), "w"), indent=1)
        print("wrote examples/hermes_rag/hybrid_results.json")


if __name__ == "__main__":
    main()
