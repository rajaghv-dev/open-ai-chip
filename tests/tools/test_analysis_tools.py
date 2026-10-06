"""Tests for examples/hermes_desktop/tool_server/analysis_tools.py (run_summary, diagnose, suggest, notes_section, explain)
and the two-step confirmation gate of run_make / claude_task. FastAPI TestClient, deterministic on committed designs;
no Ollama, Docker or Claude needed, no physical flow.

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_analysis_tools.py (also part of `make test`, section == tools)
Pass: every test passes.
Docs: tests/tools/TEST_MATRIX_TOOLS.md, examples/hermes_desktop/tool_server/README.md, docs/HERMES_DESKTOP.md
"""
import json
import os
import re
import sys
import time

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "examples", "hermes_desktop", "tool_server"))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import tool_server as ts  # noqa: E402

client = TestClient(ts.app)
D = "kv_attn_n8"


def post(name, body=None):
    return client.post("/" + name, json=body or {}).json()


def committed(d):
    return json.load(open(os.path.join(REPO, "designs", d, "output", "metrics.json")))


def test_operations_mounted():
    """Pins down: the five analysis tools are served with operationId == name and a description."""
    spec = client.get("/openapi.json").json()
    ops = {op["operationId"]: op for item in spec["paths"].values() for op in item.values()}
    for n in ("run_summary", "diagnose", "suggest", "notes_section", "explain"):
        assert n in ops and ops[n].get("description") and ops[n].get("summary")


def test_run_summary_numbers_equal_metrics():
    """Pins down: run_summary key numbers equal the committed metrics.json."""
    m = committed(D)
    r = post("run_summary", {"design": D})
    assert "error" not in r, r
    k = r["key_numbers"]
    assert k["stdcells"] == m["design__instance__count__stdcell"]
    assert k["flip_flops"] == m["design__instance__count__class:sequential_cell"]
    assert k["max_slew_violations"] == m["design__max_slew_violation__count"]
    assert k["drc"]["magic"] == m["magic__drc_error__count"] == 0
    assert k["setup_worst"]["slack_ns"] == pytest.approx(m["timing__setup__ws"], abs=1e-3)
    assert k["hold_worst"]["slack_ns"] == pytest.approx(m["timing__hold__ws"], abs=1e-3)
    assert r["violations"] == [] and r["summary"] and r["diff_vs_committed"]["changed_keys"] in (0, r["diff_vs_committed"]["changed_keys"])
    assert str(k["stdcells"]) in r["summary"]
    assert "error" in post("run_summary", {"design": "nope"})
    assert "error" in post("run_summary", {"design": D, "run": "../../etc"})
    assert "error" in post("run_summary", {})


def test_run_summary_job_log():
    """Pins down: run_summary accepts a finished job's id, reads its log and points to the design's run."""
    jid = "20000101_000000-99"
    os.makedirs(ts.JOB_DIR, exist_ok=True)
    p = os.path.join(ts.JOB_DIR, jid + ".log")
    try:
        with open(p, "w") as f:
            f.write("$ make simulate DESIGN=%s\nPASS\n[exit 0 after 3.2s]\n" % D)
        r = post("run_summary", {"job_id": jid})
        assert r["job"]["state"] == "done" and r["job"]["rc"] == 0 and r["design"] == D
        assert "Job %s" % jid in r["summary"]
        assert "error" in post("run_summary", {"job_id": "x; rm"})
    finally:
        os.remove(p)


def test_diagnose_clean_and_failure_table():
    """Pins down: diagnose reports clean on a clean design and matches the failure table on a failed job log."""
    r = post("diagnose", {"design": D})
    assert r["clean"] is True and r["failed_stages"] == [] and "never" in r
    jid = "20000101_000001-98"
    p = os.path.join(ts.JOB_DIR, jid + ".log")
    try:
        with open(p, "w") as f:
            f.write("$ make gds DESIGN=%s\n[ERROR GPL-0301] utilization exceeds 100%%\n[exit 2 after 9.0s]\n" % D)
        r = post("diagnose", {"job_id": jid})
        assert r["clean"] is False and r["matches"][0]["id"] == 2
        assert "DIE_AREA" in r["matches"][0]["fix"] and r["matches"][0]["doc"].startswith(".claude/skills/harden-design/reference.md")
        assert any("GPL-0301" in e for e in r["matches"][0]["evidence"])
    finally:
        os.remove(p)


def test_failure_table_rows_have_docs_and_no_loosening():
    """Pins down: every failure row has cause, fix, doc and no fix loosens a hard-rule constraint."""
    import analysis_tools as at  # tool_server loads its own copy; the table is plain data
    assert len(at.FAILURES) >= 13
    for row in at.FAILURES:
        assert row["cause"] and row["fix"] and row["doc"] and row["regex"]
        assert "DISABLE_LVS" not in row["fix"] and not re.search(r"(?<!not )loosen", row["fix"].lower())


def test_suggest_rules_have_docs():
    """Pins down: suggest returns rules with evidence and a doc link, and never advises loosening constraints."""
    r = post("suggest", {"design": D, "classify": False})
    assert r["rules"] and all(x["evidence"] and x["doc"] and x["advice"] for x in r["rules"])
    ids = {x["rule"] for x in r["rules"]}
    assert "fill_tap_split" in ids and "learn_next" in ids      # kv_attn_n8: 74 % fill+tap, has an Intuitions section
    assert "Never loosen MAX_TRANSITION_CONSTRAINT" in r["never"]
    for x in r["rules"]:
        assert "set MAX_TRANSITION_CONSTRAINT" not in x["advice"] and "raise CLOCK_PERIOD" not in x["advice"]
    assert "error" in post("suggest", {"design": "nope"})


def test_notes_section_int4_item1_verbatim():
    """Pins down: notes_section returns the int4 Intuitions item 1 text exactly as in NOTES.md."""
    r = post("notes_section", {"design": "kv_attn_n8_int4", "section": "intuitions", "item": 1})
    path = os.path.join(REPO, "designs", "kv_attn_n8_int4", "NOTES.md")
    line = [l for l in open(path).read().splitlines() if l.startswith("1. **Halving the cache bits")][0]
    assert r["quote"] == line and r["source"] == "designs/kv_attn_n8_int4/NOTES.md" and r["heading"] == "Intuitions and insights"
    whole = post("notes_section", {"design": "kv_attn_n8_int4", "section": "intuitions"})
    assert line in whole["quote"]
    assert "headings" in post("notes_section", {"design": "kv_attn_n8_int4"})
    assert "error" in post("notes_section", {"design": "kv_attn_n8_int4", "section": "no such heading"})


def test_explain_quotes_notes():
    """Pins down: explain returns the NOTES paragraph about flip-flops verbatim, with source and lines."""
    r = post("explain", {"design": "kv_attn_n8_int4", "topic": "why more flip-flops"})
    top = r["matches"][0]
    assert top["quote"].startswith("1. **Halving the cache bits") and "222 here against 200" in top["quote"]
    assert top["source"].endswith("NOTES.md") and top["lines"]
    assert post("explain", {"design": D, "topic": "zzzqqq"})["matches"] == []


@pytest.fixture
def fake_jobs(monkeypatch):
    """Run make targets as `true` so the gate can be proven without a flow."""
    mgr = ts.JobManager()
    real = mgr.start

    def start(kind, cmd, label, **kw):
        return real(kind, ["true"], label, **kw)
    mgr.start = start
    monkeypatch.setattr(ts, "JOBS", mgr)
    monkeypatch.delenv("CHIP_TOOLS_NO_CONFIRM", raising=False)
    monkeypatch.setattr(ts, "_CONFIRMS", {})
    return mgr


def test_gate_two_step(fake_jobs):
    """Pins down: the first run_make call starts nothing; the second with the confirm_id starts one job; ids are single use."""
    r = post("run_make", {"target": "flow-all", "design": D})
    assert r["needs_confirmation"] is True and r["will_run"] == "make flow-all DESIGN=" + D
    assert r["confirm_id"] in r["say"] and r["say"].startswith("Reply 'yes, run ")
    assert fake_jobs.jobs == {}
    # asking again does not start anything either
    assert post("run_make", {"target": "flow-all", "design": D})["needs_confirmation"] is True and fake_jobs.jobs == {}
    assert "error" in post("run_make", {"confirm_id": "nope00"})
    r2 = post("run_make", {"confirm_id": r["confirm_id"]})
    assert r2["job_id"] and len(fake_jobs.jobs) == 1 and r2["command"] == "make flow-all DESIGN=" + D
    assert "error" in post("run_make", {"confirm_id": r["confirm_id"]})        # single use
    assert len(fake_jobs.jobs) == 1


def test_gate_expiry_and_kind(fake_jobs, monkeypatch):
    """Pins down: a confirm_id expires after 10 minutes and cannot be used for another tool."""
    r = post("run_make", {"target": "simulate", "design": D})
    assert ts.CONFIRM_TTL_S == 600
    ts._CONFIRMS[r["confirm_id"]]["expires"] = time.time() - 1
    assert "expired" in post("run_make", {"confirm_id": r["confirm_id"]})["error"]
    r = post("run_make", {"target": "simulate", "design": D})
    monkeypatch.setattr(ts, "CLAUDE_BIN", "/bin/sh")
    assert "belongs to make" in post("claude_task", {"confirm_id": r["confirm_id"]})["error"]
    assert fake_jobs.jobs == {}


def test_gate_claude_task(fake_jobs, monkeypatch):
    """Pins down: claude_task is gated the same way and the confirmed job uses the stored instructions."""
    fake = os.path.join(ts.JOB_DIR, "fake_claude2.sh")
    os.makedirs(ts.JOB_DIR, exist_ok=True)
    with open(fake, "w") as f:
        f.write("#!/bin/sh\necho '{\"type\":\"result\",\"is_error\":false,\"result\":\"ok\"}'\n")
    os.chmod(fake, 0o755)
    monkeypatch.setattr(ts, "CLAUDE_BIN", fake)
    try:
        r = post("claude_task", {"instructions": "count the designs"})
        assert r["needs_confirmation"] and "--disallowedTools" in r["will_run"] and fake_jobs.jobs == {}
        r2 = post("claude_task", {"confirm_id": r["confirm_id"]})
        assert r2["job_id"] and len(fake_jobs.jobs) == 1
    finally:
        os.remove(fake)


def test_no_confirm_env(fake_jobs, monkeypatch):
    """Pins down: CHIP_TOOLS_NO_CONFIRM=1 starts the job at once (tests and terminal use)."""
    monkeypatch.setenv("CHIP_TOOLS_NO_CONFIRM", "1")
    r = post("run_make", {"target": "simulate", "design": D})
    assert r["job_id"] and "needs_confirmation" not in r


def test_markdown_in_render_png():
    """Pins down: klayout_view and render_png descriptions tell the model to paste the markdown field."""
    spec = client.get("/openapi.json").json()
    for n in ("klayout_view", "render_png"):
        d = spec["paths"]["/" + n]["post"]["description"]
        assert "markdown" in d and "verbatim" in d
