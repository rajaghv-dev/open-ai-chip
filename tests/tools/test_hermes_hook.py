# Docs: docs/HERMES_AGENT_INTEGRATION.md
"""Tests for scripts/hermes/hooks/pre_tool_call.py (Hermes Agent v0.21.5 shell-hook protocol).

The hook is run as a subprocess exactly as Hermes runs it: JSON on stdin, JSON on stdout, exit 0.
No Hermes, Docker or network needed. A temporary repo stub provides scripts/flow/frozen.py for the freeze cases.
Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_hermes_hook.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REAL_REPO = Path(__file__).resolve().parents[2]
HOOK = REAL_REPO / "scripts" / "hermes" / "hooks" / "pre_tool_call.py"


@pytest.fixture()
def env(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts" / "flow").mkdir(parents=True)
    (repo / "designs" / "frozen_d").mkdir(parents=True)
    (repo / "designs" / "live_d").mkdir(parents=True)
    (repo / "scripts" / "flow" / "frozen.py").write_text(
        "def is_frozen(path):\n    return 'frozen_d' in str(path)\n\ndef frozen_designs():\n    return ['frozen_d']\n")
    log = tmp_path / "hook.log"
    return {"repo": repo, "log": log,
            "env": dict(os.environ, CHIP_REPO=str(repo), CHIP_HOOK_LOG=str(log))}


def run(env, tool, args, event="pre_tool_call", raw=None):
    payload = {"hook_event_name": event, "tool_name": tool, "tool_input": args, "session_id": "s1",
               "cwd": str(env["repo"]), "profile": "chip", "extra": {}}
    p = subprocess.run([sys.executable, str(HOOK)], input=raw if raw is not None else json.dumps(payload),
                       capture_output=True, text=True, env=env["env"], timeout=20)
    assert p.returncode == 0, p.stderr
    data = json.loads(p.stdout)
    assert isinstance(data, dict)
    return data


def verdict(data):
    return data.get("action", "allow")


T = "terminal"
TABLE = [
    # (tool, args, expected verdict, text expected in the message)
    ("write_file", {"path": "designs/live_d/config.json", "content": "{}"}, "block", "may not edit"),
    ("patch", {"path": "README.md", "old_string": "a", "new_string": "b"}, "block", "may not edit"),
    ("write_file", {"path": "designs/live_d/config.json", "content": '"CLOCK_PERIOD": 50'}, "block", "CLOCK_PERIOD"),
    ("write_file", {"path": "designs/frozen_d/rtl/a.v", "content": "x"}, "block", "frozen"),
    ("skill_manage", {"action": "create"}, "block", "not available"),
    ("execute_code", {"code": "1"}, "block", "not available"),
    ("read_file", {"path": "designs/live_d/config.json"}, "allow", ""),
    ("read_file", {"path": "/home/x/.hermes/.env"}, "block", "secret"),
    ("read_file", {"path": "/home/x/.hermes/auth.json"}, "block", "secret"),
    ("search_files", {"path": "/home/x/.hermes/pairing", "pattern": "a"}, "block", "secret"),
    (T, {"command": "cf push"}, "block", "owner-only"),
    (T, {"command": "cf login"}, "block", "owner-only"),
    (T, {"command": "git push origin main"}, "block", "git push"),
    (T, {"command": "git commit -am x"}, "block", "owner-only"),
    (T, {"command": "git status"}, "allow", ""),
    (T, {"command": "rm -rf build"}, "block", "rm"),
    (T, {"command": "ls designs && rm -rf /"}, "block", "rm"),
    (T, {"command": "docker run --rm hello"}, "block", "docker"),
    (T, {"command": "docker ps"}, "allow", ""),
    (T, {"command": "make gds DESIGN=live_d"}, "block", "run_make"),
    (T, {"command": "make flow-all DESIGN=live_d"}, "block", "run_make"),
    (T, {"command": "make precheck"}, "block", "run_make"),
    (T, {"command": "make gds DESIGN=frozen_d"}, "block", "frozen"),
    (T, {"command": "make test"}, "allow", ""),
    (T, {"command": "make doctor"}, "allow", ""),
    (T, {"command": "make generate"}, "block", "writes repo"),
    (T, {"command": "make table"}, "block", "writes repo"),
    (T, {"command": "make mystery"}, "approve", "not on the read-only list"),
    (T, {"command": "make gds CLOCK_PERIOD=10"}, "block", "CLOCK_PERIOD"),
    (T, {"command": "echo DISABLE_LVS=1"}, "block", "DISABLE_LVS"),
    (T, {"command": "sed -i 's/25/50/' designs/live_d/config.json"}, "block", "may not edit"),
    (T, {"command": "echo x > designs/live_d/rtl/a.v"}, "block", "redirection"),
    (T, {"command": "echo x > build/scratch.txt"}, "allow", ""),
    (T, {"command": "bash -c 'cf push'"}, "block", "owner-only"),
    (T, {"command": "cat designs/live_d/config.json"}, "allow", ""),
    (T, {"command": "grep CLOCK_PERIOD designs/live_d/config.json"}, "allow", ""),
    (T, {"command": "cat ~/.hermes/.env"}, "block", "secret"),
    (T, {"command": "cp x ../open-ai-silicon/y"}, "block", "open-ai-silicon"),
    (T, {"command": "gh repo edit --visibility public"}, "block", "visibility"),
    ("mcp_chip_run_make", {"target": "gds", "design": "live_d"}, "approve", "physical flow"),
    ("mcp_chip_run_make", {"target": "flow-all", "design": "live_d"}, "approve", "physical flow"),
    ("mcp_chip_run_make", {"target": "gds", "design": "frozen_d"}, "block", "frozen"),
    ("mcp_chip_run_make", {"target": "test"}, "allow", ""),
    ("mcp_chip_run_make", {"target": "doctor"}, "allow", ""),
    ("mcp_chip_run_make", {"target": "clean"}, "block", "allow-list"),
    ("mcp_chip_whatif_run", {"design": "live_d", "changes": {"PL_TARGET_DENSITY_PCT": 60}, "tag": "t"}, "approve", "physical"),
    ("mcp_chip_whatif_run", {"design": "live_d", "changes": {"DISABLE_LVS": True}, "tag": "t"}, "block", "DISABLE_LVS"),
    ("mcp_chip_whatif_sweep", {"design": "frozen_d", "key": "k", "values": [1]}, "approve", "copy"),
    ("mcp_chip_run_experiment", {"id": "x"}, "approve", "physical"),
    ("mcp_chip_claude_task", {"instructions": "edit"}, "block", "not available"),
    ("mcp_chip_ask_claude", {"instructions": "why slack?"}, "allow", ""),
    ("mcp_chip_claude_status", {"job_id": "1"}, "allow", ""),
    ("mcp_chip_run_summary", {"design": "live_d"}, "allow", ""),
    ("mcp_chip_read_metrics", {"design": "live_d"}, "allow", ""),
    ("mcp_chip_job_status", {"job_id": "1"}, "allow", ""),
    ("mcp_chip_eda_list_designs", {}, "allow", ""),
    ("mcp__grafana__search_dashboards", {"query": "chip"}, "allow", ""),
    ("mcp__grafana__get_dashboard_panel_queries", {"uid": "chip-overview"}, "allow", ""),
    ("mcp__grafana__update_dashboard", {"dashboard": {}}, "block", "grafana read tools"),
    ("mcp__grafana__grafana_api_request", {"path": "/api/ds/query"}, "block", "grafana read tools"),
]


@pytest.mark.parametrize("tool,args,expected,text", TABLE, ids=[f"{i}-{t[0]}" for i, t in enumerate(TABLE)])
def test_decision_table(env, tool, args, expected, text):
    data = run(env, tool, args)
    assert verdict(data) == expected, data
    if expected != "allow":
        assert text.lower() in data["message"].lower(), data
    else:
        assert data == {}


def test_protocol_shape(env):
    for tool, args in (("write_file", {"path": "a"}), ("mcp_chip_run_make", {"target": "gds"}), ("read_file", {"path": "a"})):
        d = run(env, tool, args)
        assert set(d) <= {"action", "message"}
        if d:
            assert d["action"] in ("block", "approve") and isinstance(d["message"], str) and d["message"]


def test_other_event_is_noop(env):
    assert run(env, "write_file", {"path": "a"}, event="post_tool_call") == {}


def test_bad_input_fails_closed(env):
    assert verdict(run(env, None, None, raw="not json")) == "block"
    assert verdict(run(env, None, None, raw="[1]")) == "block"


def test_log_written_without_content(env):
    run(env, "write_file", {"path": "designs/live_d/a.v", "content": "SECRETCONTENT123"})
    run(env, "read_file", {"path": "x"})
    lines = [json.loads(x) for x in env["log"].read_text().splitlines()]
    assert [x["decision"] for x in lines] == ["block", "allow"]
    assert all({"ts", "tool", "decision", "reason", "summary", "session"} <= set(x) for x in lines)
    assert "SECRETCONTENT123" not in env["log"].read_text()


def test_works_without_frozen_module(env):
    (env["repo"] / "scripts" / "flow" / "frozen.py").unlink()
    assert verdict(run(env, "mcp_chip_run_make", {"target": "gds", "design": "frozen_d"})) == "approve"


def test_real_repo_default_paths():
    # without CHIP_REPO the hook must resolve the repo from its own location and still answer
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_name": "write_file", "tool_input": {"path": "x"}}),
                       capture_output=True, text=True, env=dict(os.environ, CHIP_HOOK_LOG=os.devnull), timeout=20)
    assert json.loads(p.stdout)["action"] == "block"


# ---- confirm guard wired into the hook (scripts/hermes/hooks/confirm_guard.py): the model may not confirm its own run
def _state_db(home, msgs, profile="chip"):
    import sqlite3
    d = home / "profiles" / profile
    d.mkdir(parents=True)
    con = sqlite3.connect(str(d / "state.db"))
    con.execute("create table messages (id integer primary key autoincrement, session_id text, role text, content text)")
    for role, text in msgs:
        con.execute("insert into messages (session_id, role, content) values ('s1', ?, ?)", (role, text))
    con.commit()
    con.close()


def test_guard_blocks_model_self_confirm(env, tmp_path):
    _state_db(tmp_path / "hh", [("user", "Run make simulate for vision_block"), ("assistant", "reply yes, run ab12cd")])
    env["env"]["HERMES_HOME"] = str(tmp_path / "hh")
    for tool, args in (("mcp_chip_run_make", {"target": "simulate", "confirm_id": "ab12cd"}),
                       ("mcp_chip_confirm_run", {"confirm_id": "ab12cd"}),
                       ("mcp_chip_ask_claude", {"confirm_id": "ab12cd"})):
        d = run(env, tool, args)
        assert verdict(d) == "block" and "yes, run ab12cd" in d["message"]


def test_guard_allows_user_confirm(env, tmp_path):
    _state_db(tmp_path / "hh", [("user", "Run make simulate"), ("assistant", "ok"), ("user", "yes, run ab12cd")])
    env["env"]["HERMES_HOME"] = str(tmp_path / "hh")
    assert run(env, "mcp_chip_confirm_run", {"confirm_id": "ab12cd"}) == {}
    assert run(env, "mcp_chip_run_make", {"target": "simulate", "confirm_id": "ab12cd"}) == {}


def test_guard_fails_closed_without_state_db(env, tmp_path):
    env["env"]["HERMES_HOME"] = str(tmp_path / "empty")
    assert verdict(run(env, "mcp_chip_confirm_run", {"confirm_id": "ab12cd"})) == "block"
    assert verdict(run(env, "mcp_chip_run_make", {"target": "simulate", "confirm_id": "ab12cd"})) == "block"
    # the harmless first step (no confirm_id) and other tools are not affected
    assert verdict(run(env, "mcp_chip_run_make", {"target": "simulate"})) == "allow"
    assert verdict(run(env, "mcp_chip_read_metrics", {"design": "x"})) == "allow"


def test_double_underscore_mcp_names(env):
    # Hermes v0.21.5 names MCP tools mcp__chip__<tool> (measured in build/agent/hermes_hook.log): the policy must match that spelling too
    assert verdict(run(env, "mcp__chip__run_make", {"target": "gds", "design": "live_d"})) == "approve"
    assert verdict(run(env, "mcp__chip__run_make", {"target": "gds", "design": "frozen_d"})) == "block"
    assert verdict(run(env, "mcp__chip__run_make", {"target": "rm -rf"})) == "block"
    assert verdict(run(env, "mcp__chip__whatif_run", {"design": "live_d", "changes": {}})) == "approve"
    assert verdict(run(env, "mcp__chip__claude_task", {})) == "block"
    assert verdict(run(env, "mcp__chip__read_metrics", {"design": "live_d"})) == "allow"
