"""Hermes Agent plugin for open-ai-chip: slash commands and a per-turn router that need no model turn.

Installed by scripts/hermes_agent_setup.sh as a symlink <profile home>/plugins/open-ai-chip -> this directory, enabled with
plugins.enabled: [open-ai-chip]. It is a thin client: every command is answered by the repo tool server's POST /quick
(examples/hermes_desktop/tool_server/quick_tools.py) on 127.0.0.1, which this plugin starts if it is not running.
  slash commands  /chip (help) /designs /metrics /synth /timing /drc /lvs /signoff /compare /klayout /magic /gds /layout /png
                  /log /notes /ask /sim /run /rebuild /jobs /job: typed by the user, answered in about a second.
  pre_llm_call    for a plain message: opens a layout at once ("open kv_attn in klayout"), completes the user's own
                  "yes, run <id>", issues the confirm id for "run the flow for X", or adds the facts for a number question;
                  the model then only writes a short reply.
Standard library only (it runs in Hermes's own Python). Never reads secrets, never edits repo files.
Docs: hermes-agents.md, docs/HERMES_AGENT_INTEGRATION.md
Tests: tests/tools/test_quick_tools.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PORT = int(os.environ.get("CHIP_TOOLS_PORT", "8770"))
BASE = "http://127.0.0.1:%d" % PORT
COMMANDS = {
    "chip": "open-ai-chip command list",
    "designs": "every design with cells and signoff verdict",
    "metrics": "key numbers of a design: /metrics kv_attn",
    "synth": "synthesis: cell classes, top cells, checks: /synth vision lit",
    "timing": "setup/hold slack per corner, skew, slew: /timing kv8",
    "drc": "DRC counts (add 'live' to run it in KLayout): /drc kv_attn",
    "lvs": "LVS counts and netgen's verdict: /lvs prec bf16",
    "signoff": "the whole signoff verdict: /signoff caravel kv",
    "compare": "side by side: /compare kv4 kv8 kv16",
    "klayout": "open in a KLayout window: /klayout kv_attn show only met1",
    "magic": "open in Magic: /magic vision lit",
    "gds": "open the KLayout desktop app with sky130 colours: /gds kv_attn",
    "layout": "drive the open window: /layout zoom to the lower-left 50 um",
    "png": "a picture of the layout in the chat: /png kv8",
    "log": "log digest of the last run: /log kv8",
    "notes": "a section of the design page: /notes kv8 intuitions",
    "ask": "answer with quotes from the repo docs: /ask why does kv_attn_n8_int4 have more flip-flops?",
    "sim": "start the RTL simulation now: /sim kv8",
    "run": "start a make target now: /run synth vision_block (simulate, synth, check, gl, gl-final, flow-all)",
    "rebuild": "full flow on a fresh unchanged copy (frozen design untouched): /rebuild kv8",
    "search": "search the repo docs: /search hold violation wrapper",
    "close": "close the KLayout/Magic windows: /close (all) | /close klayout | /close magic",
    "experiments": "list the repo's experiments",
    "experiment": "start an experiment now: /experiment soc-kv | /experiment kv-cache kv8",
    "result": "result of an experiment or what-if: /result soc-kv | /result <tag>",
    "whatif": "the flow on a copy with changed settings: /whatif vision_block PL_TARGET_DENSITY_PCT=60",
    "whatifs": "list the what-if copies",
    "params": "tunable settings of a design: /params vision_block [KEY]",
    "jobs": "running and finished jobs",
    "demos": "list the demo cards",
    "demo": "a demo card with the commands to type: /demo layout | /demo 3",
    "pick": "answer a 'Which design? 1) ... 2) ...' question: /pick 2 (or the name)",
    "loopdemo": "loop-engineering demos: /loopdemo signoff kv | /loopdemo layers kv8 | /loopdemo sim vision lit",
    "harness": "harness-engineering demos: /harness names | /harness facts kv",
    "job": "status and log tail of a job: /job <id>",
}


def _up(timeout=1.0):
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _ensure_server(wait_s=40):
    if _up():
        return True
    py = REPO / "build" / "agent" / "venv" / "bin" / "python"
    srv = REPO / "examples" / "hermes_desktop" / "tool_server" / "tool_server.py"
    if not py.exists() or not srv.exists():
        return False
    log = open(REPO / "build" / "agent" / "hermes_plugin_toolserver.log", "ab")
    # Hermes runs in its own Python: its PYTHON*/VIRTUAL_ENV variables would make the repo venv import Hermes's packages
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON") and k not in ("VIRTUAL_ENV", "__PYVENV_LAUNCHER__")}
    env["CHIP_TOOLS_PORT"] = str(PORT)
    proc = subprocess.Popen([str(py), str(srv)], cwd=str(REPO), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                            start_new_session=True, env=env)
    t = time.time()
    while time.time() - t < wait_s:
        if _up(0.5):
            return True
        if proc.poll() is not None:          # crashed: fail now, not after wait_s
            return False
        time.sleep(0.25)
    return False


def _quick(body, timeout=600):
    if not _ensure_server():
        return {"handled": False, "reply": "the open-ai-chip tool server is not running and could not be started "
                                            "(build/agent/hermes_plugin_toolserver.log)"}
    req = urllib.request.Request(BASE + "/quick", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return {"handled": False, "reply": "open-ai-chip tool server error: %s" % e}


def _command(name):
    def handler(raw_args=""):
        return _quick({"cmd": name, "args": raw_args or ""}).get("reply", "")
    return handler


_HANDLED = {}          # session_id -> the reply computed in code for this turn


def _transform_llm_output(response_text=None, session_id=None, **kwargs):
    """Replace the model's text with the reply computed in code, so a handled request is shown exactly (numbers, citations)."""
    reply = _HANDLED.pop(str(session_id or ""), None)
    return reply if reply else None


def _pre_llm_call(user_message=None, session_id=None, **kwargs):
    text = user_message if isinstance(user_message, str) else ""
    if not text.strip() or len(text) > 600:
        return None
    head, _, rest = text.strip().partition(" ")
    if head.startswith("/"):
        if head[1:].lower() not in COMMANDS:     # a Hermes or skill command: not ours
            return None
        # our command reached the model (a surface that does not dispatch plugin commands, e.g. `hermes -z`): answer it here
        r = _quick({"cmd": head[1:].lower(), "args": rest})
    else:
        r = _quick({"text": text}, timeout=120)
    if r.get("handled") and r.get("reply"):
        # the model only has to end the turn; transform_llm_output then puts the exact reply in its place (no paraphrase)
        _HANDLED[str(session_id or "")] = r["reply"]
        return {"context": "[open-ai-chip fast path: this request was ALREADY handled in code before you, and its result will be "
                           "shown to the user automatically. Call NO tool. Reply with exactly one word: Done]"}
    if r.get("context"):
        return {"context": r["context"]}
    return None


def register(ctx):
    for name, desc in COMMANDS.items():
        ctx.register_command(name, _command(name), description=desc)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("transform_llm_output", _transform_llm_output)
