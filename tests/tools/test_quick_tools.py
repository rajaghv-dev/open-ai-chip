"""pytest tests/tools/test_quick_tools.py: the no-model fast paths for Hermes (examples/hermes_desktop/tool_server/quick_tools.py)
and the Hermes plugin that calls them (scripts/hermes/plugin/open-ai-chip/).
Read-only commands run on the committed evidence; actions (windows, runs) are checked with the tool calls faked: no GUI, no
job, no Docker, no model.
Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_quick_tools.py
Pass: loose design names resolve, every report command answers with its source file, frozen designs never get gds/flow-all,
a typed /run starts at once, a chat "run ..." only issues a confirm id, the plugin registers its commands and hook.
Docs: hermes-agents.md, tests/tools/TEST_MATRIX_TOOLS.md"""
import importlib.util
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "examples", "hermes_desktop", "tool_server"))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import tool_server as ts  # noqa: E402

client = TestClient(ts.app)


def _mounted_globals():
    """Namespace of the quick_tools copy that tool_server mounted (_mount_extensions loads it under its own name)."""
    flat = []
    for r in ts.app.routes:
        flat.extend(r.original_router.routes if hasattr(r, "original_router") else [r])
    return next(r.endpoint.__globals__ for r in flat if getattr(r, "path", "") == "/quick")


QG = _mounted_globals()


def q(cmd, args=""):
    r = client.post("/quick", json={"cmd": cmd, "args": args})
    assert r.status_code == 200
    return r.json()["reply"]


def test_report_commands_from_evidence():
    """Pins down: /timing /synth /drc /lvs /signoff /metrics answer for a loose name, with the numbers of metrics.json and the source."""
    t = q("timing", "kv attention 16")
    assert "kv_attn_n16 timing" in t and "8.766" in t and "timing_summary.rpt" in t
    assert "vision_all_lit synthesis" in q("synth", "vision lit") and "synth_stat.rpt" in q("synth", "vision lit")
    d = q("drc", "kv_attn")
    assert "kv_attn_n8 DRC" in d and "| Magic DRC (signoff) | 0 |" in d
    assert "Circuits match uniquely" in q("lvs", "prec bf16")
    assert "signoff: CLEAN" in q("signoff", "caravel kv")
    assert "| standard cells | 2566 |" in q("metrics", "kv8")
    c = q("compare", "kv4 kv8 kv16")
    assert "| cells | 1679 | 2566 | 4169 |" in c
    assert "did you mean" in q("metrics", "audio")             # a real tie lists the choices
    assert "/klayout" in q("chip")


def test_frozen_design_never_rewritten():
    """Pins down: gds / flow-all / collect on a frozen design start nothing and point to /rebuild."""
    for tgt in ("flow-all", "synth", "gds", "collect"):
        r = q("run", "%s kv_attn_n8" % tgt)
        assert "frozen" in r and "/rebuild" in r


def test_typed_run_starts_at_once(monkeypatch):
    """Pins down: a typed /sim is the user's consent: run_make then confirm_run, no second message needed."""
    calls = []

    def fake(name, body):
        calls.append((name, body))
        return {"confirm_id": "abc123"} if name == "run_make" else {"job_id": "J1", "log": "build/agent/jobs/J1.log"}
    monkeypatch.setitem(QG, "_call", fake)
    r = q("sim", "vision lit")
    assert calls == [("run_make", {"target": "simulate", "design": "vision_all_lit"}), ("confirm_run", {"confirm_id": "abc123"})]
    assert "J1" in r


def test_rebuild_runs_on_a_copy(monkeypatch):
    calls = []

    def fake(name, body):
        calls.append((name, body))
        return {"confirm_id": "abc123"} if "confirm_id" not in body else {"job_id": "J2", "copy": "build/whatif/kv_attn_n8__rebuild-x"}
    monkeypatch.setitem(QG, "_call", fake)
    r = q("rebuild", "kv_attn")
    assert calls[0] == ("whatif_run", {"design": "kv_attn_n8", "rebuild": True}) and "J2" in r and "not touched" in r


def test_klayout_command_sentence(monkeypatch):
    """Pins down: /klayout <loose name> <more> becomes one gui_command sentence."""
    seen = {}

    def fake(name, body):
        seen.update(body)
        return {"ok": True, "did": [{"op": "open", "design": "vision_all_lit"}, {"op": "layers"}], "seconds": 1.0}
    monkeypatch.setitem(QG, "_call", fake)
    r = q("klayout", "vision lit show only met1")
    assert seen == {"text": "open vision_all_lit in klayout and show only met1", "tool": "klayout"} and "open vision_all_lit" in r


def test_router_chat(monkeypatch):
    """Pins down: chat opens a window at once, a run question only issues a confirm id, a number question gets facts, small talk passes."""
    calls = []

    def fake(name, body):
        calls.append(name)
        if name == "gui_command":
            return {"ok": True, "did": [{"op": "open", "design": "kv_attn_n8"}], "seconds": 1.0}
        if name in ("run_make", "whatif_run"):
            return {"confirm_id": "abc123", "say": "Reply 'yes, run abc123' to start. Nothing has been started yet."}
        if name == "rag_answer":
            return {"found": False}
        return {"job_id": "J3"}
    monkeypatch.setitem(QG, "_call", fake)
    r = client.post("/quick", json={"text": "open kv_attn design"}).json()
    assert r["handled"] and r["kind"] == "window" and calls == ["gui_command"]
    r = client.post("/quick", json={"text": "run the simulation for kv8"}).json()
    assert r["kind"] == "gate" and "yes, run abc123" in r["reply"] and "confirm_run" not in calls
    r = client.post("/quick", json={"text": "rebuild vision lit"}).json()
    assert r["kind"] == "gate" and calls[-1] == "whatif_run"
    r = client.post("/quick", json={"text": "yes, run abc123"}).json()
    assert r["handled"] and calls[-1] == "confirm_run" and "J3" in r["reply"]
    r = client.post("/quick", json={"text": "what is the setup slack of kv attention 16?"}).json()
    assert not r["handled"] and "8.766" in r["context"]
    r = client.post("/quick", json={"text": "how do I run the flow for kv8?"}).json()
    assert r["handled"] is False and calls[-1] == "rag_answer"                 # a how-question gets the RAG quotes (stub: none)
    assert client.post("/quick", json={"text": "hello there"}).json() == {"handled": False, "seconds": pytest.approx(0, abs=1)}


def test_router_why_question_gets_rag_quotes(monkeypatch):
    """Pins down: a why-question is answered from the quoted RAG passages (context), before any per-design number facts."""
    monkeypatch.setitem(QG, "_call", lambda name, body: {"found": True, "mode": "hybrid", "answer": "> quote (NOTES.md:224)"}
                        if name == "rag_answer" else {})
    r = client.post("/quick", json={"text": "why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8?"}).json()
    assert r["kind"] == "rag" and "NOTES.md:224" in r["context"]


def test_plugin_registers_commands_and_hook(monkeypatch):
    """Pins down: the Hermes plugin registers every command and the pre_llm_call hook; our slash text that reaches the model is
    answered by the hook; other slash commands and small talk are left alone."""
    path = os.path.join(REPO, "scripts", "hermes", "plugin", "open-ai-chip", "__init__.py")
    spec = importlib.util.spec_from_file_location("chip_plugin", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert str(mod.REPO) == REPO

    class Ctx:
        def __init__(self):
            self.cmds, self.hooks = {}, {}

        def register_command(self, name, handler, description=""):
            self.cmds[name] = handler

        def register_hook(self, name, fn):
            self.hooks[name] = fn
    ctx = Ctx()
    mod.register(ctx)
    assert {"klayout", "magic", "synth", "timing", "drc", "lvs", "signoff", "run", "rebuild", "jobs"} <= set(ctx.cmds)
    sent = []
    monkeypatch.setattr(mod, "_quick", lambda body, timeout=600: sent.append(body) or {"handled": True, "reply": "R"})
    assert ctx.cmds["timing"]("kv8") == "R" and sent[-1] == {"cmd": "timing", "args": "kv8"}
    hook = ctx.hooks["pre_llm_call"]
    assert "ALREADY handled" in hook(user_message="/drc kv8")["context"] and sent[-1] == {"cmd": "drc", "args": "kv8"}
    n = len(sent)
    assert hook(user_message="/model") is None and len(sent) == n          # a Hermes command: not ours
    assert hook(user_message="open kv8 in klayout")["context"].endswith("R")


def test_loop_and_harness_demos(monkeypatch):
    """Pins down: /loopdemo signoff walks a family with PLAN/ACT/OBSERVE/CHECK/STOP; /harness names and /harness facts pass their gates;
    /loopdemo layers and /loopdemo sim follow their plans (GUI and job faked)."""
    r = q("loop", "signoff kv")
    assert "| 0 | PLAN |" in r and r.count("| CHECK | clean |") == 5 and "tightest setup slack kv_attn_n16 (8.766 ns)" in r
    assert "gate **PASS**" in q("harness", "names") and "score 15/15, gate **PASS**" in q("harness", "facts kv")
    assert "/loopdemo signoff" in q("loopdemo", "")
    gui = []

    def fake(name, body):
        if name == "gui_command":
            gui.append(body["text"])
            return {"ok": True, "seconds": 0.1, "markdown": "![x](http://127.0.0.1:8770/img/%d.png)" % len(gui)}
        if name == "run_make":
            return {"confirm_id": "abc123"}
        if name == "confirm_run":
            return {"job_id": "J9"}
        return {"state": "done", "rc": 0, "log_tail": ["PASS vision_all_lit_tb: 29 cases"]}
    monkeypatch.setitem(QG, "_call", fake)
    monkeypatch.setattr(QG["time"], "sleep", lambda s: None)
    r = q("loop", "layers vision lit")
    assert gui[:6] == ["open vision_all_lit in klayout"] + ["show only met%d" % i for i in range(1, 6)] and r.count("| CHECK | rendered |") == 5
    r = q("loop", "sim vision lit")
    assert "PASS vision_all_lit_tb" in r and "| STOP | verified |" in r
