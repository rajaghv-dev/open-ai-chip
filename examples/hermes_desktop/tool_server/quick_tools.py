#!/usr/bin/env python3
"""Fast paths for Hermes Agent: slash commands and a sentence router that need no model turn (auto-mounted by tool_server.py).

POST /quick {cmd, args}   a slash command typed by the USER in Hermes (/klayout kv_attn, /timing vision lit, /run flow-all kv8).
                          Answers from the committed evidence (designs/<d>/output/metrics.json and reports/) or drives the
                          existing tools (gui_command, open_gds, run_make, whatif_run, ...). A typed /run or /rebuild is the
                          user's own consent, so it starts at once (still one physical flow at a time, frozen designs never
                          touched: /rebuild runs on a copy under build/whatif/).
POST /quick {text}        the router for a plain chat message (called by the Hermes plugin's pre_llm_call hook). Opens a layout
                          at once; for a number question it returns the facts as context; for a run it only issues the confirm
                          id (the user still writes "yes, run <id>"), and "yes, run <id>" itself is completed here. Returns
                          {handled, reply, context}; handled=false means: let the model do it.
Design names may be loose ("kv_attn", "vision lit", "kv attention 16"): normalize_tools.resolve_design / design_from_text.
Plugin: scripts/hermes/plugin/open-ai-chip/ (installed by scripts/hermes_agent_setup.sh). No model, no network beyond 127.0.0.1.
Docs: hermes-agents.md, docs/HERMES_AGENT_INTEGRATION.md
Tests: tests/tools/test_quick_tools.py
"""
import glob
import inspect
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts", "flow"))
import normalize_tools as nt  # noqa: E402

router = APIRouter()
LOG = os.path.join(REPO, "build", "agent", "quick.jsonl")

# make target for what people say; "synth" has no make target of its own: LibreLane's gds step runs synthesis first.
TARGET_ALIASES = {"sim": "simulate", "simulate": "simulate", "simulation": "simulate", "rtl": "simulate", "tb": "simulate",
                  "synth": "gds", "synthesis": "gds", "yosys": "gds", "gds": "gds", "harden": "flow-all", "pnr": "gds",
                  "full": "flow-all", "flow": "flow-all", "flow-all": "flow-all", "flowall": "flow-all", "all": "flow-all",
                  "check": "check", "signoff": "check", "drc": "check", "lvs": "check", "timing": "check", "sta": "check",
                  "gl": "gl", "gate": "gl", "gatelevel": "gl", "gl-final": "gl-final", "glfinal": "gl-final", "final": "gl-final",
                  "collect": "collect", "test": "test", "doctor": "doctor", "test-full": "test-full", "views": "views",
                  "soc-sim": "soc-sim", "soc-kv": "soc-kv"}
FROZEN_WRITERS = {"gds", "flow-all", "collect", "views"}       # would rewrite designs/<d>/output (frozen)
NO_DESIGN = {"test", "doctor", "test-full", "soc-sim", "soc-kv"}

HELP = """**open-ai-chip commands** (instant, no model; design names may be partial: `kv_attn`, `vision lit`, `kv attention 16`)

| Command | What it does |
|---|---|
| `/klayout <design> [show met1, zoom ...]` | open the layout in a controllable KLayout window |
| `/magic <design> [...]` | the same in Magic |
| `/gds <design>` | the full KLayout desktop app with the sky130 layer colours |
| `/layout <sentence>` | drive the open window: `show only met1 and met2`, `zoom to the lower-left 50 um`, `run drc`, `close all` |
| `/png <design>` | a picture of the layout in the chat |
| `/metrics <design>` | the key numbers (cells, flip-flops, area, slack, power, DRC/LVS) |
| `/synth <design>` | synthesis: cell classes, top cell types, synth checks |
| `/timing <design>` | setup/hold slack per corner, clock skew, slew/cap/fanout |
| `/drc <design> [live]` | DRC: Magic, KLayout, router iterations, antenna (`live` = run DRC in the KLayout window) |
| `/lvs <design>` | LVS counts and netgen's final result |
| `/signoff <design>` | the whole verdict in one table |
| `/compare <d1> <d2> ...` | side by side |
| `/designs` | every design with cells and verdict |
| `/log <design>` | log digest: errors, warnings, slowest steps |
| `/notes <design> [section]` | a section of the design page |
| `/ask <question>` | answer with quotes from the repo docs (mini RAG) |
| `/sim <design>` | start the RTL simulation now |
| `/run <target> <design>` | start a make target now: simulate, synth (=gds), check (DRC/LVS/timing), gl, gl-final, flow-all |
| `/rebuild <design>` | full flow on a fresh unchanged copy (frozen design untouched) |
| `/jobs`, `/job <id>` | running and finished jobs |
| `/loopdemo signoff kv`, `/loopdemo layers kv8`, `/loopdemo sim vision lit` | loop-engineering demos: PLAN, ACT, OBSERVE, CHECK, STOP, shown step by step |
| `/harness names`, `/harness facts kv` | harness-engineering demos: fixed cases, checks against metrics.json, score and gate |

Frozen designs (all 25) never get gds/flow-all/collect: use `/rebuild`. One physical flow at a time."""


class QuickReq(BaseModel):
    cmd: Optional[str] = Field(None, description="slash command name without '/', e.g. klayout, timing, run")
    args: Optional[str] = Field("", description="the rest of the slash command line")
    text: Optional[str] = Field(None, description="a plain chat message (router mode)")


# ---------------------------------------------------------------- helpers
def _log(entry: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps(dict(entry, t=time.strftime("%Y-%m-%dT%H:%M:%S"))) + "\n")
    except OSError:
        pass


def _call(name: str, body: dict) -> dict:
    """Call another mounted tool in this process (same as normalize_tools._self)."""
    srv = nt._server()
    flat = []
    for r in getattr(getattr(srv, "app", None), "routes", []):
        flat.extend(r.original_router.routes if hasattr(r, "original_router") else [r])
    for r in flat:
        if getattr(r, "path", "") == "/" + name and getattr(r, "endpoint", None):
            try:
                params = list(inspect.signature(r.endpoint).parameters.values())
                return r.endpoint(params[0].annotation(**body)) if params else r.endpoint()
            except Exception as e:  # noqa: BLE001
                return {"error": "call %s failed: %s" % (name, e)}
    return {"error": "tool %s is not mounted" % name}


def _design(words: str) -> Tuple[Optional[str], str]:
    """(design, message). Resolves a loose name; message explains a miss or a tie."""
    w = (words or "").strip()
    if not w:
        return None, "which design? e.g. `kv_attn_n8`, `vision_block` (`/designs` lists all)"
    d = nt.design_from_text(w)
    if d:
        return d, ""
    name, close = nt.resolve_design(w)
    if name:
        return name, ""
    return None, ("no design matches %r%s" % (w, (": did you mean " + ", ".join("`%s`" % c for c in close) + "?") if close
                                              else "; `/designs` lists them"))


def _metrics(d: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(REPO, "designs", d, "output", "metrics.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _report(d: str, name: str) -> str:
    try:
        with open(os.path.join(REPO, "designs", d, "output", "reports", name), errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _src(d: str, *files: str) -> str:
    return "Source: " + ", ".join("`designs/%s/output/%s`" % (d, f) for f in files)


def _corner_min(m: dict, prefix: str) -> Optional[float]:
    v = [x for k, x in m.items() if k.startswith(prefix + "__corner:") and isinstance(x, (int, float))]
    return min(v) if v else m.get(prefix)


def _corner_max(m: dict, prefix: str) -> Optional[float]:
    v = [x for k, x in m.items() if k.startswith(prefix + "__corner:") and isinstance(x, (int, float))]
    return max(v) if v else m.get(prefix)


def _fmt(x, nd=3):
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return ("%.*f" % (nd, x)).rstrip("0").rstrip(".") if abs(x) >= 1e-3 or x == 0 else "%.3g" % x
    return str(x)


def _frozen(d: str) -> bool:
    try:
        import frozen
        return d in frozen.frozen_designs()
    except Exception:  # noqa: BLE001
        return False


def _verdict(m: dict) -> Tuple[bool, List[str]]:
    bad = []
    for k, label in (("magic__drc_error__count", "Magic DRC"), ("klayout__drc_error__count", "KLayout DRC"),
                     ("design__lvs_error__count", "LVS"), ("design__xor_difference__count", "XOR"),
                     ("antenna__violating__nets", "antenna"), ("route__drc_errors", "route DRC")):
        if m.get(k):
            bad.append("%s %s" % (label, m[k]))
    s, h = _corner_min(m, "timing__setup__ws"), _corner_min(m, "timing__hold__ws")
    if isinstance(s, (int, float)) and s < 0:
        bad.append("setup %s ns" % _fmt(s))
    if isinstance(h, (int, float)) and h < 0:
        bad.append("hold %s ns" % _fmt(h))
    return not bad, bad


# ---------------------------------------------------------------- read-only commands (committed evidence)
def cmd_metrics(d: str) -> str:
    m = _metrics(d)
    if not m:
        return "no `designs/%s/output/metrics.json` (not hardened?)" % d
    ok, bad = _verdict(m)
    rows = [("standard cells", m.get("design__instance__count__stdcell")),
            ("flip-flops", m.get("design__instance__count__class:sequential_cell")),
            ("cell area (um^2)", m.get("design__instance__area__stdcell")),
            ("die area (um^2)", m.get("design__die__area")), ("utilization", m.get("design__instance__utilization")),
            ("worst setup slack (ns, all corners)", _corner_min(m, "timing__setup__ws")),
            ("worst hold slack (ns, all corners)", _corner_min(m, "timing__hold__ws")),
            ("total power (W, typical)", m.get("power__total")),
            ("DRC Magic / KLayout", "%s / %s" % (m.get("magic__drc_error__count"), m.get("klayout__drc_error__count"))),
            ("LVS errors", m.get("design__lvs_error__count"))]
    out = ["**%s**: %s" % (d, "signoff clean" if ok else "NOT clean: " + ", ".join(bad)), "", "| metric | value |", "|---|---|"]
    out += ["| %s | %s |" % (k, _fmt(v, 4)) for k, v in rows]
    return "\n".join(out + ["", _src(d, "metrics.json")])


def cmd_synth(d: str) -> str:
    m, stat, chk = _metrics(d), _report(d, "synth_stat.rpt"), _report(d, "synth_checks.rpt")
    if not m and not stat:
        return "no synthesis evidence for %s" % d
    cls = sorted(((k.split(":", 1)[1], v) for k, v in m.items() if k.startswith("design__instance__count__class:")),
                 key=lambda kv: -kv[1])
    cells = sorted(((int(n), t) for n, a, t in re.findall(r"^\s+(\d+)\s+([\d.Ee+]+)\s+sky130_fd_sc_hd__(\S+)", stat, re.M)), reverse=True)
    tot = re.search(r"^\s+(\d+)\s+\S+\s+cells\s*$", stat, re.M)
    probs = re.search(r"Found and reported (\d+) problems", chk)
    out = ["**%s synthesis** (Yosys, before placement)" % d, "",
           "- cells after synthesis: %s; synth checks: %s problems (`synthesis__check_error__count` %s)" % (
               tot.group(1) if tot else "n/a", probs.group(1) if probs else "n/a", m.get("synthesis__check_error__count")),
           "- after place and route: %s standard cells, %s flip-flops" % (
               m.get("design__instance__count__stdcell"), m.get("design__instance__count__class:sequential_cell")), ""]
    if cells:
        out += ["Top cell types (synth): " + ", ".join("%s x%d" % (t, n) for n, t in cells[:8]), ""]
    if cls:
        out += ["| cell class (final) | count |", "|---|---|"] + ["| %s | %s |" % kv for kv in cls]
    return "\n".join(out + ["", _src(d, "reports/synth_stat.rpt", "reports/synth_checks.rpt", "metrics.json")])


def cmd_timing(d: str) -> str:
    m, rpt = _metrics(d), _report(d, "timing_summary.rpt")
    if not m:
        return "no metrics for %s" % d
    rows = []
    for line in rpt.splitlines():
        cols = [c.strip() for c in line.strip().strip("│┃").split("│")]
        if len(cols) >= 12 and re.match(r"(Overall|nom_|min_|max_)", cols[0]):
            rows.append("| %s | %s | %s | %s | %s | %s |" % (cols[0], cols[6], cols[1], cols[9], cols[4], cols[12] if len(cols) > 12 else cols[-1]))
    out = ["**%s timing** (clock 25 ns; positive slack = met)" % d, "",
           "- worst setup slack %s ns, worst hold slack %s ns (all corners)" % (
               _fmt(_corner_min(m, "timing__setup__ws")), _fmt(_corner_min(m, "timing__hold__ws"))),
           "- clock skew: setup %s ns, hold %s ns" % (_fmt(m.get("clock__skew__worst_setup")), _fmt(m.get("clock__skew__worst_hold"))),
           "- max slew / cap / fanout violations (worst corner): %s / %s / %s (slew is reported, not a signoff failure here)" % (
               _corner_max(m, "design__max_slew_violation__count"), _corner_max(m, "design__max_cap_violation__count"),
               _corner_max(m, "design__max_fanout_violation__count")), ""]
    if rows:
        out += ["| corner | setup WS | hold WS | setup vio | hold vio | max slew |", "|---|---|---|---|---|---|"] + rows
    path = _report(d, "timing_paths_max_ss.rpt")
    sp, ep = re.search(r"Startpoint: (.+)", path), re.search(r"Endpoint: (.+)", path)
    if sp and ep:
        out += ["", "Critical setup path (max_ss): %s -> %s" % (sp.group(1).strip(), ep.group(1).strip())]
    return "\n".join(out + ["", _src(d, "reports/timing_summary.rpt", "reports/timing_paths_max_ss.rpt", "metrics.json")])


def cmd_drc(d: str) -> str:
    m = _metrics(d)
    if not m:
        return "no metrics for %s" % d
    iters = sorted(((int(k.split(":")[1]), v) for k, v in m.items() if k.startswith("route__drc_errors__iter:")))
    kl = {}
    try:
        with open(os.path.join(REPO, "designs", d, "output", "reports", "drc_klayout.json")) as f:
            kl = json.load(f)
    except (OSError, ValueError):
        pass
    nz = {k: v for k, v in kl.items() if isinstance(v, (int, float)) and v}
    out = ["**%s DRC**" % d, "",
           "| check | errors |", "|---|---|",
           "| Magic DRC (signoff) | %s |" % m.get("magic__drc_error__count"),
           "| KLayout DRC (signoff) | %s |" % m.get("klayout__drc_error__count"),
           "| detailed router, final | %s |" % m.get("route__drc_errors"),
           "| antenna violating nets / pins | %s / %s |" % (m.get("antenna__violating__nets"), m.get("antenna__violating__pins")),
           "| XOR (Magic vs KLayout GDS) | %s |" % m.get("design__xor_difference__count"), ""]
    if iters:
        out.append("Router DRC by iteration: " + " -> ".join(str(v) for _, v in iters) + " (the router repairs its own violations)")
    out.append("KLayout rule decks with errors: " + (", ".join("%s %s" % kv for kv in nz.items()) if nz else "none (%d rules checked)" % len(kl)))
    out.append("Run DRC visibly in the KLayout window: `/drc %s live`." % d)
    return "\n".join(out + ["", _src(d, "metrics.json", "reports/drc_magic.rpt", "reports/drc_klayout.json")])


def cmd_lvs(d: str) -> str:
    m, rpt = _metrics(d), _report(d, "lvs_netgen.rpt")
    if not m:
        return "no metrics for %s" % d
    keys = [k for k in sorted(m) if k.startswith("design__lvs_")]
    fin = re.findall(r"Final result: (.+)", rpt)
    out = ["**%s LVS** (netgen: layout netlist vs the synthesised netlist)" % d, "",
           "- netgen: %s" % (fin[-1].strip() if fin else "no final-result line"), "",
           "| check | count |", "|---|---|"] + ["| %s | %s |" % (k.replace("design__lvs_", "").replace("__count", ""), m[k]) for k in keys]
    return "\n".join(out + ["", _src(d, "metrics.json", "reports/lvs_netgen.rpt")])


def cmd_signoff(d: str) -> str:
    m = _metrics(d)
    if not m:
        return "no metrics for %s" % d
    ok, bad = _verdict(m)
    rows = [("Magic DRC", m.get("magic__drc_error__count")), ("KLayout DRC", m.get("klayout__drc_error__count")),
            ("LVS errors", m.get("design__lvs_error__count")), ("XOR differences", m.get("design__xor_difference__count")),
            ("antenna nets", m.get("antenna__violating__nets")), ("router DRC", m.get("route__drc_errors")),
            ("worst setup slack (ns)", _corner_min(m, "timing__setup__ws")), ("worst hold slack (ns)", _corner_min(m, "timing__hold__ws")),
            ("synth check problems", m.get("synthesis__check_error__count")), ("flow errors", m.get("flow__errors__count"))]
    frozen = " Frozen: yes (`designs/FROZEN.json`)." if _frozen(d) else ""
    return "\n".join(["**%s signoff: %s**%s" % (d, "CLEAN" if ok else "NOT clean (" + ", ".join(bad) + ")", frozen), "",
                      "| check | value |", "|---|---|"] + ["| %s | %s |" % (k, _fmt(v, 4)) for k, v in rows] + ["", _src(d, "metrics.json")])


def cmd_compare(words: str) -> str:
    parts = [p for p in re.split(r"\s*(?:,|\bvs\.?\b|\band\b|\s{2,})\s*", words) if p.strip()]
    if len(parts) < 2:
        parts = words.split()
    ds, miss = [], []
    for p in parts:
        d, msg = _design(p)
        (ds.append(d) if d and d not in ds else miss.append(msg) if not d else None)
    if len(ds) < 2:
        return "give two or more designs, e.g. `/compare kv_attn_n4 kv_attn_n8 kv_attn_n16`" + ("; " + "; ".join(miss) if miss else "")
    keys = [("cells", "design__instance__count__stdcell"), ("flip-flops", "design__instance__count__class:sequential_cell"),
            ("cell area um^2", "design__instance__area__stdcell"), ("die area um^2", "design__die__area"), ("power W", "power__total")]
    ms = {d: _metrics(d) for d in ds}
    out = ["| metric | " + " | ".join(ds) + " |", "|---|" + "---|" * len(ds)]
    out += ["| %s | %s |" % (lab, " | ".join(_fmt(ms[d].get(k), 4) for d in ds)) for lab, k in keys]
    out.append("| worst setup ns | %s |" % " | ".join(_fmt(_corner_min(ms[d], "timing__setup__ws")) for d in ds))
    out.append("| worst hold ns | %s |" % " | ".join(_fmt(_corner_min(ms[d], "timing__hold__ws")) for d in ds))
    out.append("| signoff | %s |" % " | ".join("clean" if _verdict(ms[d])[0] else "NOT clean" for d in ds))
    return "\n".join(out + ["", "Source: `designs/<d>/output/metrics.json`"])


def cmd_designs() -> str:
    out = ["| design | cells | flip-flops | setup WS ns | signoff |", "|---|---|---|---|---|"]
    for d in nt.designs():
        m = _metrics(d)
        out.append("| %s | %s | %s | %s | %s |" % (d, m.get("design__instance__count__stdcell", "-"),
                                                     m.get("design__instance__count__class:sequential_cell", "-"),
                                                     _fmt(_corner_min(m, "timing__setup__ws")), ("clean" if _verdict(m)[0] else "NOT clean") if m else "-"))
    return "\n".join(out + ["", "Source: `designs/*/output/metrics.json`"])


# ---------------------------------------------------------------- actions
def _md(r: dict, fallback: str) -> str:
    if r.get("error"):
        return "error: %s" % r["error"]
    return r.get("markdown") or r.get("say") or fallback


def cmd_window(tool: str, words: str) -> str:
    """/klayout kv_attn show only met1 -> "open kv_attn_n8 in klayout and show only met1"."""
    w = words.strip()
    if not w:
        return "usage: `/%s <design> [show only met1, zoom to the lower-left 50 um]`" % tool
    m = re.match(r"^(.*?)(?:\s*(?:,|\band\b|\bthen\b)\s*|\s+(?=(?:show|zoom|hide|run|measure|find|snapshot|close|only)\b)|$)(.*)$", w)
    first, rest = (m.group(1), m.group(2)) if m else (w, "")
    d = nt.design_from_text(first) or nt.resolve_design(first)[0] if first else None
    if d:
        sentence = "open %s in %s" % (d, tool) + ((" and " + rest.strip()) if rest.strip() else "")
    else:
        sentence = w
    r = _call("gui_command", {"text": sentence, "tool": tool})
    did = ", ".join(x.get("op", "") + (" " + x["design"] if x.get("design") else "") for x in r.get("did", []))
    if not r.get("ok"):
        hint = "" if d else " (no design matched %r: `/designs` lists them)" % first
        return "%s: %s%s" % (tool, r.get("error") or r.get("note") or "not understood", hint)
    return "%s: %s (%s s)" % (tool, did, r.get("seconds")) + ("\n\n" + r["markdown"] if r.get("markdown") else "")


def cmd_run(target: str, words: str) -> str:
    t = TARGET_ALIASES.get((target or "").lower().strip())
    if not t:
        return "unknown target %r; use: simulate, synth (=gds), check (DRC/LVS/timing), gl, gl-final, flow-all, test" % target
    d = None
    if t not in NO_DESIGN:
        d, msg = _design(words)
        if not d:
            return msg
        if t in FROZEN_WRITERS and _frozen(d):
            return ("`%s` is frozen (`designs/FROZEN.json`): `make %s` would rewrite its committed evidence. "
                    "Use `/rebuild %s` (the full flow on a fresh copy under build/whatif/; the design stays untouched)." % (d, t, d))
    first = _call("run_make", {"target": t, **({"design": d} if d else {})})
    if not first.get("confirm_id"):
        return _md(first, "started")
    r = _call("confirm_run", {"confirm_id": first["confirm_id"]})        # the user typed the command: that is the confirmation
    note = " (synthesis runs inside LibreLane's gds step)" if (target or "").lower().startswith("synth") else ""
    if r.get("job_id"):
        return "Started `make %s%s`%s: job `%s`. Follow it with `/job %s` or `/jobs`; log `%s`." % (
            t, " DESIGN=" + d if d else "", note, r["job_id"], r["job_id"], r.get("log", "build/agent/jobs/"))
    return _md(r, "not started")


def cmd_rebuild(words: str) -> str:
    d, msg = _design(words)
    if not d:
        return msg
    first = _call("whatif_run", {"design": d, "rebuild": True})
    if not first.get("confirm_id"):
        return _md(first, "started")
    r = _call("whatif_run", {"confirm_id": first["confirm_id"]})
    if r.get("job_id"):
        return ("Rebuilding `%s` unchanged on a copy (`%s`): job `%s`, a few minutes. The frozen design is not touched. "
                "Then: `/job %s`, and compare with `whatif_result` (tag `%s`)." % (d, r.get("copy"), r["job_id"], r["job_id"], (r.get("copy") or "").split("__")[-1]))
    return _md(r, "not started")


def cmd_jobs() -> str:
    r = _call("job_list", {})
    jobs = r.get("jobs") if isinstance(r, dict) else None
    if not jobs:
        return "no jobs" if not r.get("error") else "error: %s" % r["error"]
    out = ["| job | what | state |", "|---|---|---|"]
    for j in jobs[-12:]:
        out.append("| %s | %s | %s |" % (j.get("id") or j.get("job_id"), j.get("label") or j.get("cmd"), j.get("state")))
    return "\n".join(out)


def cmd_job(words: str) -> str:
    jid = words.strip().split()[0] if words.strip() else ""
    if not jid:
        return "usage: `/job <id>` (ids from `/jobs`)"
    r = _call("job_status", {"job_id": jid})
    if r.get("error"):
        return "error: %s" % r["error"]
    tail = r.get("tail") or r.get("log_tail") or ""
    if isinstance(tail, list):
        tail = "\n".join(tail)
    return "job `%s`: **%s**%s\n\n```\n%s\n```" % (jid, r.get("state"), (", exit %s" % r.get("rc")) if r.get("rc") is not None else "",
                                                    str(tail)[-1500:])


# ---------------------------------------------------------------- loop and harness engineering demos (existing designs only)
# A loop = PLAN, then repeat ACT -> OBSERVE -> CHECK until a stop condition or a budget. A harness = everything around the
# agent that makes it reliable: deterministic tools, checks against ground truth, budgets, a score and a pass/fail gate.
# These demos run in code (no model), so every step is visible and repeatable; examples/hermes_harness/ shows the same ideas
# with a model inside the loop.
def _family(words: str) -> List[str]:
    w = (words or "").strip().lower().replace(" ", "_")
    fams = {"kv": "kv_attn_", "kv_attn": "kv_attn_", "prec": "prec_", "precision": "prec_", "vision": "vision_", "audio": "audio_",
            "wrapper": "user_project_wrapper", "caravel": "user_project_wrapper", "soc": "soc_", "all": ""}
    if w in fams:
        return [d for d in nt.designs() if d.startswith(fams[w])] if fams[w] else list(nt.designs())
    d, _ = _design(words)
    return [d] if d else []


def _trace(rows: List[Tuple[int, str, str]]) -> List[str]:
    return ["| step | phase | detail |", "|---|---|---|"] + ["| %s | %s | %s |" % r for r in rows]


def loop_signoff(words: str, budget: int = 30) -> str:
    """Read-only loop over a family: for each design ACT read metrics, OBSERVE the numbers, CHECK the verdict."""
    ds = _family(words or "kv")
    if not ds:
        return "usage: `/loopdemo signoff <family or design>` (families: kv, prec, vision, audio, soc, wrapper, all)"
    rows, step, clean, worst = [(0, "PLAN", "goal: every design in %s signoff-clean; budget %d steps; stop when all checked" % (words or "kv", budget))], 0, [], None
    for d in ds:
        step += 1
        if step > budget:
            rows.append((step, "STOP", "budget reached"))
            break
        m = _metrics(d)
        ok, bad = _verdict(m)
        s = _corner_min(m, "timing__setup__ws")
        worst = (d, s) if isinstance(s, (int, float)) and (worst is None or s < worst[1]) else worst
        rows.append((step, "ACT", "read designs/%s/output/metrics.json" % d))
        rows.append((step, "OBSERVE", "cells %s, setup %s ns, DRC %s/%s, LVS %s" % (m.get("design__instance__count__stdcell"), _fmt(s),
                     m.get("magic__drc_error__count"), m.get("klayout__drc_error__count"), m.get("design__lvs_error__count"))))
        rows.append((step, "CHECK", "clean" if ok else "NOT clean: " + ", ".join(bad)))
        clean += [d] if ok else []
    rows.append((step + 1, "STOP", "all %d checked: %d clean; tightest setup slack %s (%s ns)" % (len(ds), len(clean), worst[0] if worst else "-",
                                                                                                    _fmt(worst[1]) if worst else "-")))
    return "**Loop: signoff check** (read-only, %d designs)\n\n" % len(ds) + "\n".join(_trace(rows))


def loop_layers(words: str, tool: str = "klayout") -> str:
    """GUI loop: open the design, then one layer at a time: ACT show only metN, OBSERVE the picture, CHECK it rendered."""
    tl = "magic" if re.search(r"\bmagic\b", words or "") else tool
    d, msg = _design(re.sub(r"\b(magic|klayout)\b", "", words or ""))
    if not d:
        return msg
    rows, pics, t0 = [(0, "PLAN", "open %s in %s, then show met1..met5 one at a time; budget 7 steps, 120 s" % (d, tl))], [], time.time()
    r = _call("gui_command", {"text": "open %s in %s" % (d, tl), "tool": tl})
    rows.append((1, "ACT", "open %s in %s" % (d, tl)))
    rows.append((1, "CHECK", "window open (%s s)" % r.get("seconds") if r.get("ok") else "failed: %s" % (r.get("error") or r.get("note"))))
    if not r.get("ok"):
        return "**Loop: layer tour**\n\n" + "\n".join(_trace(rows))
    for i, layer in enumerate(("met1", "met2", "met3", "met4", "met5"), start=2):
        if time.time() - t0 > 120:
            rows.append((i, "STOP", "time budget reached"))
            break
        r = _call("gui_command", {"text": "show only %s" % layer, "tool": tl})
        img = re.search(r"\((http[^)]+\.png)\)", r.get("markdown") or "")
        rows.append((i, "ACT", "show only %s" % layer))
        rows.append((i, "OBSERVE", "picture %s" % (img.group(1) if img else "none")))
        rows.append((i, "CHECK", "rendered" if r.get("ok") and img else "no picture: %s" % (r.get("error") or "-")))
        if img:
            pics.append("![%s %s](%s)" % (d, layer, img.group(1)))
    _call("gui_command", {"text": "show all", "tool": tl})
    rows.append((7, "STOP", "all layers shown; window left open with every layer (close with `/layout close all`)"))
    return "**Loop: layer tour of %s in %s**\n\n" % (d, tl) + "\n".join(_trace(rows)) + ("\n\n" + "\n".join(pics) if pics else "")


def loop_sim(words: str, budget_s: int = 180) -> str:
    """Act-observe-verify with a real job: ACT start make simulate, OBSERVE job_status every 2 s, CHECK 'PASS' in the log."""
    d, msg = _design(words)
    if not d:
        return msg
    rows = [(0, "PLAN", "start make simulate DESIGN=%s; poll every 2 s; success = exit 0 and a PASS line; budget %d s" % (d, budget_s))]
    first = _call("run_make", {"target": "simulate", "design": d})
    r = _call("confirm_run", {"confirm_id": first["confirm_id"]}) if first.get("confirm_id") else first
    jid = r.get("job_id")
    rows.append((1, "ACT", "make simulate DESIGN=%s: job %s" % (d, jid or r.get("error"))))
    if not jid:
        return "**Loop: simulate and verify**\n\n" + "\n".join(_trace(rows))
    t0, step, st = time.time(), 1, {}
    while time.time() - t0 < budget_s:
        time.sleep(2)
        step += 1
        st = _call("job_status", {"job_id": jid})
        if st.get("state") not in ("running", "queued", "pending"):
            break
        if step % 5 == 0:
            rows.append((step, "OBSERVE", "still %s after %d s" % (st.get("state"), time.time() - t0)))
    tail = st.get("log_tail") or ""
    tail = "\n".join(tail) if isinstance(tail, list) else str(tail)
    passed = re.search(r"^PASS .*$", tail, re.M)
    rows.append((step, "OBSERVE", "state %s, exit %s, %d s" % (st.get("state"), st.get("rc"), time.time() - t0)))
    rows.append((step, "CHECK", ("PASS: " + passed.group(0)[:110]) if passed and st.get("rc") == 0 else "no PASS line (see /job %s)" % jid))
    rows.append((step + 1, "STOP", "verified" if passed and st.get("rc") == 0 else ("budget reached, job keeps running" if st.get("state") == "running" else "failed")))
    return "**Loop: simulate and verify %s**\n\n" % d + "\n".join(_trace(rows))


HARNESS_NAMES = [("kv_attn", "kv_attn_n8"), ("vision lit", "vision_all_lit"), ("kv attention 16", "kv_attn_n16"),
                 ("caravel kv", "user_project_wrapper_soc_kv"), ("kv ring", "kv_attn_n8_ring"), ("prec bf16", "prec_bf16"),
                 ("kv8", "kv_attn_n8"), ("image text", "image_text_match"), ("tiny ai", "tiny_ai_core"), ("audio", None)]


def harness_names() -> str:
    """Harness: a fixed test set for the design-name resolver; score and gate (an ambiguous name must NOT be guessed)."""
    rows, ok = ["| user wrote | expected | got | pass |", "|---|---|---|---|"], 0
    for said, want in HARNESS_NAMES:
        got, _ = nt.resolve_design(said)
        p = got == want
        ok += p
        rows.append("| %s | %s | %s | %s |" % (said, want or "(ask: ambiguous)", got or "(asks)", "yes" if p else "NO"))
    gate = "PASS" if ok == len(HARNESS_NAMES) else "FAIL"
    return "**Harness: design-name resolver** (fixed set, %d cases): score %d/%d, gate **%s**\n\n" % (len(HARNESS_NAMES), ok, len(HARNESS_NAMES), gate) + "\n".join(rows)


def harness_facts(words: str) -> str:
    """Harness: ask the fast path three questions per design and check each answer against metrics.json (ground truth) and that it
    names its source file (grounding). The same idea as examples/hermes_harness/, with code in place of the model."""
    ds = _family(words or "kv")
    if not ds:
        return "usage: `/harness facts <family or design>`"
    rows, ok, n = ["| design | question | expected (metrics.json) | answer has it | cites source | pass |", "|---|---|---|---|---|---|"], 0, 0
    for d in ds:
        m = _metrics(d)
        cases = [("cells", cmd_metrics(d), str(m.get("design__instance__count__stdcell"))),
                 ("setup slack", cmd_timing(d), _fmt(_corner_min(m, "timing__setup__ws"))),
                 ("LVS", cmd_lvs(d), "| error | %s |" % m.get("design__lvs_error__count"))]
        for q, ans, want in cases:
            has, cites = want in ans, "Source: `designs/%s/output/" % d in ans
            p = has and cites
            ok, n = ok + p, n + 1
            rows.append("| %s | %s | %s | %s | %s | %s |" % (d, q, want.strip("| ").replace("|", "/"), "yes" if has else "NO", "yes" if cites else "NO", "yes" if p else "NO"))
    gate = "PASS" if ok == n else "FAIL"
    return "**Harness: grounded facts** (%d designs x 3 questions): score %d/%d, gate **%s**\n\n" % (len(ds), ok, n, gate) + "\n".join(rows)


LOOP_HELP = """**Loop and harness demos** (existing designs only; every step is shown)

| Command | Idea it shows |
|---|---|
| `/loopdemo signoff kv` | a read-only loop: PLAN, then ACT read, OBSERVE numbers, CHECK verdict per design, STOP when all checked |
| `/loopdemo layers kv8` (or `... magic`) | a GUI loop: open the layout, show met1..met5 one at a time, check each picture rendered |
| `/loopdemo sim vision lit` | act, observe, verify with a real job: start the simulation, poll, check the PASS line, budget 180 s |
| `/harness names` | a test harness for the name resolver: fixed cases, score, pass/fail gate (an ambiguous name must not be guessed) |
| `/harness facts kv` | grounding harness: 3 questions per design checked against metrics.json and for a cited source |

The same ideas with a model inside the loop: `examples/hermes_harness/` (budgets, plan-then-execute, grounding checks, eval)."""


def run_loop(words: str) -> str:
    p = (words or "").split(None, 1)
    kind, rest = (p[0].lower() if p else ""), (p[1] if len(p) > 1 else "")
    if kind == "signoff":
        return loop_signoff(rest)
    if kind in ("layers", "layer", "gui", "tour"):
        return loop_layers(rest)
    if kind in ("sim", "simulate"):
        return loop_sim(rest)
    return LOOP_HELP


def run_harness(words: str) -> str:
    p = (words or "").split(None, 1)
    kind, rest = (p[0].lower() if p else ""), (p[1] if len(p) > 1 else "")
    if kind == "names":
        return harness_names()
    if kind == "facts":
        return harness_facts(rest)
    return LOOP_HELP


def run_cmd(cmd: str, args: str) -> str:
    c = (cmd or "").lower().lstrip("/").strip()
    a = (args or "").strip()
    one = lambda f: (lambda d, m: f(d) if d else m)(*_design(a))  # noqa: E731
    if c in ("chip", "help", "chiphelp", "commands"):
        return HELP
    if c == "designs":
        return cmd_designs()
    if c in ("metrics", "numbers", "stats"):
        return one(cmd_metrics)
    if c in ("synth", "synthesis"):
        return one(cmd_synth)
    if c in ("timing", "sta", "slack"):
        return one(cmd_timing)
    if c == "drc":
        if re.search(r"\b(live|window|gui|klayout)\b", a):
            return cmd_window("klayout", re.sub(r"\b(live|window|gui|klayout)\b", "", a).strip() + " and run drc")
        return one(cmd_drc)
    if c == "lvs":
        return one(cmd_lvs)
    if c in ("signoff", "status", "clean"):
        return one(cmd_signoff)
    if c == "compare":
        return cmd_compare(a)
    if c in ("klayout", "kl"):
        return cmd_window("klayout", a)
    if c == "magic":
        return cmd_window("magic", a)
    if c == "layout":
        r = _call("gui_command", {"text": a})
        return (_md(r, "done") if r.get("ok") else "not understood: %s\n\n%s" % (a, r.get("hint", ""))) if a else "usage: `/layout show only met1 and met2`"
    if c == "gds":
        return one(lambda d: _md(_call("open_gds", {"design": d, "viewer": "klayout-app"}), "opened"))
    if c in ("png", "picture"):
        return one(lambda d: _md(_call("klayout_view", {"design": d}), "rendered"))
    if c == "log":
        return one(lambda d: _md(_call("log_digest", {"design": d}), "no digest"))
    if c == "notes":
        d, msg = _design(a.split()[0] if a else "")
        if not d:
            return msg
        sec = a.split(None, 1)[1] if len(a.split()) > 1 else None
        return _md(_call("notes_section", {"design": d, **({"section": sec} if sec else {})}), "no section")
    if c in ("ask", "why"):
        return _md(_call("rag_answer", {"question": a}), "no answer") if a else "usage: `/ask why does kv_attn_n8_int4 have more flip-flops?`"
    if c in ("sim", "simulate"):
        return cmd_run("simulate", a)
    if c == "run":
        p = a.split(None, 1)
        return cmd_run(p[0] if p else "", p[1] if len(p) > 1 else "") if p else "usage: `/run <target> <design>`, e.g. `/run synth vision_block`"
    if c in ("rebuild", "reharden"):
        return cmd_rebuild(a)
    if c in ("loopdemo", "loop"):          # /loopdemo is a Hermes built-in, so the plugin registers /loopdemo
        return run_loop(a)
    if c == "harness":
        return run_harness(a)
    if c == "jobs":
        return cmd_jobs()
    if c == "job":
        return cmd_job(a)
    return "unknown command /%s\n\n%s" % (c, HELP)


# ---------------------------------------------------------------- router for plain chat messages
OPEN_RE = re.compile(r"\b(open|load|launch|show|view|display|bring up|pull up)\b")
WINDOW_RE = re.compile(r"\b(klayout|k-layout|magic|gds|gdsii|layout)\b")
LAYER_RE = re.compile(r"\b(met[1-5]|metal ?[1-5]|li1|poly|zoom|hide|layers?|close (klayout|magic|all|the window))\b")
RUN_RE = re.compile(r"\b(run|start|kick ?off|launch|execute|redo|re-?run|rebuild|re-?harden|harden)\b")
FACT = [("timing", re.compile(r"\b(timing|slack|setup|hold|skew|sta|critical path)\b"), cmd_timing),
        ("lvs", re.compile(r"\blvs\b"), cmd_lvs),
        ("drc", re.compile(r"\b(drc|design rules?|antenna)\b"), cmd_drc),
        ("synth", re.compile(r"\b(synth|synthesis|yosys|cell types?|flip-?flops?|cells?)\b"), cmd_synth),
        ("signoff", re.compile(r"\b(signoff|sign-off|clean|verdict)\b"), cmd_signoff),
        ("metrics", re.compile(r"\b(area|power|utili[sz]ation|metrics|numbers)\b"), cmd_metrics)]
WHY_RE = re.compile(r"^(why|how (does|do|did|is|was|were|can)|what (fixed|limits|causes|caused|made|happens)|explain)\b")
YES_RE = re.compile(r"^\s*yes,?\s+run\s+([0-9a-f]{6})\b", re.I)


def _target_in(t: str) -> Optional[str]:
    if re.search(r"\b(rebuild|re-?harden)\b", t):
        return "rebuild"
    for w, tgt in (("flow-all", "flow-all"), ("full flow", "flow-all"), ("whole flow", "flow-all"), ("gate level final", "gl-final"),
                   ("gl-final", "gl-final"), ("gate level", "gl"), ("gate-level", "gl"), ("simulat", "simulate"), ("synth", "gds"),
                   ("gds", "gds"), ("harden", "flow-all"), ("signoff", "check"), ("drc", "check"), ("lvs", "check"),
                   ("timing", "check"), ("check", "check"), ("make test", "test"), ("the flow", "flow-all"), ("flow", "flow-all")):
        if w in t:
            return tgt
    return None


def route_text(text: str) -> Dict[str, Any]:
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not t or t.startswith("/"):
        return {"handled": False}
    m = YES_RE.match(t)
    if m:                                                   # the user's own confirmation, completed without a model round
        r = _call("confirm_run", {"confirm_id": m.group(1)})
        msg = ("Started job `%s` (%s). Follow it with `/job %s`." % (r["job_id"], r.get("log", ""), r["job_id"])) if r.get("job_id") \
            else "Nothing started: %s" % r.get("error", "unknown id")
        return {"handled": True, "kind": "confirm", "reply": msg}
    d = nt.design_from_text(t)
    is_run = bool(RUN_RE.search(t) and _target_in(t))
    if OPEN_RE.search(t) and (WINDOW_RE.search(t) or re.match(r"(open|load|show|view)\b", t)) and not is_run:
        if d or LAYER_RE.search(t):
            tool = "magic" if "magic" in t else "klayout"
            r = _call("gui_command", {"text": text, "tool": tool})
            if r.get("ok"):
                did = ", ".join(x.get("op", "") + (" " + x["design"] if x.get("design") else "") for x in r.get("did", []))
                return {"handled": True, "kind": "window", "reply": "%s: %s (%s s)" % (tool, did, r.get("seconds")) +
                        ("\n\n" + r["markdown"] if r.get("markdown") else "")}
    elif LAYER_RE.search(t) and not is_run and not d:
        r = _call("gui_command", {"text": text})
        if r.get("ok"):
            return {"handled": True, "kind": "window", "reply": "done: " + ", ".join(x.get("op", "") for x in r.get("did", [])) +
                    ("\n\n" + r["markdown"] if r.get("markdown") else "")}
    if is_run and d and not re.search(r"\bhow\b|\bwhat\b|\bshould\b|\bcan i\b", t):
        tgt = _target_in(t)
        if tgt == "rebuild" or (tgt in FROZEN_WRITERS and _frozen(d)):
            r = _call("whatif_run", {"design": d, "rebuild": True})
            pre = ("`%s` is frozen, so this rebuilds it on a fresh copy under build/whatif/ (the design is untouched). " % d) if tgt != "rebuild" else ""
        elif tgt:
            r = _call("run_make", {"target": tgt, **({} if tgt in NO_DESIGN else {"design": d})})
            pre = "make %s DESIGN=%s. " % (tgt, d) if tgt not in NO_DESIGN else "make %s. " % tgt
        else:
            r, pre = {}, ""
        if r.get("confirm_id"):
            return {"handled": True, "kind": "gate", "reply": pre + r.get("say", "Reply 'yes, run %s' to start." % r["confirm_id"])}
        if r.get("error"):
            return {"handled": True, "kind": "gate", "reply": "Not started: %s" % r["error"]}
    if WHY_RE.search(t):                 # a why/how question: the quoted RAG answer, no tool round in the model
        r = _call("rag_answer", {"question": text})
        if r.get("found") and r.get("answer"):
            return {"handled": False, "kind": "rag",
                    "context": "[open-ai-chip RAG: quotes from the repo for this question (rag_answer, %s mode). Answer from these "
                               "quotes only, keep their file:line citations, call no tool unless they do not answer]\n%s" % (r.get("mode"), r["answer"])}
    if d:
        for name, rx, fn in FACT:
            if rx.search(t):
                return {"handled": False, "kind": "facts",
                        "context": "[open-ai-chip facts for %s, from the committed evidence; answer from these, cite the source file, "
                                   "call no tool unless they do not answer the question]\n%s" % (d, fn(d))}
    return {"handled": False}


@router.post("/quick", operation_id="quick", summary="Fast path for Hermes slash commands and plain chat messages (no model)", response_model=None)
def quick(req: QuickReq) -> dict:
    """For the Hermes plugin only (slash commands /klayout /magic /timing /synth /drc /lvs /run ...; and the pre_llm_call router)."""
    t0 = time.time()
    if req.cmd:
        reply = run_cmd(req.cmd, req.args or "")
        out = {"handled": True, "reply": reply}
    else:
        out = route_text(req.text or "")
    out["seconds"] = round(time.time() - t0, 2)
    _log({"cmd": req.cmd, "args": (req.args or "")[:120], "text": (req.text or "")[:120], "kind": out.get("kind"),
          "handled": out.get("handled"), "seconds": out["seconds"]})
    return out
