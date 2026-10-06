"""Scripted showcase of the Hermes chip agent through Open WebUI's API, using the "Hermes chip agent" preset.
Each step is one chat turn (tools are routed by Hermes 3 8B, one Ollama job at a time). The conversation is saved as a chat in
Open WebUI ("Hermes chip agent demo ...": scroll and continue it in the UI) and written to examples/hermes_desktop/demo_transcript.md.
A step whose tool is not served by the running tool server is reported SKIPPED (not exercised), never faked.
Usage: build/agent/venv/bin/python examples/hermes_desktop/demo.py [--only substring] [--timeout S]
Exit: 0 if every exercised step called its expected tool, 1 otherwise, 2 Open WebUI unreachable.
Docs: docs/HERMES_DESKTOP.md, examples/hermes_desktop/README.md"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("WEBUI_URL", "http://127.0.0.1:8080")
TOOLS = os.environ.get("TOOLS_URL", "http://127.0.0.1:8770")
PRESET = "hermes-chip-agent"
D = "kv_attn_n8"

# (label, prompt, expected tools (any of), tools that must exist on the server for the step to run)
STEPS = [
    ("context", "Call context_brief and tell me in two lines what you know about this repo and my notes.", ["context_brief"], ["context_brief"]),
    ("query", "How many standard cells does %s have?" % D, ["read_metrics"], ["read_metrics"]),
    ("run (ask)", "Run make simulate for vision_block", ["run_make"], ["run_make"]),
    ("run (confirm)", "yes, run {confirm_id}", ["run_make"], ["run_make"]),
    ("run summary", "Use the run_summary tool for %s and summarize its result." % D, ["run_summary"], ["run_summary"]),
    ("suggestions", "Use the suggest tool for %s and list the suggestions." % D, ["suggest"], ["suggest"]),
    ("skill plan", "Use the skill_plan tool for the harden-design skill and design %s; list the steps." % D, ["skill_plan"], ["skill_plan"]),
    ("memory note", "Use the remember tool to save this note: hermes demo ran on %s" % time.strftime("%Y-%m-%d"), ["remember"], ["remember"]),
    ("GUI start", "Use gui_start to start the GUI session", ["gui_start"], ["gui_start"]),
    ("KLayout", "Use klayout_live to open %s in KLayout" % D, ["klayout_live"], ["gui_start", "klayout_live"]),
    ("Magic", "Use magic_live to open %s in Magic" % D, ["magic_live"], ["gui_start", "magic_live"]),
    ("GUI stop", "Use gui_stop to stop the GUI session", ["gui_stop"], ["gui_stop"]),
]


def api(path, token=None, body=None, timeout=60):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def served_tools():
    with urllib.request.urlopen(TOOLS + "/openapi.json", timeout=10) as r:
        spec = json.load(r)
    return {op.get("operationId") for p in spec["paths"].values() for op in p.values() if isinstance(op, dict)}


def used_tools(text):
    return re.findall(r'name="server:chip/([A-Za-z0-9_]+)"', text)


LOG = os.path.join(HERE, "..", "..", "build", "webui", "logs", "tool_server.log")


def log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def tools_since(pos):
    """Tool calls the server actually received (POST /<tool> in its access log) since byte offset pos."""
    try:
        with open(LOG, "rb") as f:
            f.seek(pos)
            return re.findall(r'"POST /([a-z_]+) HTTP', f.read().decode("utf-8", "replace"))
    except OSError:
        return []


def ask(token, content, timeout):
    d = api("/api/chat/completions", token, {"model": PRESET, "stream": False, "tool_ids": ["server:chip"],
            "messages": [{"role": "user", "content": content}]}, timeout=timeout)
    return d["choices"][0]["message"]["content"]


def save_chat(token, turns):
    """turns: [(user_text, assistant_text)] -> one chat in Open WebUI. Returns its id."""
    msgs, order, parent = {}, [], None
    for u, a in turns:
        for role, text in (("user", u), ("assistant", a)):
            mid = str(uuid.uuid4())
            m = {"id": mid, "parentId": parent, "childrenIds": [], "role": role, "content": text,
                 "timestamp": int(time.time())}
            if role == "user":
                m["models"] = [PRESET]
            else:
                m.update({"model": PRESET, "modelName": "Hermes chip agent", "done": True})
            if parent:
                msgs[parent]["childrenIds"].append(mid)
            msgs[mid] = m
            order.append(m)
            parent = mid
    chat = {"title": "Hermes chip agent demo " + time.strftime("%Y-%m-%d %H:%M"), "models": [PRESET],
            "history": {"messages": msgs, "currentId": parent}, "messages": order, "tags": [],
            "timestamp": int(time.time() * 1000)}
    return api("/api/v1/chats/new", token, {"chat": chat})["id"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default="", help="run only steps whose label contains this text")
    ap.add_argument("--timeout", type=int, default=420, help="seconds per step")
    a = ap.parse_args()
    try:
        token = api("/api/v1/auths/signin", body={"email": "admin@localhost", "password": "x"})["token"]
        have = served_tools()
    except OSError as e:
        print("Open WebUI or the tool server is not reachable (%s): run scripts/hermes.sh first" % e, file=sys.stderr)
        return 2
    turns, results, confirm_id, started = [], [], None, time.time()
    for label, prompt, expect, needs in STEPS:
        if a.only and a.only not in label:
            continue
        missing = [t for t in needs if t not in have]
        if missing:
            results.append((label, "SKIPPED", "tool not served: " + ",".join(missing), 0))
            print("%-14s SKIPPED (tool not served: %s)" % (label, ",".join(missing)))
            continue
        if "{confirm_id}" in prompt:
            if not confirm_id:
                results.append((label, "SKIPPED", "no confirm_id from the previous step", 0))
                print("%-14s SKIPPED (no confirm_id)" % label)
                continue
            prompt = prompt.format(confirm_id=confirm_id)
        t0, pos = time.time(), log_size()
        try:
            text = ask(token, prompt, a.timeout)
        except Exception as e:  # noqa: BLE001
            text = "(request failed: %s)" % e
        dt = time.time() - t0
        tools = list(dict.fromkeys(tools_since(pos) + used_tools(text)))
        m = re.search(r'confirm_id"?\s*[:=]\s*"?([0-9a-f]{6})\b', text)
        if label == "run (ask)":
            m = m or re.search(r"yes, run ([0-9a-f]{6})\b", text)
            confirm_id = m.group(1) if m else None
        ok = any(t in tools for t in expect)
        results.append((label, "ok" if ok else "FAIL", "tools called: " + (",".join(tools) or "none"), dt))
        print("%-14s %s  %.0fs  tools: %s" % (label, "ok" if ok else "FAIL", dt, ",".join(tools) or "none"))
        turns.append((prompt, text))
    chat_id = save_chat(token, turns) if turns else None
    out = os.path.join(HERE, "demo_transcript.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("# Hermes chip agent demo transcript\n\nGenerated by `scripts/hermes.sh demo` (examples/hermes_desktop/demo.py), "
                "model preset `%s` (hermes3:8b via Ollama), %s. Saved as a chat in Open WebUI (id %s). "
                "Answers are the model's own output with tool results inline.\n\n## Result\n\n| step | status | detail | seconds |\n|---|---|---|---|\n"
                % (PRESET, time.strftime("%Y-%m-%d"), chat_id))
        for label, st, det, dt in results:
            f.write("| %s | %s | %s | %.0f |\n" % (label, st, det, dt))
        f.write("\n")
        for (u, t) in turns:
            f.write("## You\n\n%s\n\n## Hermes\n\n%s\n\n" % (u, t.strip()))
    print("chat saved in Open WebUI: %s/c/%s" % (BASE, chat_id))
    print("transcript: examples/hermes_desktop/demo_transcript.md (%.0f s total)" % (time.time() - started))
    return 1 if any(r[1] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
