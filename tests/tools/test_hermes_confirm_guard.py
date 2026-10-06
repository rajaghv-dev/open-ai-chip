"""pytest tests/tools/test_hermes_confirm_guard.py: scripts/hermes/hooks/confirm_guard.py blocks a gated tool call that carries a
confirm_id unless the user's latest message in that session says "yes, run <id>" (read from a temp sqlite copy of Hermes' state.db schema).
Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_hermes_confirm_guard.py
Pass: self-confirmation blocked, user confirmation allowed, first step and non-gated tools untouched, unreadable db blocks, CLI prints JSON.
Docs: build/agent/tool_eval_PLAN.md (item E)"""
import json
import os
import sqlite3
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HOOKS = os.path.join(REPO, "scripts", "hermes", "hooks")
sys.path.insert(0, HOOKS)
import confirm_guard as g  # noqa: E402


def _home(tmp_path, msgs):
    p = tmp_path / "profiles" / "chip"
    p.mkdir(parents=True)
    con = sqlite3.connect(str(p / "state.db"))
    con.execute("create table messages (id integer primary key autoincrement, session_id text, role text, content text)")
    for sid, role, text in msgs:
        con.execute("insert into messages (session_id, role, content) values (?,?,?)", (sid, role, text))
    con.commit()
    con.close()
    return {"HERMES_HOME": str(tmp_path)}


def P(tool, ti, sid="s1"):
    return {"tool_name": tool, "tool_input": ti, "session_id": sid, "profile": "chip"}


def test_blocks_self_confirmation(tmp_path):
    env = _home(tmp_path, [("s1", "user", "Run make simulate for vision_block"), ("s1", "assistant", "Reply 'yes, run ab12cd'")])
    msg = g.check(P("mcp__chip__run_make", {"target": "simulate", "confirm_id": "ab12cd"}), env)
    assert msg and "has not written 'yes, run ab12cd'" in msg


def test_allows_user_confirmation(tmp_path):
    env = _home(tmp_path, [("s1", "user", "Run make simulate"), ("s1", "assistant", "..."), ("s1", "user", "Yes, run AB12CD")])
    assert g.check(P("mcp__chip__run_make", {"confirm_id": "ab12cd"}), env) is None
    assert g.check(P("mcp__chip__confirm_run", {"confirm_id": "ab12cd"}), env) is None
    assert g.check(P("mcp__chip__ask_claude", {"confirm_id": "ab12cd"}), env) is None


def test_other_id_or_old_message_blocked(tmp_path):
    env = _home(tmp_path, [("s1", "user", "yes, run ab12cd"), ("s1", "user", "thanks, now show metrics")])
    assert g.check(P("mcp__chip__run_make", {"confirm_id": "ab12cd"}), env)           # only the LATEST user message counts
    env = _home(tmp_path / "x", [("s1", "user", "yes, run ab12cd")])
    assert g.check(P("mcp__chip__run_make", {"confirm_id": "ffffff"}), env)
    assert g.check(P("mcp__chip__run_make", {"confirm_id": "ab12cd"}, sid="other"), env)    # another session


def test_untouched_calls(tmp_path):
    env = _home(tmp_path, [("s1", "user", "Run make simulate")])
    assert g.check(P("mcp__chip__run_make", {"target": "simulate"}), env) is None           # first step: returns an id only
    assert g.check(P("mcp__chip__read_metrics", {"design": "kv_attn_n8"}), env) is None
    assert g.check(P("read_file", {"path": "x"}), env) is None
    assert g.check(P("mcp__chip__confirm_run", {}), env)                                     # confirm_run always needs the user's yes


def test_fail_closed_when_db_missing(tmp_path):
    msg = g.check(P("mcp__chip__run_make", {"confirm_id": "ab12cd"}), {"HERMES_HOME": str(tmp_path / "nope")})
    assert msg and "cannot verify" in msg


def test_cli(tmp_path):
    env = dict(os.environ, **_home(tmp_path, [("s1", "user", "hello")]))
    r = subprocess.run([sys.executable, os.path.join(HOOKS, "confirm_guard.py")], input=json.dumps(P("mcp__chip__run_make", {"confirm_id": "ab12cd"})),
                       capture_output=True, text=True, env=env)
    assert json.loads(r.stdout)["action"] == "block" and r.returncode == 0
    r = subprocess.run([sys.executable, os.path.join(HOOKS, "confirm_guard.py")], input=json.dumps(P("mcp__chip__list_designs", {})),
                       capture_output=True, text=True, env=env)
    assert json.loads(r.stdout) == {}
