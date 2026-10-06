"""pytest tests/tools/test_rag.py  (deterministic parts of examples/hermes_rag: no LLM, no network)"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "examples", "hermes_rag"))
sys.path.insert(0, os.path.join(REPO, "tools"))
import rag  # noqa: E402


def test_index_builds_and_chunks_carry_provenance():
    idx = rag.build_index(force=True)
    assert len(idx["chunks"]) > 300 and len(idx["files"]) > 40
    assert not any(f.startswith((".claude/", "examples/")) for f in idx["files"])
    c = idx["chunks"][0]
    assert c["file"].endswith(".md") and c["heading"] and 1 <= c["start"] <= c["end"]
    # the cache is reused when no file changed, and keyed by sha256
    again = rag.build_index()
    assert again["cached"] and again["files"] == idx["files"]
    assert all(len(v) == 64 for v in idx["files"].values())


def test_search_is_deterministic_and_shaped():
    a = rag.search_docs("slew repair margin out of memory", 4)
    b = rag.search_docs("slew repair margin out of memory", 4)
    assert a == b and 1 <= len(a) <= 4
    for h in a:
        assert set(h) >= {"file", "heading", "lines", "score", "text"} and len(h["text"]) <= 830
    assert [h["score"] for h in a] == sorted((h["score"] for h in a), reverse=True)
    assert rag.search_docs("zzqxv", 4) == []


def _files(query, k=4):
    return [h["file"] for h in rag.search_docs(query, k)]


def test_right_file_for_known_queries():
    assert "docs/PRECHECK.md" in _files("precheck 12 of 14 gpio_defines oeb GPIO_MODE_INVALID", 4)
    assert "docs/PRECISION_STUDY.md" in _files("sweet spot ternary int4 accuracy area", 2)
    assert "firmware/README.md" in _files("prefill amortises fixed cost decode round trip cycles", 2)
    assert "designs/soc_image_text_match/NOTES.md" in _files("why soc_image_text_match re-run kv_attn_core added", 2)
    assert "designs/user_project_wrapper/README.md" in _files("70 LVS errors unpowered antenna diodes router", 4)


def test_tool_schema_and_call():
    t = rag.TOOL
    assert t["type"] == "function" and t["function"]["name"] == "search_docs"
    assert t["function"]["parameters"]["required"] == ["query"]
    json.dumps(t)
    assert "error" in rag.call({}) and "error" in rag.call({"query": "x", "k": "a"})
    r = rag.call({"query": "GRT-0116 congestion", "k": 3})
    assert len(r["hits"]) == 3


def test_recall_at_k_on_eval_set():
    # measured 2026-10-06: recall@1 6/10, @2 6/10, @4 9/10, @8 10/10 (examples/hermes_rag/results_summary.json)
    sys.path.insert(0, os.path.join(REPO, "examples", "hermes_rag"))
    import eval_rag
    r = eval_rag.retrieval_eval(eval_rag.load_questions())["recall_at_k_value"]
    assert r["@4"] >= 0.9 and r["@8"] >= 1.0 and r["@2"] >= 0.6 and r["@1"] >= 0.6


def test_grounding_check_and_citations():
    import rag_agent as A
    hits = [{"file": "designs/user_project_wrapper/README.md", "text": "a 70% margin ran out of memory; 20% margin was used"}]
    good = "20 percent, because 70 percent ran out of memory.\nSources: designs/user_project_wrapper/README.md > Steps"
    assert A.rag_grounding("why 20", good, hits, []) == []
    assert any("not retrieved" in p for p in A.rag_grounding("q", good.replace("designs/user_project_wrapper", "docs"), hits, []))
    assert any("999" in p for p in A.rag_grounding("q", "It is 999 percent.\nSources: designs/user_project_wrapper/README.md > x", hits, []))
    assert any("no citation" in p for p in A.rag_grounding("q", "20 percent.", hits, []))
    assert A.rag_grounding("q", "unknown, the docs do not say", hits, []) == []
    assert A.citations(good) == ["designs/user_project_wrapper/README.md"]
