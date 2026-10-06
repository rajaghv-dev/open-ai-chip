#!/usr/bin/env python3
"""Run one of the Hermes chip agent demos: a narrated, paced sequence of acts. No argument needed beyond the demo.
  python3 examples/hermes_desktop/demos.py              numbered menu
  python3 examples/hermes_desktop/demos.py 2            the same as: demos.py kv
  python3 examples/hermes_desktop/demos.py kv [--pace 6] [--yes] [--direct]
Numbers and names: 1 precision, 2 kv, 3 rtl2gds, 4 int4, 5 heatmaps, 6 soc, 7 gui, 8 proof (definitions: demo_defs.py).
Each act prints a numbered narration line, waits --pace seconds (default 6) so a presenter can explain, then runs the act:
 - chat mode (Open WebUI is up and serves the experiment tools): each act is one chat turn to the preset "Hermes chip agent"
   (legacy function calling, as docs/HERMES_DESKTOP.md); the conversation is saved as an Open WebUI chat and a transcript
   examples/hermes_desktop/demos/<name>.md; each act is checked against the tool the tool server actually received.
 - direct mode (--direct, or Open WebUI not running): the same tool calls go straight to the tool server (started here on
   127.0.0.1 if it is not running, stopped again at the end); transcript written the same way. For the chat version run
   one command: bash examples/hermes_desktop/start.sh
Runs need confirmation: a run act shows the confirm step, then asks "Start it? [y/N]" (or pass --yes). A physical flow
(only demo 3, rtl2gds) says so first. Demo 7 (gui) is run by demos/gui_demo.py and demo 8 (proof) by demos/proof_demo.py (the --pace value is passed on).
Exit: 0 all acts ok, 1 an act failed, 2 services unreachable. Standard library only.
Docs: docs/HERMES_DESKTOP.md (section "Experiments and demos"), examples/hermes_desktop/README.md
Tests: tests/tools/test_experiments.py
"""
import argparse
import atexit
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import demo_defs  # noqa: E402

WEBUI = os.environ.get("WEBUI_URL", "http://127.0.0.1:8080")
PORT = os.environ.get("CHIP_TOOLS_PORT", "8770")
OUT_DIR = os.path.join(HERE, "demos")
_PIDS = []


def http(url, body=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def tool_ops(base):
    """operationIds served at base, or None when unreachable."""
    try:
        spec = http(base + "/openapi.json", timeout=5)
    except Exception:  # noqa: BLE001
        return None
    return {op.get("operationId") for p in spec["paths"].values() for op in p.values() if isinstance(op, dict)}


def _stop_pids():
    for p in _PIDS:
        try:
            os.kill(p, signal.SIGTERM)
        except OSError:
            pass


atexit.register(_stop_pids)


def start_tool_server(port):
    """Start the tool server on 127.0.0.1:port with the venv python; returns its base URL or None."""
    py = os.path.join(REPO, "build", "agent", "venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    env = dict(os.environ, CHIP_TOOLS_PORT=str(port))
    os.makedirs(os.path.join(REPO, "build", "webui", "logs"), exist_ok=True)
    log = open(os.path.join(REPO, "build", "webui", "logs", "demo_tool_server.log"), "ab")
    p = subprocess.Popen([py, os.path.join(HERE, "tool_server", "tool_server.py")], cwd=REPO, env=env, stdout=log, stderr=log,
                         start_new_session=True)
    _PIDS.append(p.pid)
    base = "http://127.0.0.1:%s" % port
    for _ in range(60):
        if tool_ops(base):
            return base
        if p.poll() is not None:
            return None
        time.sleep(0.5)
    return None


def ensure_tools(need):
    """(base_url, note). Uses the running tool server if it serves `need`, else starts a private one."""
    base = "http://127.0.0.1:%s" % PORT
    ops = tool_ops(base)
    if ops is None:
        b = start_tool_server(PORT)
        return (b, "started a tool server for this demo on port %s" % PORT) if b else (None, "tool server could not start (build/webui/logs/demo_tool_server.log)")
    if need <= ops:
        return base, "using the running tool server on port %s" % PORT
    port2 = str(int(PORT) + 40)
    b = start_tool_server(port2)
    return (b, "the tool server on %s lacks %s (restart it: bash examples/hermes_desktop/stop.sh && bash examples/hermes_desktop/start.sh); "
            "started a private one on %s" % (PORT, ",".join(sorted(need - ops)), port2)) if b else (None, "no usable tool server")


def render(res):
    """A tool result as readable text: the parts a human wants first, then the rest compactly."""
    if not isinstance(res, dict):
        return json.dumps(res)[:1500]
    parts = []
    for k in ("error", "needs_confirmation", "will_run", "say", "summary", "prefill_table", "decode_table", "table", "lesson", "quote",
              "markdown", "explanation", "run_summary"):
        if k in res and res[k] not in (None, "", False):
            v = res[k]
            if k == "run_summary" and isinstance(v, dict):
                v = v.get("summary") or json.dumps(v)[:800]
            parts.append("%s: %s" % (k, v) if k in ("error", "will_run", "say", "needs_confirmation") else str(v))
    for m in (res.get("matches") or [])[:2]:
        if isinstance(m, dict) and m.get("markdown"):
            parts.append(m["markdown"])
    for h in (res.get("hits") or [])[:2]:
        if isinstance(h, dict):
            parts.append("%s > %s: %s" % (h.get("file"), h.get("heading"), str(h.get("text") or h.get("snippet") or "")[:300]))
    if not parts:
        parts.append(json.dumps(res)[:1500])
    return "\n".join(parts)


def confirm(prompt, yes):
    if yes:
        print("%s -> yes (--yes)" % prompt)
        return True
    if not sys.stdin.isatty():
        print("%s -> no (not a terminal; pass --yes to start runs)" % prompt)
        return False
    return input(prompt + " [y/N] ").strip().lower() in ("y", "yes")


def wait_job(base, jid, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = http(base + "/job_status", {"job_id": jid})
        if r.get("state") in ("done", "failed") or r.get("error"):
            return r
        time.sleep(2)
    return {"state": "timeout"}


def narrate(i, n, text, pace):
    print("\n=== Act %d/%d: %s" % (i, n, text), flush=True)
    if pace > 0:
        time.sleep(pace)


# ---------------------------------------------------------------- direct mode
def run_direct(d, base, pace, yes):
    steps, results, jid = d["steps"], [], None
    for i, s in enumerate(steps, 1):
        narrate(i, len(steps), s["say"], pace)
        args = dict(s["args"])
        if jid and s["tool"] in ("experiment_result", "run_summary") and "job_id" not in args:
            args["job_id"] = jid
        t0 = time.time()
        try:
            res = http(base + "/" + s["tool"], args, timeout=300)
        except Exception as e:  # noqa: BLE001
            res = {"error": str(e)}
        ok = "error" not in res or bool(res.get("needs_confirmation"))
        text = render(res)
        if s.get("confirm") and res.get("needs_confirmation"):
            print(text)
            if d["physical"]:
                print("NOTE: this is a physical flow (Docker), about %g min." % d["minutes"])
            if confirm("Start it? (the confirm_id is %s)" % res["confirm_id"], yes):
                r2 = http(base + "/run_make", {"confirm_id": res["confirm_id"]})
                jid = r2.get("job_id")
                text += "\n\nconfirmed: yes, run %s -> %s" % (res["confirm_id"], json.dumps(r2)[:300])
                if jid:
                    st = wait_job(base, jid)
                    text += "\njob %s %s rc %s after %s s" % (jid, st.get("state"), st.get("rc"), st.get("seconds"))
                    ok = st.get("state") == "done"
                else:
                    ok = False
            else:
                text += "\n(not started; later acts use the committed results)"
                ok = True
        print(text)
        results.append((s, "ok" if ok else "FAIL", s["tool"], time.time() - t0, text))
    return results


# ---------------------------------------------------------------- chat mode
def run_chat(d, base, pace, yes, timeout):
    import demo  # the existing scripted showcase: api(), ask(), tools_since(), used_tools() against Open WebUI
    demo.TOOLS = base
    token = demo.api("/api/v1/auths/signin", body={"email": "admin@localhost", "password": "x"})["token"]
    steps, results, turns, jid, cid = d["steps"], [], [], None, None
    for i, s in enumerate(steps, 1):
        narrate(i, len(steps), s["say"], pace)
        ask = s["ask"].replace("{job}", " and job_id %s" % jid if jid else "").replace("{job_ref}", " (job_id %s)" % jid if jid else "")
        t0, pos = time.time(), demo.log_size()
        before = {j["job_id"] for j in http(base + "/job_list", {}).get("jobs", [])}
        try:
            text = demo.ask(token, ask, timeout)
        except Exception as e:  # noqa: BLE001
            text = "(request failed: %s)" % e
        if s.get("confirm") and not re.search(r'confirm_id"?\s*[:=]\s*"?[0-9a-f]{6}\b|yes,? run [0-9a-f]{6}\b', text):
            print("(no confirm text in the answer: asking once more)")
            try:
                text = demo.ask(token, ask, timeout)
            except Exception as e:  # noqa: BLE001
                text = "(request failed: %s)" % e
        tools = [t for t in dict.fromkeys(demo.tools_since(pos) + demo.used_tools(text)) if t not in ("job_list", "memory_digest", "job_status")]
        ok = s["tool"] in tools
        print("you: %s\nhermes: %s\n[tools the server received: %s]" % (ask, text.strip(), ",".join(tools) or "none"))
        turns.append((ask, text))
        m = re.search(r'confirm_id"?\s*[:=]\s*"?([0-9a-f]{6})\b|yes,? run ([0-9a-f]{6})\b', text)
        if s.get("confirm"):
            cid = (m.group(1) or m.group(2)) if m else None
            if not cid:
                results.append((s, "FAIL", ",".join(tools), time.time() - t0, text))
                continue
            if d["physical"]:
                print("NOTE: this is a physical flow (Docker), about %g min." % d["minutes"])
            if confirm("Start it? (confirm_id %s)" % cid, yes):
                yes_text = "yes, run %s" % cid
                try:
                    t2 = demo.ask(token, yes_text, timeout)
                except Exception as e:  # noqa: BLE001
                    t2 = "(request failed: %s)" % e
                print("you: %s\nhermes: %s" % (yes_text, t2.strip()))
                turns.append((yes_text, t2))
                time.sleep(1)
                new = [j for j in http(base + "/job_list", {}).get("jobs", []) if j["job_id"] not in before]
                if not new:                     # the 8B model sometimes answers without calling run_make: ask once more
                    print("(no job started: repeating the confirmation once)")
                    try:
                        t3 = demo.ask(token, yes_text, timeout)
                    except Exception as e:  # noqa: BLE001
                        t3 = "(request failed: %s)" % e
                    print("you: %s\nhermes: %s" % (yes_text, t3.strip()))
                    turns.append((yes_text, t3))
                    time.sleep(1)
                    new = [j for j in http(base + "/job_list", {}).get("jobs", []) if j["job_id"] not in before]
                jid = new[-1]["job_id"] if new else None
                if jid:
                    st = wait_job(base, jid)
                    print("job %s %s rc %s after %s s" % (jid, st.get("state"), st.get("rc"), st.get("seconds")))
                    ok = st.get("state") == "done"
                else:
                    ok = False
        results.append((s, "ok" if ok else "FAIL", ",".join(tools) or "none", time.time() - t0, text))
    try:
        cid = save_chat(demo, token, d, turns)
    except Exception as e:  # noqa: BLE001
        print("could not save the chat: %s" % e)
        cid = None
    return results, cid


def save_chat(demo, token, d, turns):
    """The conversation as one Open WebUI chat titled with the demo name (same structure as demo.save_chat)."""
    import uuid
    msgs, order, parent = {}, [], None
    for u, a in turns:
        for role, text in (("user", u), ("assistant", a)):
            mid = str(uuid.uuid4())
            m = {"id": mid, "parentId": parent, "childrenIds": [], "role": role, "content": text, "timestamp": int(time.time())}
            if role == "user":
                m["models"] = [demo.PRESET]
            else:
                m.update({"model": demo.PRESET, "modelName": "Hermes chip agent", "done": True})
            if parent:
                msgs[parent]["childrenIds"].append(mid)
            msgs[mid] = m
            order.append(m)
            parent = mid
    chat = {"title": "Demo %d %s: %s" % (d["num"], d["name"], d["title"]), "models": [demo.PRESET],
            "history": {"messages": msgs, "currentId": parent}, "messages": order, "tags": ["demo"],
            "timestamp": int(time.time() * 1000)}
    return demo.api("/api/v1/chats/new", token, {"chat": chat})["id"]


def write_transcript(d, mode, results, chat_id, note, secs):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, d["name"] + ".md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Demo %d: %s\n\n%s\n\nGenerated by `python3 examples/hermes_desktop/demos.py %s` on %s, mode: %s (%s). %s"
                "Total %.0f s. Physical flow: %s.\n\n" % (d["num"], d["title"], d["blurb"], d["name"], time.strftime("%Y-%m-%d"),
                                                          mode, note, ("Saved as Open WebUI chat %s. " % chat_id) if chat_id else "", secs,
                                                          "yes" if d["physical"] else "no"))
        f.write("| act | tool | status | seconds |\n|---|---|---|---|\n")
        for i, (s, st, tl, dt, _t) in enumerate(results, 1):
            f.write("| %d | %s | %s | %.0f |\n" % (i, tl, st, dt))
        for i, (s, st, tl, dt, text) in enumerate(results, 1):
            f.write("\n## Act %d: %s\n\nLook for: %s\n\n%s\n" % (i, s["say"], s["look"], text.strip()))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demo", nargs="?", help="number or name (omit for the menu); --list prints the menu too")
    ap.add_argument("--list", action="store_true", help="print the numbered menu")
    ap.add_argument("--pace", type=float, default=6.0, help="seconds to pause after each narration line (default 6; 0 = none)")
    ap.add_argument("--yes", action="store_true", help="start the runs in the demo without asking (you ordered the demo)")
    ap.add_argument("--direct", action="store_true", help="call the tool server directly, no chat model")
    ap.add_argument("--timeout", type=int, default=420, help="seconds per chat turn")
    a = ap.parse_args(argv)
    if a.list or not a.demo:
        print(demo_defs.menu())
        return 0
    d = demo_defs.find(a.demo)
    if not d:
        print("unknown demo %r\n%s" % (a.demo, demo_defs.menu()), file=sys.stderr)
        return 2
    print("Demo %d %s: %s (about %g min%s)\n%s" % (d["num"], d["name"], d["title"], d["minutes"],
                                                  ", runs a physical flow" if d["physical"] else "", d["blurb"]))
    if d.get("runner"):
        path = os.path.join(REPO, d["runner"])
        if not os.path.exists(path):
            print("the gui demo is not installed (%s missing)" % d["runner"], file=sys.stderr)
            return 2
        return subprocess.call([sys.executable, path, "--pace", str(a.pace)], cwd=REPO)
    need = {s["tool"] for s in d["steps"]}
    base, note = ensure_tools(need)
    if not base:
        print(note, file=sys.stderr)
        return 2
    print(note)
    mode, chat_id, t0 = "direct", None, time.time()
    chat_ok = False
    if not a.direct:
        try:
            http(WEBUI + "/health", timeout=3)
            import demo as _d
            tok = _d.api("/api/v1/auths/signin", body={"email": "admin@localhost", "password": "x"})["token"]
            chat_ok = _d.PRESET in json.dumps(_d.api("/api/models", tok)) and base == "http://127.0.0.1:%s" % PORT
        except Exception:  # noqa: BLE001
            chat_ok = False
        if not chat_ok:
            print("chat mode not available (Open WebUI not running or not using this tool server): running direct. "
                  "For the chat version run: bash examples/hermes_desktop/start.sh")
    if chat_ok:
        mode = "chat (Open WebUI, preset hermes-chip-agent)"
        results, chat_id = run_chat(d, base, a.pace, a.yes, a.timeout)
    else:
        results = run_direct(d, base, a.pace, a.yes)
    path = write_transcript(d, mode, results, chat_id, note, time.time() - t0)
    bad = [r for r in results if r[1] != "ok"]
    print("\n%s: %d/%d acts ok in %.0f s; transcript %s%s" % (d["name"], len(results) - len(bad), len(results), time.time() - t0,
                                                              os.path.relpath(path, REPO),
                                                              "; chat %s/c/%s" % (WEBUI, chat_id) if chat_id else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
