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


def _recall(mode, heldout):
    sys.path.insert(0, os.path.join(REPO, "examples", "hermes_rag"))
    import eval_rag
    return eval_rag.retrieval_eval(eval_rag.load_questions(), mode, heldout)


def test_recall_at_k_on_eval_set_v1_unchanged():
    # v1 (the original search, config "rag") re-measured on the current corpus: @1 5/10, @4 8/10, @8 10/10 (the first run, on the
    # smaller corpus of 2026-10-06 14:49, had 6/10, 6/10, 9/10, 10/10; examples/hermes_rag/results_summary_v1.json)
    r = _recall("v1", False)["recall_at_k_value"]
    assert r["@4"] >= 0.8 and r["@8"] >= 1.0 and r["@1"] >= 0.5


def test_recall_at_k_v2_main_and_heldout():
    # measured: v2 main @1 8/10, @2 8/10, @4 9/10, @8 10/10; held-out (5 questions) @1 4/5, @2 4/5, @4 5/5, @8 5/5
    r = _recall("v2", False)
    assert r["recall_at_k_value"]["@1"] >= 0.8 and r["recall_at_k_value"]["@4"] >= 0.9 and r["recall_at_k_value"]["@8"] >= 1.0
    assert r["recall_at_k_value"]["@1"] > _recall("v1", False)["recall_at_k_value"]["@1"]
    h = _recall("v2", True)
    assert h["questions"] == 5 and h["recall_at_k_value"]["@1"] >= 0.8 and h["recall_at_k_value"]["@4"] >= 1.0
    assert r["evidence_value"] >= 0.5 and h["evidence_value"] >= 0.8


def test_heldout_questions_are_flagged():
    sys.path.insert(0, os.path.join(REPO, "examples", "hermes_rag"))
    import eval_rag
    Q = eval_rag.load_questions()
    assert [q["id"] for q in Q if q.get("heldout")] == ["h01", "h02", "h03", "h04", "h05"]
    assert sum(1 for q in Q if not q.get("heldout")) == 12


def test_v2_search_shape_and_identifier_boost():
    a = rag.search_docs_v2("why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8", 4)
    assert a == rag.search_docs_v2("why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8", 4)
    assert a[0]["file"].startswith("designs/kv_attn_n8_int4/")
    assert all(set(h) >= {"file", "heading", "lines", "score", "text", "id"} and len(h["text"]) <= 1300 for h in a)
    assert rag.search_docs_v2("why how what does", 4) == [] and rag.search_docs_v2("zzqxv", 4) == []
    assert rag.query_identifiers("GRT-0116 on wb_rst_i of kv_attn_n8_int4") == ["grt-0116", "wb_rst_i", "kv_attn_n8_int4"]
    assert "error" in rag.call({}, "v2") and rag.call({"query": "GRT-0116 congestion", "k": 2}, "v2")["hits"]


def test_router_decisions_for_the_eval_questions():
    sys.path.insert(0, os.path.join(REPO, "examples", "hermes_rag"))
    import eval_rag, router
    Q = eval_rag.load_questions()
    wrong = [q["id"] for q in Q if router.classify(q["question"]) != q["route"]]
    assert wrong == ["r08"]                      # r08 asks "how many CPU cycles": numbers, but only the firmware README has them
    for q in json.load(open(os.path.join(REPO, "tools", "eval", "questions.json"))):
        assert router.classify(q["question"]) == ("unknown" if q["id"] in ("q14", "q15") else "metric"), q["id"]
    assert router.classify("Why did the std cell count halve?") == "doc"
    assert router.classify("How many standard cells does vision_block have?") == "metric"
    assert router.classify("Tell me a joke") == "unknown"
    assert eval_rag.router_eval(Q)["rag_questions"] == "16/17"


def _stub_chat(replies, seen):
    it = iter(replies)

    def chat(msgs):
        seen.append(list(msgs))
        return {"message": {"content": next(it)}}
    return chat


def test_guardrail_pure_function():
    import rag_agent as A
    hits = [{"file": "designs/tiny_ai_core/NOTES.md", "text": "x"}]
    assert A.guardrail_problem("doc", "It was a bug.", hits)
    assert A.guardrail_problem("doc", "A fix.\nSources: designs/tiny_ai_core/NOTES.md > Layout", hits) is None
    assert A.guardrail_problem("doc", "A fix.\nSources: docs/OTHER.md > x", hits)       # cites a file that was not retrieved
    assert A.guardrail_problem("doc", "unknown, the docs do not say", hits) is None
    assert A.guardrail_problem("metric", "It is 5.", []) is None and A.guardrail_problem("unknown", "5", []) is None


def test_auto_retrieve_and_guardrail_with_stub_model():
    import rag_agent as A
    q = "Why did simplifying the tiny_ai_core pins roughly halve its standard cell count?"
    good = "Fewer tap cells.\nSources: designs/tiny_ai_core/NOTES.md > Intuitions and insights"
    # 1. no guardrail: the first answer is accepted as is; the retrieval context was injected before the first model turn
    seen = []
    r = A.run_rag_episode(q, grounding=False, search_mode="v2", auto_retrieve=True, prompt_version="v3", chat=_stub_chat(["It was the pins."], seen))
    assert r["route"] == "doc" and r["answer"] == "It was the pins." and r["guardrail_rejections"] == 0
    assert r["tool_calls"][0]["name"] == "search_docs" and r["tool_calls"][0]["auto"] and r["searches"][0]["auto"]
    assert len(seen) == 1 and seen[0][-1]["role"] == "tool" and "file: designs/tiny_ai_core/NOTES.md" in seen[0][-1]["content"]
    # 2. guardrail: an answer without a citation is rejected once, the second answer is accepted
    seen = []
    r = A.run_rag_episode(q, grounding=False, search_mode="v2", auto_retrieve=True, guardrail=True, prompt_version="v3",
                          chat=_stub_chat(["It was the pins.", good], seen))
    assert r["guardrail_rejections"] == 1 and r["answer"] == good and len(seen) == 2
    assert "documentation question" in seen[1][-1]["content"]
    # 3. rejected only once: a second uncited answer is returned as is
    r = A.run_rag_episode(q, grounding=False, search_mode="v2", auto_retrieve=True, guardrail=True, prompt_version="v3",
                          chat=_stub_chat(["no cite", "still no cite"], []))
    assert r["guardrail_rejections"] == 1 and r["answer"] == "still no cite"
    # 4. metric-type questions are not retrieved for and not guarded
    seen = []
    r = A.run_rag_episode("How many standard cells does vision_block have?", grounding=False, search_mode="v2", auto_retrieve=True,
                          guardrail=True, prompt_version="v3", chat=_stub_chat(["There are 5."], seen))
    assert r["route"] == "metric" and r["searches"] == [] and r["guardrail_rejections"] == 0 and len(seen) == 1
    # 5. the old configuration is untouched: no auto retrieval, no guardrail even for a doc question
    seen = []
    r = A.run_rag_episode(q, grounding=False, chat=_stub_chat(["plain answer"], seen))
    assert r["searches"] == [] and r["guardrail_rejections"] == 0 and r["answer"] == "plain answer"


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
