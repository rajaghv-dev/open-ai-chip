#!/usr/bin/env python3
"""Install the skill slash prompts and the memory filter into a running Open WebUI (WEBUI_AUTH=False). Idempotent.
Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md, docs/SKILLS.md
Tests: tests/tools/test_skills_memory.py, tests/tools/test_proof.py, tests/tools/test_logs_open.py
One prompt per project skill (and /experiments, /demo, /demo-<name> for the demos): /harden, /soc-run, /wrapper, /notes, /precision, /add-engine (Prompts API:
POST /api/v1/prompts/create, update by id; checked against the installed open_webui routers/prompts.py). Each prompt tells
the model to call skill_plan and follow the steps. Also installs memory_filter.py as a global filter function
(Functions API: /api/v1/functions/create, /id/<id>/update, toggle, toggle/global), which appends the memory digest to the
system prompt, and receipt_filter.py as a second global filter (receipt under every answer, repo passages as citations; see
docs/HERMES_DESKTOP.md "Proof: local, repo, context"). Run after Open WebUI is up (start.sh: see README "start.sh hook"). Standard library only.
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
RECEIPT_ID = "chip_receipt"

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
        if d.get("runner") and not d.get("steps"):
            text = ("Call the demo_steps tool with name %s and tell me the terminal command it returns: this demo opens real "
                    "windows and is started from a terminal." % d["name"])
        else:
            text = ("Call the demo_steps tool with name %s. Then do its steps one at a time: say the narration, call the step's "
                    "tool with its args, show the result and what to look for. A step that needs confirmation shows the confirm "
                    "text and waits for my 'yes, run <id>' before run_make. Say when the demo is done." % d["name"])
        out["/demo-%s" % d["name"]] = ("Demo %d: %s" % (d["num"], d["title"]), "chip-demo", text)
    return out


# The repo-integration prompt library (every capability of the tool server). One row per slash prompt:
# (command, name, group, usage, what you get, example, tools called, prompt text). {{x}} = Open WebUI input the user fills in.
# capability_tools.py (the capability_map tool and /help) reads CHIP_PROMPTS; tests/tools/test_logs_open.py checks that every
# tool named here is a mounted operation. The prompt texts say "Call the X tool" because the 8B model routes explicit asks best.
_V = " Show the `markdown` field of the result verbatim."
CHIP_PROMPTS = [
    ("/help", "Help: what can I ask", "Ask", "/help", "a table: what you can ask, an example, the tool", "/help", ["capability_map"],
     "Call the capability_map tool and show its `markdown` field verbatim. Do not add anything."),
    ("/designs", "List the designs", "Ask", "/designs", "every design with family and one headline number", "/designs", ["list_designs"],
     "Call the list_designs tool and show the designs grouped by family, one line each."),
    ("/metrics", "Metrics of a design", "Ask", "/metrics <design>", "cells, area, slack, power, DRC of one design from metrics.json", "/metrics kv_attn_n8", ["read_metrics"],
     "Call the read_metrics tool for design {{design}} with pattern design__instance__count__stdcell, then again with pattern design__instance__area and "
     "with pattern setup__ws. Give the numbers with units and name the file (designs/{{design}}/output/metrics.json)."),
    ("/compare", "Compare a metric", "Ask", "/compare <metric>", "one metric across all designs, sorted", "/compare design__instance__count__stdcell", ["compare_designs"],
     "Call the compare_designs tool with metric {{metric}} (exact metrics.json key: cells = design__instance__count__stdcell, area = design__instance__area, "
     "power = power__total, setup slack = timing__setup__ws). Show the table sorted, name the largest and the smallest."),
    ("/why", "Why: explain a design topic", "Ask", "/why <design> <topic>", "the quoted NOTES.md paragraphs with file, heading and lines", "/why kv_attn_n8_int4 flip-flops", ["explain", "search_docs"],
     "Call the explain tool with design {{design}} and topic {{topic}}. Quote the text it returns with its file and heading. If it finds nothing, "
     "call search_docs with the same words as the query and quote the best passage."),
    ("/run", "Run a make target (asks first)", "Run", "/run <target> <design>", "starts a make job after your 'yes, run <id>'", "/run simulate vision_block", ["run_make", "job_status"],
     "Call the run_make tool with target {{target}} and design {{design}}. It returns a confirm_id: show the will_run text and wait for me to reply "
     "'yes, run <id>'. Start nothing before that. After my yes, call run_make with the confirm_id, then job_status until done."),
    ("/status", "Status of jobs", "Run", "/status", "running and finished jobs with state, seconds and the last log lines", "/status", ["job_list", "job_status"],
     "Call the job_list tool. For a running job also call job_status with its job_id and show the last lines. Say clearly if nothing is running."),
    ("/summary", "Summary of a run", "Analyse", "/summary <design>", "stages, key numbers, current-or-stale, diff vs committed metrics", "/summary kv_attn_n8", ["run_summary"],
     "Call the run_summary tool for design {{design}} and give the plain-English summary: stages, key numbers, whether the run is current or stale."),
    ("/diagnose", "Diagnose a failed run", "Analyse", "/diagnose <design>", "the failure matched to the harden-design table, with evidence and fix", "/diagnose vision_block", ["diagnose"],
     "Call the diagnose tool for design {{design}}. Show the matched failure, its evidence, the fix and the doc link. Say if nothing failed."),
    ("/suggest", "Suggestions for a design", "Analyse", "/suggest <design>", "rule-based advice (never loosens a constraint)", "/suggest kv_attn_n8", ["suggest"],
     "Call the suggest tool for design {{design}}. List the suggestions. You never apply them; say what I should do."),
    ("/log", "Read a log", "Logs", "/log <design> [which]", "the tail of flow, error, warning, a stage, a step, sim, gl or a job log", "/log kv_attn_n8 error", ["read_log", "list_logs"],
     "Call the read_log tool with design {{design}} and which {{which}} (flow, error, warning, make, stages, stage:<name>, step:<NN or name>, sim, gl or job:<id>; "
     "use flow if I left it empty). Show the `markdown` field verbatim. If it is empty, say the log is empty."),
    ("/log-errors", "Errors and slow steps of a run", "Logs", "/log-errors <design>", "errors and warnings grouped by step, the slowest steps, stage outcomes", "/log-errors kv_attn_n8", ["log_digest"],
     "Call the log_digest tool for design {{design}}." + _V + " Then say in one sentence whether the run had a real failure (error.log empty or not)."),
    ("/open-gds", "Open the layout", "Layout", "/open-gds <design> [viewer]", "opens the GDS: klayout-app (full KLayout), klayout, magic or png", "/open-gds kv_attn_n8 klayout-app", ["open_gds"],
     "Call the open_gds tool with design {{design}} and viewer {{viewer}} (klayout-app, klayout, magic or png; use klayout-app if I left it empty)."
     " Say which window opened. Do not ask for confirmation: opening a viewer is read-only."),
    ("/klayout", "Operate KLayout by text", "Layout", "/klayout <sentence>", "opens and drives the real KLayout window from one plain sentence (no model routing)", "/klayout open kv_attn_n8 and show only met1 and met2", ["gui_command"],
     "Call the gui_command tool with tool klayout and text {{sentence}} exactly as I wrote it (do not rewrite it)." + _V +
     " If it says understood false, show its hint. Do not ask for confirmation: the windows are read-only."),
    ("/magic", "Operate Magic by text", "Layout", "/magic <sentence>", "opens and drives the real Magic window (DRC, find, measure) from one plain sentence", "/magic open kv_attn_n8 and run drc", ["gui_command"],
     "Call the gui_command tool with tool magic and text {{sentence}} exactly as I wrote it (do not rewrite it)." + _V +
     " If it says understood false, show its hint. Also state drc_errors when the result has it. Do not ask for confirmation: read-only."),
    ("/open", "Open a repo file", "Logs", "/open <file>", "the text of a doc, NOTES, report or json with a line range", "/open designs/kv_attn_n8/NOTES.md", ["open_file"],
     "Call the open_file tool with path {{file}}." + _V),
    ("/skills", "List the skills", "Skills", "/skills", "the six project skills with one line each", "/skills", ["list_skills"],
     "Call the list_skills tool and show each skill with its one-line description."),
    ("/skill", "Plan with a skill", "Skills", "/skill <name> [design]", "the ordered steps of a skill with the tool per step", "/skill harden-design vision_block", ["skill_plan"],
     "Call the skill_plan tool with skill {{skill}} and design {{design}}. Show the numbered steps and the tool for each. Do step 1 only and wait for my go."),
    ("/param", "Explain a setting", "Analyse", "/param <design> [key]", "current value, default, engine, meaning, safe range, rule and doc link of a synthesis, timing or OpenROAD setting", "/param vision_block PL_TARGET_DENSITY_PCT", ["param_info"],
     "Call the param_info tool with design {{design}} and key {{key}} (leave key out if I left it empty: then show the tunable keys by engine)."
     " Give the current value and where it comes from, the meaning, the safe range, the rule that applies and the doc link. You change nothing."),
    ("/whatif", "What-if on a copy (asks first)", "Run", "/whatif <design> <KEY=value>", "checks the rules, shows the patch text, runs the flow on a COPY after your 'yes, run <id>', compares with the committed metrics", "/whatif vision_block CLOCK_PERIOD=20", ["propose_change", "whatif_run", "job_status", "whatif_result"],
     "Call the propose_change tool with design {{design}} and changes {{change}} (a JSON object such as {\"CLOCK_PERIOD\": 20}). Show each key as allowed or blocked with the rule. "
     "If a key is blocked, say which HARD RULE and stop. If all are allowed, show the patch text and say it is never applied. Then call whatif_run with design, changes and tag {{tag}}: "
     "it returns a confirm_id: show the plan and wait for me to reply 'yes, run <id>'. Start nothing before that. After my yes call whatif_run with the confirm_id, then job_status until done, "
     "then whatif_result with the tag and show its `markdown` field verbatim, the verdict, and that designs/ was not touched."),
    ("/sweep", "Sweep one setting (asks first)", "Run", "/sweep <design> <KEY> <v1,v2,...>", "one what-if per value, run in sequence on copies after your 'yes, run <id>', one comparison table", "/sweep vision_block PL_TARGET_DENSITY_PCT 50,60,70", ["whatif_sweep", "job_status", "whatif_result"],
     "Call the whatif_sweep tool with design {{design}}, key {{key}} and values {{values}} (a JSON list of 2 to 6 numbers). It returns a confirm_id and a plan: show the plan, name any blocked value with its rule, "
     "and wait for me to reply 'yes, run <id>'. Start nothing before that. After my yes call whatif_sweep with the confirm_id, then job_status until done, then whatif_result with the sweep_id and show its `markdown` field verbatim."),
    ("/remember", "Remember a note", "Memory", "/remember <note>", "saves one short note in local memory", "/remember I prefer kv_attn_n8 for demos", ["remember"],
     "Call the remember tool with note {{note}}. Confirm the note id."),
    ("/recall", "Recall notes and past runs", "Memory", "/recall [query]", "the newest notes and runs, or those matching a word", "/recall kv", ["recall"],
     "Call the recall tool with query {{query}} (empty = newest). List the matches with their ids."),
    ("/experiments", "Experiments (menu)", "Experiments", "/experiments", "everything the repo can run, with expected time", "/experiments", ["list_experiments", "run_experiment"], None),
    ("/demo", "Demos (menu)", "Experiments", "/demo [n]", "the numbered demos, then step by step", "/demo 2", ["list_demos", "demo_steps"], None),
    ("/proof", "Proof: is this local", "Proof", "/proof", "sockets, model, offline settings, outbound connections, verdict", "/proof", ["proof_local"],
     "Call the proof_local tool and show the verdict and the status line, then the evidence in a short list."),
    ("/context", "Context of the last turn", "Proof", "/context", "what the model was sent: system prompt, tools prompt, memory digest, passages", "/context", ["show_context"],
     "Call the show_context tool and show what the model was sent for the last turn (sha256 values included)."),
]


def chip_prompt_forms():
    """Payloads of the repo-integration prompts that carry their own text (/experiments and /demo come from demo_prompts)."""
    return [{"command": c, "name": n, "content": t, "tags": ["chip-repo", g.lower()], "commit_message": "install_prompts.py", "is_production": True}
            for c, n, g, u, d, e, tools, t in CHIP_PROMPTS if t]


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


def receipt_form():
    src = open(os.path.join(HERE, "receipt_filter.py"), encoding="utf-8").read()
    return {"id": RECEIPT_ID, "name": "Chip agent receipt", "content": src,
            "meta": {"description": "Appends a code-built receipt (model, repo commit, files read with sha256, context, speed) and citations under every answer."}}


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
    for f in prompt_forms() + demo_forms() + chip_prompt_forms():
        old = have.get(f["command"].lstrip("/"))
        if old:
            f2 = dict(f, command=old["command"])      # keep whichever form (with or without '/') the server stores
            call("/api/v1/prompts/id/%s/update" % old["id"], f2, token)
            print("prompt updated:", f["command"])
        else:
            call("/api/v1/prompts/create", f, token)
            print("prompt created:", f["command"])


def install_filter(token, f=None):
    f = f or filter_form()
    fid = f["id"]
    try:
        cur = call("/api/v1/functions/id/" + fid, None, token)
    except urllib.error.HTTPError:
        cur = None
    if cur:
        cur = call("/api/v1/functions/id/%s/update" % fid, f, token)
        print("filter updated:", f["name"])
    else:
        cur = call("/api/v1/functions/create", f, token)
        print("filter created:", f["name"])
    if not cur.get("is_active"):
        cur = call("/api/v1/functions/id/%s/toggle" % fid, {}, token)
    if not cur.get("is_global"):
        call("/api/v1/functions/id/%s/toggle/global" % fid, {}, token)
    print("filter active and global:", f["name"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-filter", action="store_true", help="skip the memory digest and receipt filters")
    ap.add_argument("--dry-run", action="store_true", help="print the payload commands, change nothing")
    a = ap.parse_args(argv)
    if a.dry_run:
        for f in prompt_forms() + demo_forms() + chip_prompt_forms():
            print(f["command"], "->", f["name"])
        return 0
    token = call("/api/v1/auths/signin", {"email": "admin@localhost", "password": "x"})["token"]
    install_prompts(token)
    if not a.no_filter:
        install_filter(token)
        install_filter(token, receipt_form())
    return 0


if __name__ == "__main__":
    sys.exit(main())
