"""pytest tests/tools/test_skills_memory.py: skill_plan (6 skills), local memory (remember/recall/forget, run history,
digest bound, rotation, validation, secrets), context_brief, the Open WebUI prompts installer payload and the filter.
No Ollama, no network, no Open WebUI: memory goes to a temp dir (CHIP_MEMORY_DIR).

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_skills_memory.py
Pass: every test passes.
Docs: tests/tools/TEST_MATRIX_TOOLS.md, docs/HERMES_DESKTOP.md, docs/AGENT_CONTEXT.md
"""
import importlib.util
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts", "lib"))
sys.path.insert(0, os.path.join(REPO, "examples", "hermes_desktop"))
MOD = os.path.join(REPO, "examples", "hermes_desktop", "tool_server", "skills_memory_tools.py")


@pytest.fixture()
def sm(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("skills_memory_tools_t", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "MEM_DIR", str(tmp_path / "memory"))
    return m


SIX = ["harden-design", "soc-run", "wrapper-build", "write-design-notes", "precision-variant", "add-tiny-engine"]


def test_skill_plan_all_six(sm):
    """Pins down: every skill returns numbered steps; run skills name run_make, edit skills hand off to Claude."""
    for s in SIX:
        p = sm.skill_plan_for(s, "kv_attn_n8")
        assert "error" not in p, p
        assert [x["n"] for x in p["steps"]] == list(range(1, len(p["steps"]) + 1))
        assert p["docs"].startswith(".claude/skills/" + s)
        assert os.path.isfile(os.path.join(REPO, ".claude", "skills", s, "SKILL.md"))
    for s in ("harden-design", "soc-run", "wrapper-build"):
        p = sm.skill_plan_for(s, "kv_attn_n8")
        assert p["mode"] == "run" and any(x.get("tool") == "run_make" for x in p["steps"])
        assert "confirm" in p["note_gate"]
    for s in ("write-design-notes", "precision-variant", "add-tiny-engine"):
        p = sm.skill_plan_for(s, "kv_attn_n8")
        assert p["mode"] == "edit" and "claude_task" in p["handoff"] and "does not edit" in p["handoff"]
        assert all(x.get("tool") != "run_make" or x["args"]["target"] in ("simulate", "flow-all", "adapter-test")
                   for x in p["steps"])


def test_skill_plan_harden_steps_and_errors(sm):
    p = sm.skill_plan_for("harden-design", "vision_block")
    tools = [x.get("tool") for x in p["steps"]]
    assert tools.index("run_make") < tools.index("run_summary") < tools.index("diagnose") < tools.index("suggest")
    assert any(x.get("args", {}).get("target") == "flow-all" and x["args"]["design"] == "vision_block" for x in p["steps"])
    assert "error" in sm.skill_plan_for("nope")
    assert "error" in sm.skill_plan_for("harden-design", "not_a_design")
    assert sm.skill_plan_for("/harden")["skill"] == "harden-design"
    assert "note" in sm.skill_plan_for("harden-design")      # no design given


def test_memory_roundtrip_and_forget(sm):
    a = sm.remember_note("I prefer areas in um2", "preferences")
    b = sm.remember_note("kv_attn_n8 is the reference engine")
    assert a["topic"] == "preferences" and b["topic"] == "general"
    d = sm.MEM_DIR
    assert sorted(os.listdir(d)) == ["MEMORY.md", "general.md", "preferences.md", "runs.md"] or "runs.md" not in os.listdir(d)
    assert "preferences" in open(os.path.join(d, "MEMORY.md")).read()
    r = sm.recall_notes("areas um2")
    assert r["count"] == 1 and r["notes"][0]["id"] == a["id"]
    assert sm.recall_notes()["count"] == 2
    assert sm.forget_note(a["id"])["forgotten"] == a["id"]
    assert sm.recall_notes("areas")["count"] == 0
    assert "error" in sm.forget_note(a["id"])


def test_validation_and_secrets(sm):
    for bad in ("../x", "A b", "runs", "x" * 40, "a/b"):
        assert "error" in sm.remember_note("hello", bad), bad
    assert "error" in sm.remember_note("   ")
    assert "error" in sm.remember_note("x" * 500)
    assert "error" in sm.remember_note("my api_key = abcdef123456")
    assert "error" in sm.remember_note("sk-" + "a" * 30)
    assert "error" in sm.remember_note("h" * 50)
    assert not os.path.exists(sm.MEM_DIR) or os.listdir(sm.MEM_DIR) == []
    assert "error" in sm.forget_note("../../etc/passwd")


def test_only_memory_dir_written(sm, tmp_path):
    sm.remember_note("a note", "t1")
    sm.record_run("kv_attn_n8", "make simulate", "PASS")
    for root, _, files in os.walk(str(tmp_path)):
        assert root.startswith(sm.MEM_DIR) or root == str(tmp_path)
    assert not [f for f in os.listdir(sm.MEM_DIR) if not f.endswith(".md")]


def test_run_history_digest_and_bounds(sm):
    for i in range(sm.MAX_RUN_ENTRIES + 20):
        sm.record_run("kv_attn_n8", "make flow-all DESIGN=kv_attn_n8", "ok", "cells=%d" % i,
                      os.path.join(REPO, "build", "agent", "jobs", "j%d.log" % i))
    assert len(sm._read_entries("runs")) == sm.MAX_RUN_ENTRIES       # rotated, newest kept
    text = open(os.path.join(sm.MEM_DIR, "runs.md")).read()
    assert "cells=%d" % (sm.MAX_RUN_ENTRIES + 19) in text and "cells=0 " not in text and "/Users/" not in text
    for i in range(sm.MAX_TOPIC_ENTRIES + 5):
        sm.remember_note("note number %d " % i + "word " * 70, "bulk")
    assert len(sm._read_entries("bulk")) == sm.MAX_TOPIC_ENTRIES
    d = sm.digest()
    assert sm._tokens(d) <= sm.DIGEST_TOKENS
    assert d.count("\n- ") <= 8 + 5
    assert 1 <= len(re.findall(r"cmd=make flow-all", d)) <= 5          # last 5 runs only, whole lines
    assert sm.memory_digest()["approx_tokens"] <= sm.DIGEST_TOKENS


def test_digest_empty(sm):
    d = sm.digest()
    assert "no notes yet" in d and "no runs recorded yet" in d


def test_record_run_redacts_and_never_raises(sm):
    r = sm.record_run("d", "make x", "failed", "token=abcdefghijk")
    assert "[redacted]" in r["stored"]
    assert "error" not in sm.record_run(None, "", "", None, None)


def test_context_brief(sm):
    sm.remember_note("remember me", "t")
    b = sm.context_brief()
    assert "FLOW" in b["context"] and "CLEAN" in b["context"] and "HARD RULES" in b["context"]
    assert "remember me" in b["memory"] and "<!--" not in b["context"]
    assert len(b["context"]) <= sm.BRIEF_CONTEXT_CHARS + 10


def test_router_tools_and_hidden_record_run(sm):
    ops = {r.operation_id: r for r in sm.router.routes}
    names = {"skill_plan", "remember", "recall", "forget", "memory_digest", "context_brief", "record_run"}
    assert names <= set(ops)
    assert not ops["record_run"].include_in_schema
    assert all(ops[n].include_in_schema for n in names - {"record_run"})


def test_prompts_installer_payload():
    import install_prompts as IP
    forms = IP.prompt_forms()
    assert [f["command"] for f in forms] == ["/harden", "/soc-run", "/wrapper", "/notes", "/precision", "/add-engine"]
    for f in forms:
        assert f["name"] and "skill_plan" in f["content"] and f["tags"][0] == "chip-skill"
        assert set(f) <= {"command", "name", "content", "tags", "commit_message", "is_production"}   # PromptForm fields
    skills = {f["tags"][1] for f in forms}
    assert skills == set(SIX)
    ff = IP.filter_form()
    assert ff["id"].isidentifier() and "class Filter" in ff["content"] and "def inlet" in ff["content"]


def test_memory_filter_inlet(monkeypatch):
    spec = importlib.util.spec_from_file_location("memory_filter_t", os.path.join(REPO, "examples", "hermes_desktop",
                                                                                  "memory_filter.py"))
    pytest.importorskip("pydantic")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    f = m.Filter()
    monkeypatch.setattr(f, "_digest", lambda: "MEMORY: x")
    body = f.inlet({"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]})
    assert body["messages"][0]["content"].endswith("MEMORY: x") and len(body["messages"]) == 2
    assert f.inlet(body)["messages"][0]["content"].count(m.MARK) == 1      # no double insert
    def boom():
        raise OSError("down")
    monkeypatch.setattr(f, "_digest", boom)
    b2 = {"messages": [{"role": "user", "content": "hi"}]}
    assert f.inlet(b2) == {"messages": [{"role": "user", "content": "hi"}]}
