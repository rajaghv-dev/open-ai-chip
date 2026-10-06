#!/usr/bin/env python3
"""Confirm guard for Hermes Agent: a gated tool call that carries a confirm_id is allowed only if the USER's latest message in the
same session says "yes, run <that id>". The model must not confirm its own run.
Why: measured 2026-10-07 in the tool-calling eval (case r01, Hermes chip profile, qwen3.5-64k:9b): after the first run_make call returned
confirm_id, the model called run_make again with that id in the same turn and the job started without the user ever answering. The tool
server's gate (single-use id, 10 minutes) cannot see who wrote the id, so the check lives where the conversation is visible: this hook
reads the session's messages from Hermes' own state.db (read-only, sqlite) under <HERMES_HOME>/profiles/<profile>/state.db (or
<HERMES_HOME>/state.db for the default profile). Messages are read only to match the id; nothing is logged but the decision.

  check(payload) -> None to allow, else a block message.   Library use: from confirm_guard import check
  CLI (hook wrapper): python3 confirm_guard.py  < hook payload JSON  -> prints {"action": "block", "message": ...} or {} ; exit 0
Gated tools: mcp__chip__ run_make run_experiment whatif_run whatif_sweep ask_claude claude_task confirm_run. A call WITHOUT confirm_id
(the first, harmless step that only returns an id) is always allowed. Fail closed: unreadable state.db with a confirm_id -> block.
Stdlib only. Tests: tests/tools/test_hermes_confirm_guard.py. Docs: build/agent/tool_eval_PLAN.md (item E)
"""
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

GATED = {"run_make", "run_experiment", "whatif_run", "whatif_sweep", "ask_claude", "claude_task", "confirm_run"}
ID_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{6}(?![0-9a-f])")


def tool_base(name):
    m = re.match(r"^mcp[_]+[A-Za-z0-9-]+?[_]+(.+)$", str(name or ""))
    return m.group(1) if m else str(name or "")


def db_path(payload, env=None):
    env = env if env is not None else os.environ
    home = Path(env.get("HERMES_HOME") or Path.home() / ".hermes")
    prof = payload.get("profile")
    cands = []
    if prof and prof != "default":
        cands.append(home / "profiles" / str(prof) / "state.db")
    cands.append(home / "state.db")
    for c in cands:
        if c.exists():
            return c
    return None


def last_user_message(path, session_id):
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=2)
    try:
        row = con.execute("select content from messages where session_id=? and role='user' order by id desc limit 1", (session_id,)).fetchone()
    finally:
        con.close()
    return row[0] if row and row[0] else ""


def check(payload, env=None):
    name = tool_base(payload.get("tool_name"))
    ti = payload.get("tool_input") or {}
    if name not in GATED or not isinstance(ti, dict):
        return None
    cid = str(ti.get("confirm_id") or "").strip().lower()
    if not cid and name != "confirm_run":
        return None
    m = ID_RE.search(cid)
    cid = m.group(0) if m else cid
    sid = payload.get("session_id")
    try:
        path = db_path(payload, env)
        msg = last_user_message(path, sid) if path and sid else None
    except (sqlite3.Error, OSError):
        msg = None
    if msg is None:
        return "cannot verify that the user confirmed (session messages unreadable): ask the user to reply 'yes, run %s'" % (cid or "<id>")
    if re.search(r"\byes\b[,;:.!]?\s+run\s+%s\b" % re.escape(cid), msg.lower()) if cid else False:
        return None
    return ("the user has not written 'yes, run %s' in their latest message. Show the confirm text (`say`) to the user and STOP; "
            "never confirm a run yourself" % (cid or "<id>"))


def main():
    try:
        payload = json.load(sys.stdin)
        msg = check(payload)
    except Exception as e:  # noqa: BLE001  (fail closed)
        msg = "confirm guard error: %s" % e
    print(json.dumps({"action": "block", "message": msg}) if msg else "{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
