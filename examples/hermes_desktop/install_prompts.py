#!/usr/bin/env python3
"""Install the skill slash prompts and the memory filter into a running Open WebUI (WEBUI_AUTH=False). Idempotent.
Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md, docs/SKILLS.md
One prompt per project skill (and /experiments, /demo, /demo-<name> for the demos): /harden, /soc-run, /wrapper, /notes, /precision, /add-engine (Prompts API:
POST /api/v1/prompts/create, update by id; checked against the installed open_webui routers/prompts.py). Each prompt tells
the model to call skill_plan and follow the steps. Also installs memory_filter.py as a global filter function
(Functions API: /api/v1/functions/create, /id/<id>/update, toggle, toggle/global), which appends the memory digest to the
system prompt. Run after Open WebUI is up (start.sh: see README "start.sh hook"). Standard library only.
  python3 examples/hermes_desktop/install_prompts.py [--no-filter] [--dry-run]
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("WEBUI_URL", "http://127.0.0.1:8080")
FILTER_ID = "chip_memory_digest"

# command -> (name, skill, text). {{design}} is an Open WebUI custom input variable the user fills in when the prompt runs.
PROMPTS = {
    "/harden": ("Harden a design", "harden-design",
                "Use the skill_plan tool with skill harden-design and design {{design}}. Show the numbered steps. "
                "Then do step 1 only and wait for my confirmation before each run_make."),
    "/soc-run": ("Run the SoC simulations", "soc-run",
                 "Use the skill_plan tool with skill soc-run. Show the numbered steps and ask which target I want first "
                 "(soc-sim, soc-kv, adapter-test, caravel-rtl, caravel-gl)."),
    "/wrapper": ("Build a Caravel wrapper", "wrapper-build",
                 "Use the skill_plan tool with skill wrapper-build and design {{design}}. Show the steps. "
                 "Say which steps need file edits and must go to Claude."),
    "/notes": ("Write design notes", "write-design-notes",
               "Use the skill_plan tool with skill write-design-notes and design {{design}}. Show the plan. "
               "You do not edit files: say it must go to Claude (claude_task) or Claude Code."),
    "/precision": ("Add a precision variant", "precision-variant",
                   "Use the skill_plan tool with skill precision-variant. Show the steps for a new number format "
                   "{{format}}. You do not edit files: say it must go to Claude."),
    "/add-engine": ("Add a tiny AI engine", "add-tiny-engine",
                    "Use the skill_plan tool with skill add-tiny-engine. Show the steps for a new engine {{engine}}. "
                    "You do not edit files: say it must go to Claude."),
}


# Experiments and demos (examples/hermes_desktop/demo_defs.py is the single source of the demo list).
def demo_prompts():
    """command -> (name, tag, text) for /experiments, /demo and /demo-<name> (one per demo, by name)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("chip_demo_defs", os.path.join(HERE, "demo_defs.py"))
    dd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dd)
    out = {
        "/experiments": ("List the experiments", "chip-experiments",
                         "Call the list_experiments tool. Show the experiments grouped (design, family, system, pictures) with id, title "
                         "and expected time. Ask which one I want. Start nothing until I choose; then use run_experiment and wait "
                         "for my 'yes, run <id>' before run_make."),
        "/demo": ("Demo menu", "chip-demo",
                  "Call the list_demos tool and show the numbered menu (number, name, title, time). Ask: which demo? When I reply "
                  "with a number or a name, use the demo_steps tool with it and do its steps one at a time: say the narration, "
                  "call the tool, show the result, then go on. A step that needs confirmation waits for my 'yes, run <id>'."),
    }
    for d in dd.DEMOS:
        if d.get("runner"):
            text = ("Call the demo_steps tool with name %s and tell me the terminal command it returns: this demo opens real "
                    "windows and is started from a terminal." % d["name"])
        else:
            text = ("Call the demo_steps tool with name %s. Then do its steps one at a time: say the narration, call the step's "
                    "tool with its args, show the result and what to look for. A step that needs confirmation shows the confirm "
                    "text and waits for my 'yes, run <id>' before run_make. Say when the demo is done." % d["name"])
        out["/demo-%s" % d["name"]] = ("Demo %d: %s" % (d["num"], d["title"]), "chip-demo", text)
    return out


def prompt_forms():
    """The payloads (PromptForm) that will be created or updated; testable without a server."""
    return [{"command": c, "name": n, "content": t, "tags": ["chip-skill", s],
             "commit_message": "install_prompts.py", "is_production": True} for c, (n, s, t) in PROMPTS.items()]


def demo_forms():
    return [{"command": c, "name": n, "content": t, "tags": [tag], "commit_message": "install_prompts.py", "is_production": True}
            for c, (n, tag, t) in demo_prompts().items()]


def filter_form():
    src = open(os.path.join(HERE, "memory_filter.py"), encoding="utf-8").read()
    return {"id": FILTER_ID, "name": "Chip agent memory digest", "content": src,
            "meta": {"description": "Appends the local agent memory digest to the system prompt."}}


def call(path, body=None, token=None, method=None):
    req = urllib.request.Request(BASE + path, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def install_prompts(token):
    have = {p["command"].lstrip("/"): p for p in call("/api/v1/prompts/", None, token) or []}
    for f in prompt_forms() + demo_forms():
        old = have.get(f["command"].lstrip("/"))
        if old:
            f2 = dict(f, command=old["command"])      # keep whichever form (with or without '/') the server stores
            call("/api/v1/prompts/id/%s/update" % old["id"], f2, token)
            print("prompt updated:", f["command"])
        else:
            call("/api/v1/prompts/create", f, token)
            print("prompt created:", f["command"])


def install_filter(token):
    f = filter_form()
    try:
        cur = call("/api/v1/functions/id/" + FILTER_ID, None, token)
    except urllib.error.HTTPError:
        cur = None
    if cur:
        cur = call("/api/v1/functions/id/%s/update" % FILTER_ID, f, token)
        print("filter updated:", f["name"])
    else:
        cur = call("/api/v1/functions/create", f, token)
        print("filter created:", f["name"])
    if not cur.get("is_active"):
        cur = call("/api/v1/functions/id/%s/toggle" % FILTER_ID, {}, token)
    if not cur.get("is_global"):
        call("/api/v1/functions/id/%s/toggle/global" % FILTER_ID, {}, token)
    print("filter active and global")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-filter", action="store_true", help="skip the memory digest filter")
    ap.add_argument("--dry-run", action="store_true", help="print the payload commands, change nothing")
    a = ap.parse_args(argv)
    if a.dry_run:
        for f in prompt_forms() + demo_forms():
            print(f["command"], "->", f["name"])
        return 0
    token = call("/api/v1/auths/signin", {"email": "admin@localhost", "password": "x"})["token"]
    install_prompts(token)
    if not a.no_filter:
        install_filter(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
