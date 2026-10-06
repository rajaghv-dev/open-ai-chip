#!/usr/bin/env python3
"""capability_map for the Hermes tool server (a FastAPI APIRouter, auto-mounted by tool_server.py): the answer to /help.

capability_map {group?}: generated from the mounted operations (operationId, summary, first docstring sentence, read from the app's
OpenAPI) and the slash prompt library (examples/hermes_desktop/install_prompts.py CHIP_PROMPTS, PROMPTS, demo_prompts). Groups: Ask,
Run, Analyse, Logs, Layout/GUI, Skills, Memory, Experiments/Demos, Proof (an operation not listed in GROUPS lands in "Other", so a new
tool is never hidden). The reply has `groups` (data), `prompts` (what you can ask -> example -> tool), `tools` and a `markdown` table.
Read-only. Does not import tool_server.py (it reads the running app through the request).
Docs: docs/HERMES_DESKTOP.md ("Everything you can ask"), examples/hermes_desktop/tool_server/README.md
Tests: tests/tools/test_logs_open.py
"""
import importlib.util
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.abspath(os.path.join(HERE, ".."))
router = APIRouter()

ORDER = ["Ask", "Run", "Analyse", "Logs", "Layout/GUI", "Skills", "Memory", "Experiments/Demos", "Proof", "Other"]
# operationId -> group (anything missing is "Other")
GROUPS: Dict[str, str] = {}
for _g, _names in {
    "Ask": "list_designs read_metrics compare_designs signoff_summary precheck_summary search_docs rag_answer rag_search rag_index notes_section explain capability_map "
           "context_brief layout_summary layer_stats find_pins classify_slew health param_info",
    "Run": "run_make job_status job_list job_cancel claude_task whatif_run whatif_sweep whatif_clean",
    "Analyse": "run_summary diagnose suggest propose_change whatif_result whatif_list",
    "Logs": "list_logs read_log log_digest open_file",
    "Layout/GUI": "klayout_view render_png open_gds gui_start gui_stop gui_status gui_command gui_examples klayout_live magic_live",
    "Skills": "list_skills get_skill skill_plan",
    "Memory": "remember recall forget memory_digest",
    "Experiments/Demos": "list_experiments run_experiment experiment_result engine_pictures list_demos demo_steps",
    "Proof": "proof_local show_context call_log",
}.items():
    for _n in _names.split():
        GROUPS[_n] = _g
PROMPT_GROUP = {"Layout": "Layout/GUI", "Experiments": "Experiments/Demos"}
EXTRA_PROMPT_GROUP = {"/klayout": "Layout/GUI", "/magic": "Layout/GUI", "/harden": "Skills", "/soc-run": "Skills", "/wrapper": "Skills",
                      "/notes": "Skills", "/precision": "Skills", "/add-engine": "Skills"}


def _install_prompts():
    spec = importlib.util.spec_from_file_location("chip_install_prompts", os.path.join(DESKTOP, "install_prompts.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def prompt_rows() -> List[Dict[str, Any]]:
    """Every installed slash prompt: command, group, usage, what you get, example, tools. Library rows carry their own metadata;
    the others (skill prompts, /demo-<name>, /klayout, /magic, ...) get their group, tool and name from the text."""
    ip = _install_prompts()
    rows = [{"command": c, "name": n, "group": PROMPT_GROUP.get(g, g), "usage": u, "desc": d, "example": e, "tools": list(tools)}
            for c, n, g, u, d, e, tools, _t in ip.CHIP_PROMPTS]
    have = {r["command"] for r in rows}
    texts = {}
    try:
        texts.update({c: (n, t) for c, (n, _s, t) in ip.PROMPTS.items()})
        texts.update({c: (n, t) for c, (n, _tag, t) in ip.demo_prompts().items()})
    except Exception:  # noqa: BLE001
        pass
    for c, (n, t) in texts.items():
        if c in have:
            continue
        tools = re.findall(r"\b([a-z][a-z0-9]*_[a-z0-9_]+)\b tool", t) or re.findall(r"Use the (\w+) tool", t)
        rows.append({"command": c, "name": n, "group": EXTRA_PROMPT_GROUP.get(c, "Experiments/Demos" if c.startswith("/demo") else "Other"),
                     "usage": c, "desc": n, "example": c, "tools": sorted(set(tools))})
    return rows


def operations(app) -> List[Dict[str, str]]:
    """(operationId, summary, first sentence of the description) of every mounted, visible operation."""
    out = []
    for path, item in app.openapi().get("paths", {}).items():
        for meth, op in item.items():
            if meth.lower() != "post" or "operationId" not in op:
                continue
            desc = re.sub(r"\s+", " ", (op.get("description") or "")).strip()
            first = re.split(r"(?<=[.!?])\s", desc, 1)[0] if desc else ""
            out.append({"tool": op["operationId"], "path": path, "summary": op.get("summary", ""), "about": first[:200]})
    return sorted(out, key=lambda o: o["tool"])


def build(app, group: Optional[str] = None) -> Dict[str, Any]:
    ops = operations(app)
    for o in ops:
        o["group"] = GROUPS.get(o["tool"], "Other")
    prompts = prompt_rows()
    names = {o["tool"] for o in ops}
    by_group: Dict[str, Dict[str, Any]] = {g: {"tools": [], "prompts": []} for g in ORDER}
    for o in ops:
        by_group[o["group"]]["tools"].append(o)
    for p in prompts:
        p["group"] = p["group"] if p["group"] in by_group else "Other"
        p["tools_ok"] = all(t in names for t in p["tools"])
        by_group[p["group"]]["prompts"].append(p)
    want = None
    if group:
        g = group.strip().lower()
        want = next((x for x in ORDER if x.lower() == g or x.lower().startswith(g)), None)
    lines: List[str] = []
    for g in ORDER:
        if want and g != want:
            continue
        d = by_group[g]
        if not d["tools"] and not d["prompts"]:
            continue
        lines.append("### %s" % g)
        if d["prompts"]:
            lines += ["", "| what you can ask | example | tool |", "|---|---|---|"]
            for p in d["prompts"]:
                lines.append("| `%s` %s | `%s` | %s |" % (p["usage"], p["desc"], p["example"], ", ".join(p["tools"]) or "-"))
        lines += ["", "Tools: " + ", ".join("`%s`" % o["tool"] for o in d["tools"]), ""]
    md = "\n".join(lines).strip()
    return {"tools_count": len(ops), "prompts_count": len(prompts), "groups": {g: {"tools": [o["tool"] for o in by_group[g]["tools"]],
            "prompts": [p["command"] for p in by_group[g]["prompts"]]} for g in ORDER if by_group[g]["tools"] or by_group[g]["prompts"]},
            "prompts": prompts, "tools": ops, "markdown": md}


class CapReq(BaseModel):
    group: Optional[str] = Field(None, description="optional: Ask, Run, Analyse, Logs, Layout, Skills, Memory, Experiments, Proof")


@router.post("/capability_map", operation_id="capability_map", summary="What you can ask: every prompt, example and tool", response_model=None)
def capability_map(request: Request, req: CapReq = CapReq()) -> dict:
    """The capability map of the agent (answer to /help): every slash prompt with an example and the tool it calls, and every tool,
    grouped Ask, Run, Analyse, Logs, Layout/GUI, Skills, Memory, Experiments/Demos, Proof. Show the `markdown` field verbatim."""
    r = build(request.app, req.group)
    return {"tools_count": r["tools_count"], "prompts_count": r["prompts_count"], "groups": r["groups"], "markdown": r["markdown"]}
