#!/usr/bin/env python3
"""make_hermes_context.py -- generate .hermes.md, the one context file Hermes Agent reads from this repo (it takes the FIRST of
.hermes.md > AGENTS.override.md > AGENTS.md > CLAUDE.md, so .hermes.md replaces CLAUDE.md for Hermes; Claude Code keeps CLAUDE.md).
Docs: docs/HERMES_AGENT_INTEGRATION.md, build/agent/tool_eval_PLAN.md (item D)

Sources, so it cannot drift: the design list, flow, rules and style come from tools/prompts/master_prompt.txt (generated from the
repo); the safety rules are the HARD RULES section of CLAUDE.md, condensed to its bullet lines; the tool routing block below is
hand-written for the curated MCP tools in tools/hermes_tools.json (the check fails if a tool it names is not in that file).
  python3 scripts/docs/make_hermes_context.py            write .hermes.md, print size (4 chars/token)
  python3 scripts/docs/make_hermes_context.py --check    exit 1 if .hermes.md is not what the generator produces
  python3 scripts/docs/make_hermes_context.py --print    to stdout
Budget: 3,000 tokens (the 64k context of qwen3.5-64k:9b is shared with tool schemas, skills index and the conversation).
Standard library only.
"""
import json
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OUT = os.path.join(REPO, ".hermes.md")
MASTER = os.path.join(REPO, "tools", "prompts", "master_prompt.txt")
CLAUDE = os.path.join(REPO, "CLAUDE.md")
TOOLS = os.path.join(REPO, "tools", "hermes_tools.json")
BUDGET_TOKENS = 3000

ROUTING = """\
TOOL CHOICE (the MCP tools are named mcp_chip_<tool>; pick by the user's words):
- A QUESTION is never an order. "How do I ...", "How can I ...", "What command ...", "Should I ...", "Can you tell me ..." -> answer in words
  with the command text (make flow-all DESIGN=<d>, make test, make simulate DESIGN=<d>) and call NO run tool.
- ORDERS to run ("Run make simulate for X", "Run the full flow for X", "Run the experiment soc-kv") -> run_make / run_experiment. The first
  call starts NOTHING: it returns confirm_id and `say`. Show `say` and STOP: end your turn and wait for the user. Only when the user's
  NEXT message is "yes, run <id>" call confirm_run with that id and nothing else (do not call run_make again). You may never confirm on
  your own, invent an id or reuse one; the hook blocks it and the run never starts.
- "Use the <name> tool ..." -> call the tool named <name>. "Ask Claude ..." -> ask_claude (confirm gate, only on request).
- Numbers of one design (cells, area, slack, DRC) -> read_metrics (keys design__instance__count__stdcell = cells, timing__setup__ws = WORST setup slack). Several designs, "which is biggest/slowest" -> compare_designs.
  Clean / signoff status -> signoff_summary. ALWAYS call a docs tool before answering a Why / How-does / What-limits / what-fixed question about the repo: rag_answer (whole repo), explain (one design), search_docs (keywords, commands, rules); never answer those from memory. Section of a design page -> notes_section.
- Last run result -> run_summary; why a stage failed -> diagnose; what to improve -> suggest; log lines or errors -> read_log / log_digest.
- Live windows ("open X in klayout / magic", "show only met1", "zoom to ...", "close the window") -> gui_command with the user's sentence as text
  (open_gds for "open the GDS"). Call it AT ONCE with the user's words: partial names ("kv_attn", "vision lit", "kv attention 16") are
  resolved by the server, which says which design it opened. Never search the disk for a GDS; there is no other way to find one. A PICTURE in the chat ("show X met1 lower-left 50 um", "render X") -> klayout_view, paste its `markdown` field as given.
- Experiments: list_experiments; results of one -> experiment_result {id}; OpenROAD heat maps -> engine_pictures {design, view}; demos -> demo_steps {name}.
- Skills: list or text of a skill -> your skills tools (skills_list, skill_view); the plan for a task -> skill_plan {skill, design}.
- Is it local / does it send data -> proof_local. What context or files did you use -> show_context. What can you do -> capability_map. Remember a note -> remember; "what did I tell you / my notes" -> recall (not session_search). Which files did you read -> show_context.
- A made-up design name returns the valid list: say the design does not exist and name two valid ones; never invent numbers.
- Leave optional arguments out; never write "-", "none" or "string". Pass the design as the user wrote it if unsure; a name that matches nothing returns the valid list.
- "What can I type" / fast commands -> tell the user about /chip (instant: /klayout X, /magic X, /timing X, /drc X, /run synth X, /rebuild X, /loop signoff kv, /harness facts kv).
- Off-topic (poems, general knowledge) or unknown facts (tapeout yield, price, schedule): call no tool and say so in one sentence.
- Answer short. Quote tool results; show a `markdown`/`say` field verbatim. A number you did not get from a tool is unknown.
"""


def _master_blocks():
    lines = open(MASTER, encoding="utf-8").read().splitlines()
    heads = ("OFF-TOPIC RULE", "WHAT:", "DESIGNS (exact names):", "FLOW", "SAFETY:", "STYLE:")
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith(heads):
            out.append(lines[i])
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^[A-Z][A-Z -]+[ (:]", lines[i]):
                out.append(lines[i])
                i += 1
        else:
            i += 1
    return "\n".join(out)


def _hard_rules():
    t = open(CLAUDE, encoding="utf-8").read()
    m = re.search(r"^## HARD RULES\n(.*?)(?=^## )", t, re.M | re.S)
    lines = [l.rstrip() for l in (m.group(1) if m else "").splitlines() if l.strip()]
    return "\n".join(lines).replace("/Users/", "<home>/")      # the structure check forbids absolute home paths in committed files


def build():
    parts = ["# open-ai-chip: context for Hermes Agent (generated by scripts/docs/make_hermes_context.py; do not edit)",
             "You are the read-and-run assistant of this repo. You never edit files. Claude Code reads CLAUDE.md; you read this file.",
             _master_blocks(), "HARD RULES (from CLAUDE.md):\n" + _hard_rules(), ROUTING]
    return "\n\n".join(parts) + "\n"


def check_tools():
    """Tools named in ROUTING that are neither in hermes_tools.json include/aliases nor plain words."""
    cur = json.load(open(TOOLS, encoding="utf-8"))
    known = set(cur.get("include", {})) | set(cur.get("aliases", {}))
    claimed = set(re.findall(r"\b(read_metrics|compare_designs|signoff_summary|search_docs|explain|notes_section|run_summary|diagnose|"
                             r"suggest|read_log|log_digest|gui_command|open_gds|klayout_view|list_experiments|experiment_result|"
                             r"engine_pictures|demo_steps|skill_plan|proof_local|show_context|"
                             r"capability_map|run_make|run_experiment|confirm_run|ask_claude)\b", ROUTING))
    return sorted(claimed - known)


def main():
    text = build()
    est = len(text) // 4
    if est > BUDGET_TOKENS:
        print("over budget: ~%d tokens > %d" % (est, BUDGET_TOKENS), file=sys.stderr)
        return 1
    if "--print" in sys.argv:
        sys.stdout.write(text)
        return 0
    if "--check" in sys.argv:
        cur = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if cur != text:
            print(".hermes.md is stale: run python3 scripts/docs/make_hermes_context.py", file=sys.stderr)
            return 1
        missing = check_tools() if os.path.exists(TOOLS) else []
        if missing:
            print("tools named in the routing block but not in tools/hermes_tools.json: %s" % ", ".join(missing), file=sys.stderr)
            return 1
        print(".hermes.md up to date (~%d tokens)" % est)
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote .hermes.md: %d chars, ~%d tokens" % (len(text), est))
    return 0


if __name__ == "__main__":
    sys.exit(main())
