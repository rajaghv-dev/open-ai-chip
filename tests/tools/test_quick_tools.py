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


@pytest.fixture(autouse=True)
def _own_pending(monkeypatch, tmp_path):
    """Each test gets its own "Which design?" memory, so no answer is taken by another test's question."""
    monkeypatch.setitem(QG["nt"].__dict__, "PENDING", str(tmp_path / "pending.json"))


def q(cmd, args=""):
    r = client.post("/quick", json={"cmd": cmd, "args": args})
    assert r.status_code == 200
    return r.json()["reply"]


def test_report_commands_from_evidence():
    """Pins down: /timing /synth /drc /lvs /signoff /metrics answer for a loose name, with the numbers of metrics.json and the source."""
    t = q("timing", "kv attention 16")
    assert "kv_attn_n16 timing: 🟢" in t and "8.766" in t and "timing_summary.rpt" in t
    assert "vision_all_lit synthesis" in q("synth", "vision lit") and "synth_stat.rpt" in q("synth", "vision lit")
    d = q("drc", "kv_attn")
    assert "kv_attn_n8 DRC: ✅ clean" in d and "| Magic DRC (signoff) | ✅ | 0 |" in d
    assert "Circuits match uniquely" in q("lvs", "prec bf16")
    assert "signoff: ✅ CLEAN" in q("signoff", "caravel kv")
    assert "| standard cells | 2566 |" in q("metrics", "kv8") and "🟢" in q("metrics", "kv8")
    c = q("compare", "kv4 kv8 kv16")
    assert "| cells | 1679 | 2566 | 4169 |" in c
    assert q("metrics", "audio").startswith("Which design? 1) audio_onset  2) audio_pitch")   # a real tie asks
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
    assert r["kind"] == "rag" and r["handled"] and "NOTES.md:224" in r["reply"]      # quoted verbatim, not paraphrased


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
    assert "Done" in hook(user_message="open kv8 in klayout", session_id="s1")["context"]
    assert ctx.hooks["transform_llm_output"](response_text="Done", session_id="s1") == "R"        # the exact reply replaces the model's
    assert ctx.hooks["transform_llm_output"](response_text="hi", session_id="s1") is None         # only once, only for that turn


def test_loop_and_harness_demos(monkeypatch):
    """Pins down: /loopdemo signoff walks a family with PLAN/ACT/OBSERVE/CHECK/STOP; /harness names and /harness facts pass their gates;
    /loopdemo layers and /loopdemo sim follow their plans (GUI and job faked)."""
    r = q("loop", "signoff kv")
    assert "| 0 | PLAN |" in r and r.count("| CHECK | ✅ clean |") == 5 and "tightest setup slack kv_attn_n16 (8.766 ns)" in r
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


def test_llm_names_and_helpful_miss():
    """Pins down: "llm" / "transformer" mean the KV-cache attention family; a name that points two ways ("llm_lit") never
    dead-ends: the reply offers ready-to-type commands and the design list by family, and nothing is opened."""
    full = sorted(set(VALID_ALL()))
    assert QG["nt"].resolve_design("llm", full)[0] == "kv_attn_n8" and QG["nt"].resolve_design("transformer", full)[0] == "kv_attn_n8"
    r = q("klayout", "open llm_lit design")
    assert r.startswith("Which design? 1) kv_attn_n8  2) vision_all_lit") and "`/klayout vision_all_lit`" in r
    r = q("klayout", "open zzqq design")
    assert "No design is called 'zzqq'" in r and "| KV-cache attention (LLM inference) |" in r


def VALID_ALL():
    return QG["nt"].designs()


def test_ambiguous_name_asks_then_pick_completes(monkeypatch, tmp_path):
    """Pins down: every command takes partial names; an ambiguous one asks with numbered choices (no guess, nothing run), and the
    user's answer ("2", "/pick 2", a name) runs the same command on that design without a model turn; shell syntax is refused."""
    r = q("timing", "audio")
    assert r.startswith("Which design? 1) audio_onset  2) audio_pitch") and "`/timing audio_pitch`" in r
    r = client.post("/quick", json={"text": "2"}).json()
    assert r["kind"] == "pick" and r["reply"].startswith("(audio_pitch) ## audio_pitch timing")
    assert client.post("/quick", json={"text": "2"}).json()["handled"] is False          # the question was cleared
    assert "7)" in q("lvs", "prec") and "prec_tern" in q("lvs", "prec")                   # a whole family is offered
    assert q("pick", "bf16").startswith("(prec_bf16) ## prec_bf16 LVS: ✅")
    assert "nothing to pick" in q("pick", "1")
    assert QG["nt"].resolve_design("vision_block; rm -rf /")[0] is None


def test_router_search_and_experiments(monkeypatch):
    """Pins down: plain sentences for search, the experiment list, an experiment run (confirm id only) and its result are handled
    in code, before the model."""
    calls = []

    def fake(name, body):
        calls.append((name, body))
        if name == "rag_search":
            return {"mode": "hybrid", "passages": [{"markdown": "[NOTES.md](x)", "text": "hold fixed by the SDC"}]}
        if name == "list_experiments":
            return {"experiments": [{"id": "soc-kv", "title": "KV cache prefill vs decode", "expected_time": "15 s", "command": "make soc-kv"}]}
        if name == "run_experiment":
            return {"confirm_id": "abc123", "will_run": "make soc-kv", "expected_time": "15 s", "say": "Reply 'yes, run abc123' to start."}
        return {"markdown": "TABLE"}
    monkeypatch.setitem(QG, "_call", fake)
    post = lambda t: client.post("/quick", json={"text": t}).json()  # noqa: E731
    r = post("search the docs for hold violation fix")
    assert r["kind"] == "search" and calls[-1] == ("rag_search", {"query": "hold violation fix", "k": 5})
    assert post("list the experiments")["kind"] == "experiments"
    r = post("run the soc-kv experiment")
    assert r["kind"] == "gate" and "yes, run abc123" in r["reply"] and calls[-1] == ("run_experiment", {"id": "soc-kv"})
    assert ("confirm_run", {"confirm_id": "abc123"}) not in calls                      # a sentence never starts a run
    r = post("show the results of the kv-cache experiment")
    assert r["kind"] == "result"


def test_demo_cards():
    """Pins down: nine demo cards, each with commands to type and sentences; /demo by name or number; /demos lists them."""
    assert len(QG["DEMO_CARDS"]) == 9
    r = q("demo", "layout")
    assert r.startswith("## Demo 3: Open, operate and close a layout") and "- `klayout llm show only met1`" in r and "**Or say**" in r
    assert q("demo", "6").startswith("## Demo 6: Kick off an experiment")
    assert "| 9 | The model itself, for contrast | `/demo model` |" in q("demos", "")


def test_colours_and_plain_commands(monkeypatch):
    """Pins down: 🔴 only for negative slack, by column (a delta is never coloured); plain "timing kv8" is a command answered as a
    chat reply; a plain command that would START a run keeps the confirm step unless prefixed with "chip"."""
    md = "| metric | what-if | committed | delta |\n|---|---|---|---|\n| setup worst slack ns | -0.5 | 16.07 | -16.57 |\n" \
         "\n| corner | setup what-if | hold what-if |\n|---|---|---|\n| nom_tt | 2.07 | 0.05 |"
    out = QG["colour_slack"](md)
    assert out.startswith("🔴 **Negative slack") and "🔴 -0.5" in out and "🟢 16.07" in out and "| -16.57 |" in out
    assert "🟢 2.07" in out and "🟡 0.05" in out                       # hold thresholds for the hold column
    assert QG["colour_slack"]("| corner | setup x |\n|---|---|\n| a | 3 |").startswith("| corner")   # no banner when all met
    pc = QG["plain_command"]
    assert pc("timing kv8") == ("timing", "kv8") and pc("klayout kv_attn show only met1") == ("klayout", "kv_attn show only met1")
    assert pc("run the soc-kv experiment") is None and pc("close all windows") is None
    assert pc("rebuild vision lit") is None and pc("whatif vision lit CLOCK_PERIOD=5") is None and pc("run synth kv8") is None
    assert pc("chip rebuild vision lit") == ("rebuild", "vision lit") and pc("chip close") == ("close", "")
    assert pc("ask why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8?")[0] == "ask"
    r = client.post("/quick", json={"text": "timing kv8"}).json()
    assert r["handled"] and r["kind"] == "command" and r["reply"].startswith("## kv_attn_n8 timing: 🟢")
