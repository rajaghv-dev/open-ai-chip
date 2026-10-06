"""Opt-in live smoke tests: one or two questions per agent example against the LOCAL Ollama model (hermes3:8b).
Skipped unless HERMES_LIVE=1 (and Ollama answering on OLLAMA_URL). Never part of the fast gate.

  HERMES_LIVE=1 build/agent/venv/bin/python -m pytest -q tests/tools/test_live_smoke.py

Before running: `pgrep -f "eval_rag.py|eval_harness.py|run_eval.py|agent.py"` must show no other job (one model, one GPU).
The model is deterministic at temperature 0 / seed 42 but a smoke test only asserts structure plus the scorer verdict of
the questions that are 15/15 reproducible in the committed results (docs/HERMES_AGENT.md).

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_live_smoke.py (also part of `make test`, section == tools)
Pass: every test passes or is skipped (opt-in tests need their env flag).
Docs: tests/tools/TEST_MATRIX_TOOLS.md, docs/HERMES_AGENT.md
"""
import json
import os
import sys
import urllib.request

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for sub in ("tools", os.path.join("tools", "eval"), os.path.join("examples", "hermes_rag"),
            os.path.join("examples", "hermes_harness"), os.path.join("examples", "hermes_klayout_gui"),
            os.path.join("examples", "hermes_klayout_demo")):
    sys.path.insert(0, os.path.join(REPO, sub))

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("HERMES_MODEL", "hermes3:8b")


def _live_ready():
    if os.environ.get("HERMES_LIVE") != "1":
        return "set HERMES_LIVE=1 to run the live (Ollama) smoke tests"
    try:
        tags = json.load(urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5))
    except Exception as e:  # noqa: BLE001
        return "Ollama not reachable at %s (%s)" % (OLLAMA, e)
    if not any(m["name"] == MODEL for m in tags.get("models", [])):
        return "model %s is not pulled" % MODEL
    return None


_why = _live_ready()
pytestmark = [pytest.mark.live, pytest.mark.skipif(_why is not None, reason=_why or "")]
Q = {q["id"]: q for q in json.load(open(os.path.join(REPO, "tools", "eval", "questions.json")))}


def test_live_hermes_agent_prompt_mode():
    """Pins down: live hermes agent prompt mode."""
    import hermes_agent
    import run_eval
    for qid in ("q01", "q14"):                    # a lookup and an unanswerable question
        r = hermes_agent.ask(Q[qid]["question"], "prompt", verbose=False)
        assert run_eval.score(Q[qid]["check"], r["answer"]), (qid, r["answer"])
        assert (len(r["tool_calls"]) >= 1) == (qid == "q01")


def test_live_hermes_agent_native_mode_runs():
    """Pins down: live hermes agent native mode runs."""
    import hermes_agent
    r = hermes_agent.ask("List the hardened designs using a tool; just say how many.", "native", verbose=False)
    assert isinstance(r["answer"], str) and r["answer"] and r["seconds"] > 0      # native is 6/15: only assert it runs


def test_live_harness_all_features():
    """Pins down: live harness all features."""
    import harness
    import run_eval
    cfg = harness.Config(guardrails=True, grounding=True, pick_extreme=True)
    for qid in ("q04", "q14"):                    # pick_extreme question and an unanswerable one
        r = harness.run_episode(Q[qid]["question"], cfg, harness.Tracer("live_smoke"))
        assert run_eval.score(Q[qid]["check"], r["answer"]), (qid, r["answer"])
    r = harness.run_episode(Q["q04"]["question"], cfg, harness.Tracer("live_smoke"))
    assert "pick_extreme" in [c["name"] for c in r["tool_calls"]]


def test_live_rag_router_doc_question():
    """Pins down: live rag router doc question."""
    import rag_agent
    import harness
    import eval_rag
    import run_eval
    Qr = {q["id"]: q for q in eval_rag.load_questions()}
    for qid in ("r01", "u01"):                    # one doc question with retrieve-first, one unanswerable control
        q = Qr[qid]
        r = rag_agent.run_rag_episode(q["question"], True, False, harness.Tracer("live_smoke"), search_mode="v2",
                                      auto_retrieve=True, guardrail=True, prompt_version="v3")
        assert r["route"] == q["route"]
        if q["route"] == "doc":
            assert r["searches"] and r["searches"][0]["auto"]
        assert run_eval.score(q["check"], r["answer"]), (qid, r["answer"])


def test_live_klayout_gui_view_request():
    """Pins down: live klayout gui view request."""
    pytest.importorskip("klayout.lay")
    import agent
    from offscreen_backend import OffscreenBackend
    gds = os.path.join(REPO, "build", "results", "tiny_ai_core", "tiny_ai_core.gds")
    if not os.path.isfile(gds):
        pytest.skip("tiny_ai_core GDS not collected")
    be = OffscreenBackend()
    try:
        r = agent.run_episode("Open tiny_ai_core, show only met1 and take a snapshot.", be, agent.live_model, emit=lambda l: None)
    finally:
        be.close()
    names = [c["name"] for c in r["calls"] if c["ok"]]
    assert "open_design" in names and "snapshot" in names and r["pngs"]


def test_live_klayout_demo_question():
    """Pins down: live klayout demo question."""
    pytest.importorskip("klayout.db")
    import importlib.util       # by path: examples/hermes_klayout_gui/demo.py has the same module name
    spec = importlib.util.spec_from_file_location("klayout_demo_example", os.path.join(REPO, "examples", "hermes_klayout_demo", "demo.py"))
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    gds = os.path.join(REPO, "build", "results", "tiny_ai_core", "tiny_ai_core.gds")
    if not os.path.isfile(gds):
        pytest.skip("tiny_ai_core GDS not collected")
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        demo.run("What is the die size of tiny_ai_core?", False)
    out = buf.getvalue()
    assert "ACT     die_size" in out and "250" in out
