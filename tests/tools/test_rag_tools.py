"""pytest tests/tools/test_rag_tools.py: the hybrid mini RAG tools (examples/hermes_desktop/tool_server/rag_tools.py).
A small fixture repo is written to a temp dir (rag.ROOT points at it) and embeddings are a deterministic stub (hashed bag of words),
so the tests need no Ollama, no model, no network and no flow run.

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_rag_tools.py
Pass: chunks keep file/heading/lines, RRF fusion is right, the index is incremental, a down Ollama falls back to BM25 and says so,
citations have the documented shape, the design filter holds, rag_answer quotes sentences verbatim.
Docs: tests/tools/TEST_MATRIX_TOOLS.md, docs/HERMES_AGENT_INTEGRATION.md ("Mini RAG")
"""
import hashlib
import importlib.util
import json
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TS = os.path.join(REPO, "examples", "hermes_desktop", "tool_server")
sys.path.insert(0, os.path.join(REPO, "examples", "hermes_rag"))
import rag  # noqa: E402

spec = importlib.util.spec_from_file_location("rag_tools_under_test", os.path.join(TS, "rag_tools.py"))
RT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RT)

NOTES_A = """# kv_attn_a
Intro line about the design that is long enough to count.

## Intuitions and insights
The int4 variant has more flip-flops because Yosys pruned constant cache bits in the baseline and the clamp logic hides them.
Another sentence about setup slack being limited by the reset input delay of 12.5 ns.

```
# not a heading
```
"""
NOTES_B = "# kv_attn_b\n\n## Results\nThe b design uses a ring buffer for the cache and reports a smaller area than the others.\n"
DOC = "# Flow\n\n## Congestion\nGRT-0116 global routing congestion was fixed by placing pins in pad order on the bottom edge.\n"


def _stub_embed(texts, model, timeout=0):
    out = []
    for t in texts:
        v = [0.0] * 64
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
        out.append(v)
    return out


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    def w(rel, text):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    w("designs/kv_attn_a/NOTES.md", NOTES_A)
    w("designs/kv_attn_b/NOTES.md", NOTES_B)
    w("docs/FLOW.md", DOC)
    w("scripts/tool.py", '#!/usr/bin/env python3\n"""tool: does a thing for the flow.\nDocs: docs/FLOW.md\n"""\nimport os\n')
    w("scripts/run.sh", "#!/usr/bin/env bash\n# Docs: docs/FLOW.md\n# run.sh -- runs it\nset -e\n")
    w("designs/kv_attn_a/output/metrics.json", json.dumps({"design__instance__count__class:sequential_cell": 222, "timing__setup__ws": 12.5, "route__drc_errors": 0}))
    w("designs/kv_attn_a/runs/RUN_1/junk.md", "# junk\nshould never be indexed\n")
    w("build/x.md", "# build\nnever\n")
    w("CLAUDE.md", "# claude\nnever\n")
    monkeypatch.setattr(rag, "ROOT", str(tmp_path))
    monkeypatch.setattr(RT, "EMBED_FN", _stub_embed)
    monkeypatch.setattr(RT, "QUERY_PREFIX", "")
    RT._MEM.clear()
    RT._QV.clear()
    yield tmp_path
    RT._MEM.clear()


def test_corpus_skips_build_runs_claude_and_includes_code_and_metrics(repo):
    files = {f["file"]: f["kind"] for f in RT.corpus_files()}
    assert "designs/kv_attn_a/NOTES.md" in files and files["scripts/tool.py"] == "code"
    assert files["designs/kv_attn_a/output/metrics.json"] == "metrics"
    assert not any("runs/" in f or f.startswith("build/") or f == "CLAUDE.md" for f in files)


def test_chunking_keeps_file_heading_path_and_lines(repo):
    cs = RT.chunk_one({"file": "designs/kv_attn_a/NOTES.md", "kind": "md"})
    h = next(c for c in cs if c["heading"].endswith("Intuitions and insights"))
    assert h["file"] == "designs/kv_attn_a/NOTES.md" and h["heading"] == "kv_attn_a > Intuitions and insights"
    assert h["start"] == 4 and h["end"] >= h["start"] and "pruned" in h["text"]
    assert not any(c["heading"].endswith("not a heading") for c in cs)       # '#' inside a fence is not a heading


def test_code_header_and_metrics_chunks(repo):
    py = RT.chunk_code_header("scripts/tool.py")[0]
    assert py["start"] == 2 and "Docs: docs/FLOW.md" in py["text"] and "import os" not in py["text"]
    sh = RT.chunk_code_header("scripts/run.sh")[0]
    assert "run.sh -- runs it" in sh["text"] and "set -e" not in sh["text"]
    mt = RT.chunk_metrics("designs/kv_attn_a/output/metrics.json")[0]
    assert "kv_attn_a sequential cells (flip-flops and latches): 222" in mt["text"] and "worst setup slack" in mt["text"]


def test_rrf_fusion():
    a = [(1, 9.0), (2, 8.0), (3, 7.0)]
    b = [(3, 0.9), (1, 0.8), (4, 0.7)]
    f = RT.fuse([a, b])
    assert [x[0] for x in f][:2] == [1, 3]                                     # appear high in both lists
    assert dict(f)[1] == pytest.approx(1 / 61 + 1 / 62)
    assert dict(f)[4] == pytest.approx(1 / 63)
    assert RT.fuse([a])[0][0] == 1


def test_index_is_incremental_and_cached(repo):
    st = RT.build_index()
    assert st["files"] == 6 and st["chunks"] > 0 and st["vectors"] == st["chunks"] and st["vectors_new"] == st["chunks"]
    RT._MEM.clear()
    st2 = RT.build_index()
    assert st2["files_rechunked"] == 0 and st2["vectors_new"] == 0           # nothing changed: nothing re-embedded
    (repo / "docs/FLOW.md").write_text(DOC + "\n## New\nA brand new section about antenna diodes and their insertion.\n")
    RT._MEM.clear()
    st3 = RT.build_index()
    assert st3["files_rechunked"] == 1 and 0 < st3["vectors_new"] < st3["chunks"]
    assert os.path.isfile(repo / "build/agent/rag/chunks.json")


def test_hybrid_search_and_citation_shape(repo):
    r = RT.search("why does the int4 variant have more flip-flops", k=3)
    assert r["mode"] == "hybrid" and r["note"] is None
    top = r["hits"][0]
    assert top["file"] == "designs/kv_attn_a/NOTES.md" and "pruned" in top["text"]
    assert set(top) >= {"file", "heading", "lines", "score", "text", "markdown"}
    assert re.fullmatch(r"\[designs/kv_attn_a/NOTES\.md > .+ \(lines \d+-\d+\)\]\(designs/kv_attn_a/NOTES\.md#L\d+-L\d+\)", top["markdown"])


def test_fallback_without_ollama(repo, monkeypatch):
    RT.build_index()                                      # vectors exist ...
    def down(texts, model, timeout=0):
        raise ConnectionRefusedError("ollama is down")
    monkeypatch.setattr(RT, "EMBED_FN", down)             # ... but the query cannot be embedded
    RT._QV.clear()
    r = RT.search("GRT-0116 congestion fixed by pad order")
    assert r["mode"] == "bm25-fallback" and "BM25 only" in r["note"]
    assert r["hits"][0]["file"] == "docs/FLOW.md"
    a = RT.answer("GRT-0116 congestion fixed by pad order")
    assert a["mode"] == "bm25-fallback" and a["found"]


def test_build_with_ollama_down_still_indexes_bm25(repo, monkeypatch):
    def down(texts, model, timeout=0):
        raise ConnectionRefusedError("down")
    monkeypatch.setattr(RT, "EMBED_FN", down)
    st = RT.build_index()
    assert st["chunks"] > 0 and st["vectors"] == 0 and "embedding failed" in st["note"]
    assert RT.search("ring buffer cache area")["mode"] == "bm25-fallback"


def test_design_filter_explicit_and_auto(repo):
    q = "ring buffer cache area"
    explicit = RT.search(q, k=5, design="kv_attn_b")
    assert explicit["filter"] == ["kv_attn_b"] and all(h["file"].startswith("designs/kv_attn_b/") for h in explicit["hits"])
    auto = RT.search("what does kv_attn_b say about the cache", k=1)
    assert auto["filter"] == ["kv_attn_b"] and auto["hits"][0]["file"].startswith("designs/kv_attn_b/")
    assert RT.designs_named("compare kv_attn_a and kv_attn_b") == ["kv_attn_a", "kv_attn_b"]
    assert RT.designs_named("kv_attn_a2 is not a design") == []
    assert RT.search("GRT-0116 congestion", design="kv_attn_a")["filter"] == ["kv_attn_a"]


def test_answer_quotes_are_verbatim_with_source(repo):
    a = RT.answer("Why does the int4 variant have more flip-flops?")
    assert a["found"] and a["quotes"]
    notes = [q for q in a["quotes"] if q["file"].endswith("NOTES.md")]
    assert notes and "pruned" in a["answer"]
    text = (repo / "designs/kv_attn_a/NOTES.md").read_text()
    for q in notes:
        assert q["quote"] in text                                              # verbatim
        assert re.fullmatch(r"designs/kv_attn_a/NOTES\.md:\d+", q["citation"])
        assert q["quote"][:30] in text.split("\n")[q["line"] - 1]              # the cited line holds it
    for q in a["quotes"]:                                                      # a metrics quote is a rendered fact line, cited as metrics.json:line
        assert q["file"].endswith(("NOTES.md", "metrics.json")) and q["citation"].startswith(q["file"] + ":")
    assert a["passages"]


def test_answer_when_nothing_matches(repo):
    a = RT.answer("zzzqqq xylophone quasar")
    assert a["found"] is False and "do not know" in a["answer"]


def test_endpoints_and_status(repo):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(RT.router)
    c = TestClient(app)
    ops = {op["operationId"] for item in app.openapi()["paths"].values() for op in item.values()}
    assert ops == {"rag_search", "rag_answer", "rag_index"}
    r = c.post("/rag_search", json={"query": "ring buffer cache", "k": 2, "design": "kv_attn_b"}).json()
    assert r["hits"] and "markdown" in r and r["markdown"].startswith("1. [designs/kv_attn_b/NOTES.md")
    assert c.post("/rag_answer", json={"question": "GRT-0116 congestion fixed by"}).json()["found"]
    s = c.post("/rag_index", json={}).json()
    assert s["files"] == 6 and s["model"] == RT.EMBED_MODEL and set(s["by_kind"]) == {"md", "code", "metrics"} and "age_seconds" in s
    assert c.post("/rag_index", json={"rebuild": True}).json()["vectors_new"] == s["chunks"]
