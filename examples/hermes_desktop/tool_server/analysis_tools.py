#!/usr/bin/env python3
"""Analysis tools for the Hermes desktop/browser agent: run_summary, diagnose, suggest, notes_section, explain.

Mounted by tool_server.py (_mount_extensions) as `router`; every endpoint is `POST /<tool_name>` (operationId == name).
The reasoning is deterministic code (thresholds, regex tables, NOTES.md parsing); the small model only picks a tool and
relays the result. Every answer carries evidence (file, key or quoted line) and a doc link. Read-only: nothing here
starts a flow, edits a file or loosens a constraint (CLAUDE.md HARD RULES).
Does NOT import tool_server.py (it imports this file). Reuses scripts/lib/repo.py, scripts/flow/find_reusable_run.py
and tools/eda_tools.py.
Docs: docs/HERMES_DESKTOP.md, examples/hermes_desktop/tool_server/README.md, .claude/skills/harden-design/reference.md
Tests: tests/tools/test_analysis_tools.py
"""
import glob
import importlib.util
import json
import math
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
for _p in (os.path.join(REPO, "tools"), os.path.join(REPO, "scripts", "lib"), os.path.join(REPO, "scripts", "flow")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import eda_tools  # noqa: E402
import repo  # noqa: E402
from fastapi import APIRouter  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

router = APIRouter()

JOB_DIR = os.path.join(REPO, "build", "agent", "jobs")
SKILL_REF = ".claude/skills/harden-design/reference.md"
SKILL_MD = ".claude/skills/harden-design/SKILL.md"
CORNER_SETUP = "max_ss_100C_1v60"
CORNER_HOLD = "min_ff_n40C_1v95"
# Thresholds (documented in suggest's output and docs/HERMES_DESKTOP.md). Clock is 25 ns.
SETUP_THIN_NS = 1.0        # setup slack under 1 ns on a 25 ns clock (4 %) is thin
HOLD_THIN_NS = 0.1         # hold slack under 0.1 ns is thin (committed designs sit at 0.10 .. 0.9 ns)
UTIL_LOW = 0.25            # instance utilisation under 25 % : die larger than needed
UTIL_HIGH = 0.60           # over 60 % : failures seen at 82 % and 115 % (harden-design reference, rows 1-2)
UTIL_TARGET = 0.40         # SKILL.md section 0: size DIE_AREA for about 40 % utilisation
FILL_SHARE = 0.5           # fill+tap above this share of the instance area is "mostly empty floorplan"
NEVER = ("Never loosen MAX_TRANSITION_CONSTRAINT or CLOCK_PERIOD (25 ns), never DISABLE_LVS, never turn "
         "ERROR_ON_SYNTH_CHECKS off, never SYNTH_STRATEGY DELAY (CLAUDE.md HARD RULES).")


# ---------------------------------------------------------------- helpers
def _rel(p: str) -> str:
    return os.path.relpath(p, REPO)


def _designs() -> List[str]:
    return repo.design_dirs()


def _check_design(d: Any) -> Optional[str]:
    if not isinstance(d, str) or not re.fullmatch(r"[A-Za-z0-9_]+", d) or d not in _designs():
        return "unknown design %r; valid: %s" % (d, ", ".join(_designs()))
    return None


def _json(path: str, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _lines(path: str, tail: Optional[int] = None) -> List[str]:
    try:
        with open(path, errors="replace") as f:
            ls = [l.rstrip("\n") for l in f]
    except OSError:
        return []
    return ls[-tail:] if tail else ls


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and not (isinstance(v, float) and math.isinf(v)) else None


def _fmt(v, nd=3):
    if isinstance(v, float):
        return round(v, nd)
    return v


_FIND = None


def _find_run_mod():
    """scripts/flow/find_reusable_run.py as a module (its CLI part is under __main__)."""
    global _FIND
    if _FIND is None:
        spec = importlib.util.spec_from_file_location("chip_find_reusable_run", os.path.join(REPO, "scripts", "flow", "find_reusable_run.py"))
        _FIND = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_FIND)
    return _FIND


def _runs(d: str) -> List[str]:
    return sorted(glob.glob(os.path.join(REPO, "designs", d, "runs", "RUN_*")), reverse=True)


def _resolve_run(d: str, which: Optional[str]) -> Tuple[Optional[str], Optional[str], str]:
    """(run_dir, error, how). 'current' = newest complete run whose inputs are unchanged (find_reusable_run.py);
    falls back to the newest complete run (reported as stale); 'latest' = newest directory; else a run directory name."""
    which = (which or "current").strip()
    runs = _runs(d)
    if which in ("current", "latest"):
        if not runs:
            return None, None, "none"
        if which == "latest":
            return runs[0], None, "latest"
        try:
            run, why = _find_run_mod().find_run(d)
        except Exception as e:  # noqa: BLE001
            run, why = None, "find_reusable_run failed: %s" % e
        if run:
            return run, None, "current"
        for r in runs:
            if os.path.isfile(os.path.join(r, "final", "metrics.json")):
                return r, None, "newest complete (not current: %s)" % why
        return runs[0], None, "latest (no complete run: %s)" % why
    if not re.fullmatch(r"RUN_[0-9_-]+", which):
        return None, "run must be 'current', 'latest' or a name like RUN_2026-10-06_07-00-35", ""
    p = os.path.join(REPO, "designs", d, "runs", which)
    if not os.path.isdir(p):
        return None, "no run %s for %s; have: %s" % (which, d, ", ".join(os.path.basename(r) for r in runs[:5]) or "none"), ""
    return p, None, "named"


def _metrics_of(d: str, run: Optional[str]) -> Tuple[Dict[str, Any], str]:
    if run:
        m = _json(os.path.join(run, "final", "metrics.json"))
        if m:
            return m, _rel(os.path.join(run, "final", "metrics.json"))
    p = os.path.join(REPO, "designs", d, "output", "metrics.json")
    return (_json(p, {}) or {}), _rel(p)


def _stages(d: str) -> List[Dict[str, Any]]:
    out = []
    for l in _lines(os.path.join(REPO, "build", "flow", d, "stages.txt")):
        p = l.split()
        if len(p) >= 2:
            out.append({"stage": p[0], "status": p[1], "seconds": int(p[2]) if len(p) > 2 and p[2].isdigit() else None,
                        "log": "build/flow/%s/stage_%s.log" % (d, p[0])})
    return out


def _worst(m: dict, prefix: str) -> Optional[dict]:
    vals = {k.split("corner:")[1]: v for k, v in m.items() if k.startswith(prefix + "__corner:") and _num(v) is not None}
    if not vals:
        v = _num(m.get(prefix))
        return {"slack_ns": round(v, 4), "corner": "all"} if v is not None else None
    c = min(vals, key=lambda k: vals[k])
    return {"slack_ns": round(vals[c], 4), "corner": c}


def _bbox_um(m: dict) -> Optional[Tuple[float, float]]:
    b = m.get("design__die__bbox")
    if isinstance(b, str):
        try:
            x0, y0, x1, y1 = [float(t) for t in b.split()]
            return x1 - x0, y1 - y0
        except ValueError:
            return None
    return None


def key_numbers(m: dict) -> dict:
    g = m.get
    die = _bbox_um(m)
    k = {
        "stdcells": g("design__instance__count__stdcell"),
        "flip_flops": g("design__instance__count__class:sequential_cell"),
        "die_um": "%g x %g" % die if die else None,
        "utilisation_pct": round(g("design__instance__utilization") * 100, 2) if _num(g("design__instance__utilization")) is not None else None,
        "setup_worst": _worst(m, "timing__setup__ws"),
        "hold_worst": _worst(m, "timing__hold__ws"),
        "drc": {"magic": g("magic__drc_error__count"), "klayout": g("klayout__drc_error__count"), "route": g("route__drc_errors")},
        "lvs_errors": g("design__lvs_error__count"),
        "xor_differences": g("design__xor_difference__count"),
        "antenna": {"violating_nets": g("antenna__violating__nets"), "violations": g("route__antenna_violation__count")},
        "max_slew_violations": g("design__max_slew_violation__count"),
        "max_cap_violations": g("design__max_cap_violation__count"),
        "max_fanout_violations": g("design__max_fanout_violation__count"),
        "power_total_mW": round(g("power__total") * 1000, 4) if _num(g("power__total")) is not None else None,
    }
    return k


def violations(m: dict) -> List[str]:
    """The conditions check_signoff.py fails on, from metrics only (no Docker/Yosys needed)."""
    v = []
    for key in ("magic__drc_error__count", "klayout__drc_error__count", "route__drc_errors", "design__lvs_error__count",
                "design__xor_difference__count", "antenna__violating__nets", "route__antenna_violation__count",
                "synthesis__check_error__count", "design__instance_unmapped__count", "design__inferred_latch__count"):
        if _num(m.get(key)):
            v.append("%s = %s" % (key, m[key]))
    for key, val in sorted(m.items()):
        if (key.startswith("timing__setup__ws") or key.startswith("timing__hold__ws")) and _num(val) is not None and val < 0:
            v.append("%s = %.3f ns (negative slack)" % (key, val))
    return v


def metric_diff(new: dict, old: dict, limit: int = 20) -> dict:
    changed = {}
    for k in sorted(set(new) | set(old)):
        a, b = new.get(k), old.get(k)
        same = a == b or (isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b)))
        if not same:
            changed[k] = {"run": _fmt(a, 6), "committed": _fmt(b, 6)}
    keys = list(changed)[:limit]
    return {"changed_keys": len(changed), "shown": {k: changed[k] for k in keys},
            "note": "only changed keys; empty means the run reproduces the committed output/metrics.json"}


def _job_info(job_id: str) -> Tuple[Optional[dict], Optional[str]]:
    if not re.fullmatch(r"[0-9]{8}_[0-9]{6}-[0-9]+", job_id or ""):
        return None, "bad job_id %r (expected like 20261006_101500-01)" % job_id
    p = os.path.join(JOB_DIR, job_id + ".log")
    if not os.path.isfile(p):
        return None, "no log for job %s (build/agent/jobs/%s.log)" % (job_id, job_id)
    ls = _lines(p)
    cmd = ls[0][2:] if ls and ls[0].startswith("$ ") else ""
    rc, secs = None, None
    for l in reversed(ls[-5:]):
        m = re.match(r"\[exit (-?\d+) after ([\d.]+)s\]", l)
        if m:
            rc, secs = int(m.group(1)), float(m.group(2))
            break
    mk = re.match(r"make (\S+)(?: DESIGN=(\S+))?", cmd)
    return {"job_id": job_id, "command": cmd, "rc": rc, "seconds": secs, "log": _rel(p),
            "state": "running" if rc is None else ("done" if rc == 0 else "failed"),
            "target": mk.group(1) if mk else None, "design": mk.group(2) if mk else None, "lines": ls}, None


# ---------------------------------------------------------------- failure table (harden-design reference.md, as data)
# (id, name, symptom regex over logs/metrics text, cause, fix, doc). Rows 1-13 of reference.md "Failure table".
FAILURES = [
    {"id": 1, "name": "repair step out of memory", "regex": r"out of memory|OOM|Killed|exit(?:ed)? (?:with )?(?:code )?137|\[Errno 12\]|MemoryError",
     "cause": "slew margins 70/40 with input ports whose transition is already over the limit: the repair step chases unfixable nets and exceeds the 8 GB cap",
     "fix": "set PL_RESIZER_MAX_SLEW_MARGIN and GRT_DESIGN_REPAIR_MAX_SLEW_PCT to 20", "doc": SKILL_REF + "#1-repair-step-runs-out-of-memory-container-capped-at-8-gb-profiletight"},
    {"id": 2, "name": "utilisation above 100 % (GPL-0301)", "regex": r"GPL-0301|utilization.*(?:exceed|above|over) 100|design too large|core area.*too small",
     "cause": "DIE_AREA too small for the cells (die can also be pin-limited)",
     "fix": "enlarge DIE_AREA (FP_SIZING absolute) to about 36-40 % utilisation, e.g. 80 -> 120 um", "doc": SKILL_REF + "#2-gpl-0301--utilisation-above-100--or-82--then-out-of-memory"},
    {"id": 3, "name": "pin_order.cfg comment lines", "regex": r"pin_order|IO_PIN_ORDER_CFG.*(?:not found|invalid)|pin .* not found",
     "cause": "LibreLane reads every line starting with '#' in pin_order.cfg as a direction marker",
     "fix": "remove comment lines from pin_order.cfg; put notes in a \"//IO_PIN_ORDER_CFG\" config key", "doc": SKILL_REF + "#3-pin_ordercfg-comments"},
    {"id": 4, "name": "lint PINNOTFOUND vccd1 on a macro", "regex": r"PINNOTFOUND",
     "cause": "the wrapper is linted against a macro netlist without power pins",
     "fix": "list the macro's powered netlist (pnl) in the wrapper config MACROS entry", "doc": SKILL_REF + "#4-lint-pinnotfound-vccd1-on-a-macro-instance-wrapper"},
    {"id": 5, "name": "global routing congestion (GRT-0116)", "regex": r"GRT-0116|congestion|overflow.*(?:global|routing)",
     "cause": "too many macro pins on one edge close to the wrapper pad row",
     "fix": "fewer macro pins ordered like the wrapper pads (pin_order.cfg), place the macro facing the pads", "doc": SKILL_REF + "#5-grt-0116--global-routing-congestion-in-the-wrapper"},
    {"id": 6, "name": "hold violations inside a wrapper", "regex": r"hold.*(?:violat|negative)|timing__hold__ws.*= -",
     "cause": "macro hardened with default constraints and long input wires; Caravel clock latency spread is 4.65-5.57 ns",
     "fix": "harden the macro with the Caravel macro SDC (PNR_SDC_FILE and SIGNOFF_SDC_FILE) and keep Wishbone wires short", "doc": SKILL_REF + "#6-hold-violations-inside-a-wrapper--0894-ns"},
    {"id": 7, "name": "unpowered antenna diodes in the wrapper", "regex": r"unpowered|nwell.*(?:drc|spacing)|design__lvs_error__count = [1-9]",
     "cause": "the wrapper's router added diodes on long macro-to-pad wires; the wrapper is elaborate-only so they are unpowered",
     "fix": "short wires, macro SDC (row 6), DIODE_ON_PORTS \"in\" inside the macro", "doc": SKILL_REF + "#7-unpowered-antenna-diodes-in-the-wrapper-lvs-70-errors-nwell-drc"},
    {"id": 8, "name": "max-slew / max-fanout piles from heuristic diode insertion", "regex": r"max_(?:slew|fanout)_violation__count = (?:[1-9]\d{2,})",
     "cause": "RUN_HEURISTIC_DIODE_INSERTION true on a big macro",
     "fix": "RUN_HEURISTIC_DIODE_INSERTION false plus CTS settings (sink clustering 8, diameter 20, buffer distance 30); classify slew first (classify_slew)", "doc": SKILL_REF + "#8-max-slew--max-fanout-from-heuristic-diode-insertion"},
    {"id": 9, "name": "float MAC setup misses", "regex": r"timing__setup__ws.*= -|setup.*(?:violat|negative slack)",
     "cause": "a floating-point MAC does not fit one 25 ns stage at max_ss_100C_1v60",
     "fix": "pipeline the product register, then RUN_POST_GRT_RESIZER_TIMING true and PL/GRT_RESIZER_SETUP_SLACK_MARGIN 0.5; the clock stays 25 ns", "doc": SKILL_REF + "#9-floating-point-mac-setup-misses-25-ns-clock-max_ss_100c_1v60"},
    {"id": 10, "name": "logic lost (check_signoff)", "regex": r"logic lost|registers removed by synthesis",
     "cause": "fewer sequential cells survive than the RTL elaborates to",
     "fix": "python3 scripts/flow/check_signoff.py <d> --breakdown; prove each missing bit dead and add an allowance with a reason in scripts/flow/signoff_allowances.json, or fix the RTL", "doc": SKILL_REF + "#10-logic-lost-in-check_signoff"},
    {"id": 11, "name": "undriven outputs of a wrapper", "regex": r"is used but has no driver",
     "cause": "a wrapper leaves io_out/io_oeb/la_data_out undriven",
     "fix": "explicit undriven_outputs + reason in scripts/flow/signoff_allowances.json (owner decision)", "doc": SKILL_REF + "#11-undriven-outputs-of-a-wrapper"},
    {"id": 12, "name": "flow killed at the time cap", "regex": r"FLOW_TIMEOUT|timed out after|Terminated|exit(?:ed)? (?:with )?(?:code )?124",
     "cause": "run_capped.sh stops a flow at FLOW_TIMEOUT (600 s); biggest real builds take 181 s, so a timeout is a hang",
     "fix": "shrink the problem first (die, margins); raise FLOW_TIMEOUT only if justified and recorded", "doc": SKILL_REF + "#12-timeouts"},
    {"id": 13, "name": "max-slew count that stays", "regex": r"max_slew_violation__count = [1-9]",
     "cause": "port-driven nets (Caravel input transition above the limit) cannot be fixed by resizing; more repair margin made it worse",
     "fix": "run classify_slew; reported, not failed on; do not loosen MAX_TRANSITION_CONSTRAINT", "doc": SKILL_REF + "#13-max-slew-counts-that-stay"},
    {"id": 14, "name": "run is stale (flow runs again)", "regex": r"started before .* was modified",
     "cause": "a file in a directory of one of the design's inputs changed or was added after the run started",
     "fix": "python3 scripts/flow/find_reusable_run.py <d> shows the file; re-run only if you accept it", "doc": SKILL_MD + "#2-reuse-and-staleness-scriptsflowfind_reusable_runpy-d"},
]
# rows whose regex is a metrics/check synthesised line, not a log line, are matched on the "findings" part of the corpus
_SOFT = {6, 8, 9, 13}


def _corpus(d: str, run: Optional[str], job: Optional[dict], m: dict) -> Tuple[List[Tuple[str, str]], List[str]]:
    """[(source, line)] evidence lines: job log tail, failing stage log tails, run error.log, flow.log tail, synthesised
    findings from the metrics. Returns (lines, failed_stages)."""
    out: List[Tuple[str, str]] = []
    failed = []
    if job:
        out += [("build/agent/jobs/%s.log" % job["job_id"], l) for l in job["lines"][-200:]]
    for s in _stages(d):
        if s["status"] != "PASS":
            failed.append(s["stage"])
            out += [(s["log"], l) for l in _lines(os.path.join(REPO, s["log"]), 80)]
    chk = os.path.join(REPO, "build", "flow", d, "stage_check.log")
    out += [(_rel(chk), l) for l in _lines(chk, 40) if "FAIL" in l or "logic lost" in l]
    if run:
        out += [(_rel(os.path.join(run, "error.log")), l) for l in _lines(os.path.join(run, "error.log"), 80)]
        if not os.path.isfile(os.path.join(run, "final", "metrics.json")):
            out += [(_rel(os.path.join(run, "flow.log")), l) for l in _lines(os.path.join(run, "flow.log"), 60)]
    for v in violations(m):
        out.append(("metrics", v))
    if _num(m.get("design__max_slew_violation__count")):
        out.append(("metrics", "design__max_slew_violation__count = %s" % m["design__max_slew_violation__count"]))
    return out, failed


def run_diagnose(d: str, run: Optional[str], job: Optional[dict], m: dict) -> dict:
    corpus, failed = _corpus(d, run, job, m)
    matches = []
    for row in FAILURES:
        rx = re.compile(row["regex"], re.I)
        ev = [(s, l.strip()) for s, l in corpus if rx.search(l)]
        if row["id"] in _SOFT:                      # metric-derived rows: need the metric line, not log chatter
            ev = [(s, l) for s, l in ev if s == "metrics"]
        if row["id"] == 13 and any(r["id"] == 8 for r in matches):
            continue
        if row["id"] == 8 and (_json(repo.config_path(d), {}) or {}).get("RUN_HEURISTIC_DIODE_INSERTION") is False:
            continue                                  # the fix of row 8 is already applied in this config
        if ev:
            matches.append({"id": row["id"], "name": row["name"], "cause": row["cause"], "fix": row["fix"], "doc": row["doc"],
                            "evidence": ["%s: %s" % (s, l[:200]) for s, l in ev[:3]], "_n": len(ev)})
    # row 8 vs 13: a pile of hundreds is the diode-insertion row, a few is "stays"; keep both only when they differ
    matches.sort(key=lambda r: (r["id"] in (8, 13), -r["_n"]))
    for r in matches:
        r.pop("_n")
    res = {"design": d, "failed_stages": failed, "matches": matches[:4]}
    if job:
        res["job"] = {k: job[k] for k in ("job_id", "command", "state", "rc", "seconds")}
        if job["rc"] not in (0, None):
            res["job_tail"] = [l for l in job["lines"][-12:]]
    only_info = all(r["id"] in (8, 13) for r in matches)
    hard = bool(failed) or bool(violations(m)) or (job is not None and job["rc"] not in (0, None))
    if not hard and only_info:
        res["clean"] = True
        res["summary"] = ("No failed stage and no violated signoff metric for %s. %s" % (
            d, ("Informational: " + matches[0]["name"] + " (reported, not failed on; see suggest).") if matches else "Nothing to fix."))
    else:
        res["clean"] = False
        if matches:
            t = matches[0]
            res["summary"] = "Most likely: %s. Cause: %s. Fix: %s. Doc: %s." % (t["name"], t["cause"], t["fix"], t["doc"])
        else:
            res["summary"] = ("A failure was found (%s) but it matches no row of the harden-design failure table; read the first "
                              "failing log lines in job_tail / the stage log and search_docs for the message." % (
                                  ", ".join(failed) or "; ".join(violations(m)[:2]) or "job rc %s" % (job or {}).get("rc")))
    res["never"] = NEVER
    return res


# ---------------------------------------------------------------- NOTES parsing
def _notes_path(d: str) -> str:
    return os.path.join(REPO, "designs", d, "NOTES.md")


def parse_sections(path: str) -> List[dict]:
    """[{heading, level, start, end, lines}] for every markdown heading outside code fences; end is the last line before the
    next heading of ANY level (a section's own text, not its subsections)."""
    ls = _lines(path)
    secs, fence = [], False
    for i, l in enumerate(ls):
        if l.startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"(#{1,6})\s+(.*\S)\s*$", l)
        if m:
            if secs:
                secs[-1]["end"] = i
            secs.append({"heading": m.group(2), "level": len(m.group(1)), "start": i + 1, "end": len(ls)})
    for s in secs:
        s["lines"] = ls[s["start"]:s["end"]]
    return secs


_ALIAS = {"intuitions": "Intuitions and insights", "insights": "Intuitions and insights", "intuition": "Intuitions and insights",
          "lessons": "Intuitions and insights", "timing": "Timing", "synthesis": "Synthesis", "verification": "Verification",
          "layout": "Layout", "floorplan": "Floorplan", "placement": "Placement", "clock": "Clock tree", "cts": "Clock tree",
          "routing": "Routing", "drc": "DRC", "lvs": "LVS", "power": "Power", "antenna": "Antenna", "slew": "Antenna",
          "reproduce": "Reproduce", "architecture": "Architecture", "data flow": "Data flow", "what it is": "What it is",
          "overview": "What it is", "runtime": "Run time and memory", "memory": "Run time and memory"}


def _section_text(lines: List[str]) -> str:
    return "\n".join(lines).strip("\n")


def find_sections(secs: List[dict], q: str) -> List[dict]:
    ql = q.strip().lower()
    target = _ALIAS.get(ql, q).lower()
    exact = [s for s in secs if s["heading"].lower().strip("` ") == target]
    if exact:
        return exact
    return [s for s in secs if target in s["heading"].lower() or ql in s["heading"].lower()]


_ITEM = re.compile(r"^(\d+)\.\s")


def _items(lines: List[str], base: int) -> List[dict]:
    """numbered list items: [{n, text, line}], each item is the line starting `N. ` plus its continuation lines."""
    items = []
    for i, l in enumerate(lines):
        m = _ITEM.match(l)
        if m:
            items.append({"n": int(m.group(1)), "start": base + i, "text": l})
        elif items and l.strip() and not l.startswith("#"):
            items[-1]["text"] += "\n" + l
    return items


class NotesReq(BaseModel):
    design: str = Field(..., description="design directory name, e.g. kv_attn_n8_int4 (see list_designs)", examples=["kv_attn_n8_int4"])
    section: Optional[str] = Field(None, description="NOTES.md section: intuitions, timing, synthesis, verification, layout, routing, "
                                   "architecture, ... or any heading words. Omit to list the headings.", examples=["intuitions"])
    item: Optional[int] = Field(None, description="optional: only numbered item N of that section (e.g. section intuitions, item 1)", ge=1)


@router.post("/notes_section", operation_id="notes_section", summary="Quote a section of a design's NOTES.md", response_model=None)
def notes_section(req: NotesReq) -> dict:
    """Return the EXACT text of a section of designs/<design>/NOTES.md (for 'why' questions about a design: intuitions,
    timing, synthesis, verification, ...). Fields: `quote` (verbatim text), `source` (file), `heading`, `lines`.
    Paste `quote` verbatim in your answer and cite `source`; do not paraphrase numbers. Optional `item` picks one numbered point."""
    err = _check_design(req.design)
    if err:
        return {"error": err}
    path = _notes_path(req.design)
    secs = parse_sections(path)
    if not secs:
        return {"error": "designs/%s/NOTES.md not found or has no headings" % req.design}
    if not req.section:
        return {"design": req.design, "source": _rel(path), "headings": [("#" * s["level"]) + " " + s["heading"] for s in secs],
                "say": "Call notes_section again with a section from this list."}
    hits = find_sections(secs, req.section)
    if not hits:
        return {"error": "no section matching %r in %s" % (req.section, _rel(path)),
                "headings": [s["heading"] for s in secs]}
    s = hits[0]
    ls = _lines(path)
    if req.item:
        items = _items(ls[s["start"]:s["end"]], s["start"] + 1)
        it = [x for x in items if x["n"] == req.item]
        if not it:
            return {"error": "section %r has no numbered item %d (items: %s)" % (s["heading"], req.item, [x["n"] for x in items])}
        text = it[0]["text"]
        lines = "%d" % it[0]["start"] if "\n" not in text else "%d-%d" % (it[0]["start"], it[0]["start"] + text.count("\n"))
    else:
        text = _section_text(s["lines"])
        lines = "%d-%d" % (s["start"] + 1, s["end"])
    cut = len(text) > 6000
    q = text[:6000]
    return {"design": req.design, "source": _rel(path), "heading": s["heading"], "lines": lines, "truncated": cut,
            "also_matching": [h["heading"] for h in hits[1:5]], "quote": q,
            "say": "Quote `quote` verbatim and cite %s, heading \"%s\", lines %s." % (_rel(path), s["heading"], lines)}


_STOP = set("a an the of to in on for and or is are was were be why how what does do did it its this that with as by at from "
            "have has had which than then so not no more less than vs versus about into out".split())


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[:-len(suf)]
    return w


def _tokens(s: str) -> List[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9_]+", s.lower().replace("flip-flop", "flipflop").replace("flops", "flipflop").replace("flop", "flipflop"))
            if t not in _STOP and len(t) > 1]


def paragraphs(path: str) -> List[dict]:
    """Blocks of NOTES.md: a numbered item, or a blank-line separated paragraph/table, with heading and line range."""
    ls = _lines(path)
    heading, blocks, cur, fence = "", [], None, False

    def flush():
        nonlocal cur
        if cur and "".join(cur["text"]).strip():
            cur["text"] = "\n".join(cur["text"]).strip("\n")
            blocks.append(cur)
        cur = None

    for i, l in enumerate(ls):
        if l.startswith("```"):
            fence = not fence
            flush()
            continue
        if fence:
            continue
        m = re.match(r"#{1,6}\s+(.*\S)", l)
        if m:
            flush()
            heading = m.group(1)
            continue
        if not l.strip():
            flush()
            continue
        if _ITEM.match(l):
            flush()
        if cur is None:
            cur = {"heading": heading, "start": i + 1, "end": i + 1, "text": []}
        cur["text"].append(l)
        cur["end"] = i + 1
    flush()
    return blocks


class ExplainReq(BaseModel):
    design: str = Field(..., description="design directory name, e.g. kv_attn_n8_int4", examples=["kv_attn_n8_int4"])
    topic: str = Field(..., description="what to explain, in a few words, e.g. 'flip-flops', 'slew', 'die size', 'why int4 is larger'",
                       examples=["flip-flops"])


@router.post("/explain", operation_id="explain", summary="Quote the NOTES.md passages that explain a topic", response_model=None)
def explain(req: ExplainReq) -> dict:
    """Answer 'why' about a design by QUOTING its NOTES.md: finds the best matching paragraphs for the topic and returns
    them verbatim with file, heading and line numbers (also designs/<d>/README.md if present). Paste `markdown` or each
    `quote` verbatim and cite the source; never paraphrase the numbers. If nothing matches it says so."""
    err = _check_design(req.design)
    if err:
        return {"error": err}
    want = set(_tokens(req.topic))
    if not want:
        return {"error": "topic is empty; give a few words such as 'flip-flops'"}
    scored = []
    for fn in ("NOTES.md", "README.md"):
        p = os.path.join(REPO, "designs", req.design, fn)
        for b in paragraphs(p):
            tk = _tokens(b["text"])
            ht = set(_tokens(b["heading"]))
            body = set(tk)
            hit = want & (body | ht)
            if not hit:
                continue
            score = len(hit) / len(want) * 10 + 1.5 * len(want & ht) + min(sum(1 for t in tk if t in want), 8) * 0.2
            if b["heading"].lower().startswith("intuitions"):
                score += 1.0
            if len(b["text"]) < 40:
                score -= 3
            scored.append((score, fn, p, b))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return {"design": req.design, "topic": req.topic, "matches": [],
                "say": "NOTES.md of %s has no passage about %r. Say so; do not guess. Try search_docs." % (req.design, req.topic)}
    out = []
    for sc, fn, p, b in scored[:3]:
        out.append({"source": _rel(p), "heading": b["heading"], "lines": "%d-%d" % (b["start"], b["end"]),
                    "score": round(sc, 2), "quote": b["text"][:3000],
                    "markdown": "> %s\n\n(%s, \"%s\", lines %d-%d)" % (b["text"][:3000].replace("\n", "\n> "), _rel(p), b["heading"], b["start"], b["end"])})
    return {"design": req.design, "topic": req.topic, "matches": out,
            "say": "Quote the first match verbatim and cite its source, heading and lines; add others only if asked."}


# ---------------------------------------------------------------- run_summary
class RunSummaryReq(BaseModel):
    design: Optional[str] = Field(None, description="design directory name, e.g. kv_attn_n8; omit when you give job_id", examples=["kv_attn_n8"])
    run: Optional[str] = Field("current", description="'current' (newest complete run whose inputs are unchanged, default), 'latest', "
                               "or a run directory name like RUN_2026-10-06_07-00-35")
    job_id: Optional[str] = Field(None, description="id of a finished run_make job (from run_make/job_list); summarises that job and points to its run")
    signoff_check: Optional[bool] = Field(False, description="also run scripts/flow/check_signoff.py (needs Docker for the register count; slower)")


def _signoff_run(d: str) -> dict:
    import subprocess
    try:
        r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "flow", "check_signoff.py"), d], capture_output=True,
                           text=True, timeout=90, cwd=REPO, env=dict(os.environ, **repo.docker_env()))
        ls = [l.strip() for l in (r.stdout + r.stderr).splitlines() if l.strip()]
        return {"exit_code": r.returncode, "verdict": ls[-1] if ls else "", "output": ls[-10:]}
    except Exception as e:  # noqa: BLE001
        return {"note": "check_signoff.py not run: %s" % type(e).__name__}


@router.post("/run_summary", operation_id="run_summary", summary="Summarise a flow run", response_model=None)
def run_summary(req: RunSummaryReq) -> dict:
    """Summarise a design's flow run from the run's own files: status per stage, cells, flip-flops, die, utilisation, worst
    setup/hold (with corner), DRC/LVS/XOR/antenna, slew/cap/fanout counts, power, wall time, peak memory, whether the run is
    current, what differs from the committed output/metrics.json, and a short plain-English `summary`. Give `design`
    (run: current|latest|<run dir name>) or the `job_id` of a finished run_make job. Relay `summary` and the key numbers."""
    job = None
    d = req.design
    if req.job_id:
        job, err = _job_info(req.job_id)
        if err:
            return {"error": err}
        d = d or job["design"]
        if not d:
            return {"job": {k: job[k] for k in ("job_id", "command", "state", "rc", "seconds", "log")},
                    "summary": "Job %s (%s) is %s, rc %s, %s s. It has no DESIGN, so there is no run to summarise; log: %s." % (
                        job["job_id"], job["command"], job["state"], job["rc"], job["seconds"], job["log"])}
    if not d:
        return {"error": "give design or job_id"}
    err = _check_design(d)
    if err:
        return {"error": err}
    run, err, how = _resolve_run(d, req.run)
    if err:
        return {"error": err}
    m, msrc = _metrics_of(d, run)
    committed = _json(os.path.join(REPO, "designs", d, "output", "metrics.json"), {}) or {}
    resources = _json(os.path.join(REPO, "designs", d, "output", "resources.json"), {}) or {}
    if run and resources.get("run_dir") and os.path.basename(resources["run_dir"]) != os.path.basename(run):
        resources = {}                                   # the committed resources belong to another run
    stages = _stages(d)
    out: Dict[str, Any] = {"design": d, "run": _rel(run) if run else None, "run_selection": how, "metrics_source": msrc}
    # currentness
    cur = None
    if run:
        try:
            r2, why = _find_run_mod().find_run(d)
            cur = {"current": bool(r2) and os.path.abspath(r2) == os.path.abspath(run), "reason": why}
            if r2 and os.path.abspath(r2) != os.path.abspath(run):
                cur["reason"] = "the current run is %s" % _rel(r2)
        except Exception as e:  # noqa: BLE001
            cur = {"current": None, "reason": "check failed: %s" % e}
    out["is_current"] = cur
    # logs
    logs = {}
    if run:
        fl = _lines(os.path.join(run, "flow.log"), 6)
        logs = {"flow_log_tail": fl,
                "flow_complete": any("Flow complete." in l for l in fl),
                "error_log_lines": len(_lines(os.path.join(run, "error.log"))),
                "warning_log_lines": len(_lines(os.path.join(run, "warning.log")))}
        steps = sorted(x for x in os.listdir(run) if re.match(r"\d\d-", x))
        logs["steps_done"] = len(steps)
        if steps:
            logs["last_step"] = steps[-1]
    out["logs"] = logs
    out["stages"] = [{"stage": s["stage"], "status": s["status"], "seconds": s["seconds"]} for s in stages]
    out["key_numbers"] = key_numbers(m) if m else {}
    wt = resources.get("wall_s_total")
    pm = resources.get("container_peak_mem_gb")
    out["resources"] = {"wall_s_total": wt, "container_peak_mem_gb": pm, "source": "designs/%s/output/resources.json" % d} if resources else {}
    out["violations"] = violations(m) if m else []
    out["diff_vs_committed"] = metric_diff(m, committed) if (m and committed and msrc != _rel(os.path.join(REPO, "designs", d, "output", "metrics.json"))) else \
        {"changed_keys": 0, "shown": {}, "note": "numbers are the committed output/metrics.json itself" if msrc.endswith("output/metrics.json") else "no committed metrics"}
    if req.signoff_check:
        out["check_signoff"] = _signoff_run(d)
    if job:
        out["job"] = {k: job[k] for k in ("job_id", "command", "state", "rc", "seconds", "log")}
    # plain-English summary
    k = out["key_numbers"]
    sl = []
    if job:
        sl.append("Job %s (%s) %s with rc %s after %s s." % (job["job_id"], job["command"], job["state"], job["rc"], job["seconds"]))
    if not m:
        sl.append("%s has no metrics.json (no complete run and no committed output); stages: %s." % (
            d, ", ".join("%s %s" % (s["stage"], s["status"]) for s in stages) or "none recorded"))
    else:
        failed = [s["stage"] for s in stages if s["status"] != "PASS"]
        sl.append("%s: %s. %s" % (d, ("stage(s) failed: " + ", ".join(failed)) if failed else
                                  ("all %d recorded stages passed" % len(stages) if stages else "no stage record in build/flow"),
                                  ("Signoff metrics violated: " + "; ".join(out["violations"][:3]) + ".") if out["violations"] else
                                  "DRC, LVS, XOR, antenna and slack metrics are clean."))
        sl.append("%s std cells, %s flip-flops, die %s um, utilisation %s %%." % (k["stdcells"], k["flip_flops"], k["die_um"], k["utilisation_pct"]))
        sw, hw = k["setup_worst"], k["hold_worst"]
        if sw and hw:
            sl.append("Worst setup slack %s ns (%s), worst hold slack %s ns (%s); slew violations %s, cap %s, fanout %s (reported, not failed on)." % (
                sw["slack_ns"], sw["corner"], hw["slack_ns"], hw["corner"], k["max_slew_violations"], k["max_cap_violations"], k["max_fanout_violations"]))
        if wt is not None:
            sl.append("Flow took %s s, peak memory %s GB (resources.json)." % (wt, pm))
    if cur is not None:
        sl.append("Run %s is %s%s." % (out["run"], "current" if cur["current"] else "NOT current",
                                       "" if cur["current"] else " (%s)" % cur["reason"]))
    elif not run:
        sl.append("No local run directory; numbers are from the committed designs/%s/output/metrics.json." % d)
    dv = out["diff_vs_committed"]
    if dv.get("changed_keys"):
        sl.append("%d metric key(s) differ from the committed metrics.json (see diff_vs_committed)." % dv["changed_keys"])
    out["summary"] = " ".join(sl[:6])
    return out


# ---------------------------------------------------------------- diagnose
class DiagnoseReq(BaseModel):
    design: Optional[str] = Field(None, description="design directory name, e.g. kv_attn_n8; omit when you give job_id", examples=["kv_attn_n8"])
    job_id: Optional[str] = Field(None, description="id of a finished run_make job whose failure to explain")


@router.post("/diagnose", operation_id="diagnose", summary="Diagnose a failed stage or violated check", response_model=None)
def diagnose(req: DiagnoseReq) -> dict:
    """Find out why a stage or check failed: matches the logs and metrics of a design (or of a run_make job) against the
    harden-design failure table and returns the top matches with cause, fix, evidence lines and a doc link. If nothing
    failed it says `clean: true`. Give `design` or `job_id`. Relay `summary`; never suggest loosening constraints."""
    job = None
    d = req.design
    if req.job_id:
        job, err = _job_info(req.job_id)
        if err:
            return {"error": err}
        d = d or job["design"]
    if not d:
        return {"error": "give design or job_id (the job has no DESIGN)"}
    err = _check_design(d)
    if err:
        return {"error": err}
    run, err, _ = _resolve_run(d, "latest" if job else "current")
    if err:
        return {"error": err}
    m, _src = _metrics_of(d, run)
    return run_diagnose(d, run, job, m)


# ---------------------------------------------------------------- suggest
class SuggestReq(BaseModel):
    design: str = Field(..., description="design directory name, e.g. kv_attn_n8 (see list_designs)", examples=["kv_attn_n8"])
    classify: Optional[bool] = Field(True, description="run classify_slew (port-driven vs internal) when there are slew violations and a local run; set false for a fast answer")


def _rule(rid, sev, title, evidence, advice, doc):
    return {"rule": rid, "severity": sev, "title": title, "evidence": evidence, "advice": advice, "doc": doc}


def _allowances(d: str) -> dict:
    return ((_json(os.path.join(REPO, "scripts", "flow", "signoff_allowances.json"), {}) or {}).get("designs") or {}).get(d, {})


def _used_as_macro_in(d: str) -> List[str]:
    out = []
    for w in _designs():
        if w != d and d in (_json(repo.config_path(w), {}).get("MACROS") or {}):
            out.append(w)
    return out


def suggest_rules(d: str, m: dict, run: Optional[str], cur: Optional[dict], classify: bool) -> List[dict]:
    R = []
    g = m.get
    src = "designs/%s/output/metrics.json" % d
    sw, hw = _worst(m, "timing__setup__ws"), _worst(m, "timing__hold__ws")
    if sw and sw["slack_ns"] < SETUP_THIN_NS:
        R.append(_rule("thin_setup", "warn", "Setup margin is thin", "setup worst slack %s ns at %s (%s); threshold %s ns on a 25 ns clock" % (
            sw["slack_ns"], sw["corner"], src, SETUP_THIN_NS),
            "Pipeline the long path (RTL change), or use the tool-only setup repair keys of harden-design row 9. Keep CLOCK_PERIOD at 25 ns. " + NEVER, SKILL_REF + "#9-floating-point-mac-setup-misses-25-ns-clock-max_ss_100c_1v60"))
    elif sw:
        R.append(_rule("setup_margin_ok", "info", "Setup margin is comfortable", "setup worst slack %s ns at %s, above the %s ns threshold (%s)" % (
            sw["slack_ns"], sw["corner"], SETUP_THIN_NS, src), "Timing is not the limit at 40 MHz; no action.", SKILL_MD + "#3-reading-results"))
    if hw and hw["slack_ns"] < HOLD_THIN_NS:
        R.append(_rule("thin_hold", "warn", "Hold margin is thin", "hold worst slack %s ns at %s (%s); threshold %s ns" % (hw["slack_ns"], hw["corner"], src, HOLD_THIN_NS),
                       "Hold is repaired by buffers (hold_buffer count %s). In a wrapper harden the macro with the Caravel macro SDC (row 6). Never relax checks." % g("design__instance__count__hold_buffer"),
                       SKILL_REF + "#6-hold-violations-inside-a-wrapper--0894-ns"))
    elif hw and hw["slack_ns"] < 0.2:
        R.append(_rule("hold_smallest_margin", "info", "Hold is the tighter margin", "hold worst slack %s ns at %s vs setup %s ns (%s)" % (
            hw["slack_ns"], hw["corner"], sw["slack_ns"] if sw else "?", src), "Positive and repaired; recheck after any RTL or constraint change.", SKILL_MD + "#3-reading-results"))
    # slew
    nslew = _num(g("design__max_slew_violation__count"))
    if nslew:
        ev = "%d max-slew violations (reported, not failed on) in %s" % (nslew, src)
        advice = "Classify them before fixing: port-driven nets cannot be fixed by resizing and more repair margin made it worse (tiny_ai_core //SLEW). " + NEVER
        if classify and run:
            try:
                c = eda_tools.call("classify_slew", {"design": d})
                if "output" in c:
                    ev += "; classify_slew: " + " | ".join(c["output"][:4])
            except Exception as e:  # noqa: BLE001
                ev += "; classify_slew unavailable (%s)" % type(e).__name__
        elif classify:
            ev += "; classify_slew needs a local run dir (none here)"
        R.append(_rule("slew_classify", "info", "Slew violations: environment-limited vs internal", ev, advice, SKILL_REF + "#13-max-slew-counts-that-stay"))
    # utilisation / die
    ut = _num(g("design__instance__utilization"))
    die = _bbox_um(m)
    if ut is not None and die:
        side = math.sqrt(die[0] * die[1])
        want = side * math.sqrt(ut / UTIL_TARGET)
        if ut > UTIL_HIGH:
            R.append(_rule("util_high", "warn", "Utilisation is high", "utilisation %.1f %% on a %g x %g um die (%s); above %d %%" % (ut * 100, die[0], die[1], src, UTIL_HIGH * 100),
                           "Grow DIE_AREA toward ~%d %% utilisation (about %.0f x %.0f um for this cell area); 82 %% ran out of memory and 115 %% was refused. Do not touch the slew margins to compensate. %s" % (UTIL_TARGET * 100, want, want, NEVER),
                           SKILL_REF + "#2-gpl-0301--utilisation-above-100--or-82--then-out-of-memory"))
        elif ut < UTIL_LOW:
            R.append(_rule("util_low", "info", "Utilisation is low", "utilisation %.1f %% on a %g x %g um die (%s); below %d %%" % (ut * 100, die[0], die[1], src, UTIL_LOW * 100),
                           "The die is bigger than the logic needs (about %.0f x %.0f um would give ~%d %%). It may be pin-limited (109 Wishbone pins need an edge: tiny_ai_core 250 um); if not, shrink DIE_AREA to save fill, then re-run. Low utilisation never fails a check." % (want, want, UTIL_TARGET * 100),
                           SKILL_REF + "#2-gpl-0301--utilisation-above-100--or-82--then-out-of-memory"))
    # fill / tap / area split
    inst, fill, tap, std = (_num(g("design__instance__area")), _num(g("design__instance__area__class:fill_cell")),
                            _num(g("design__instance__area__class:tap_cell")), _num(g("design__instance__area__stdcell")))
    if inst and fill is not None and tap is not None and std is not None and (fill + tap) / inst > FILL_SHARE:
        R.append(_rule("fill_tap_split", "info", "Most of the area is fill and tap cells, not logic",
                       "instance area %.0f um^2 = std cells %.0f + ... ; fill %.0f um^2 (%d cells) and tap %.0f um^2 (%d cells) = %.0f %% of it (%s)" % (
                           inst, std, fill, g("design__instance__count__class:fill_cell") or 0, tap, g("design__instance__count__class:tap_cell") or 0, (fill + tap) / inst * 100, src),
                       "Fill and tap are floorplan overhead that scale with the die, so compare designs by synthesised area or std-cell area (design__instance__area__stdcell), not by final cell count. See the 'Final area is a floorplan story' insight in kv_attn_n8_int4/NOTES.md.",
                       "designs/kv_attn_n8_int4/NOTES.md#intuitions-and-insights"))
    # stale
    if cur is not None and cur.get("current") is False:
        R.append(_rule("stale_run", "warn", "The run is not current", "find_reusable_run.py: %s" % cur.get("reason"),
                       "Inputs changed after the run started. Re-run the flow only if you accept it: `make flow-all DESIGN=%s` (ask me and confirm; one physical flow at a time). Metrics should be identical for pure additions." % d,
                       SKILL_MD + "#2-reuse-and-staleness-scriptsflowfind_reusable_runpy-d"))
    # macro views
    cfg = _json(repo.config_path(d), {}) or {}
    for mac in (cfg.get("MACROS") or {}):
        if os.path.isfile(repo.config_path(mac)) and not os.path.isfile(os.path.join(REPO, "build", "macros", mac, "SOURCE.txt")):
            R.append(_rule("missing_views", "warn", "Macro views missing for %s" % mac, "build/macros/%s/SOURCE.txt does not exist" % mac,
                           "Export them first: `make views DESIGN=%s` (needs a current run of %s; else `make gds DESIGN=%s`)." % (mac, mac, mac), SKILL_MD + "#1-the-one-command"))
    users = _used_as_macro_in(d)
    if users and not os.path.isfile(os.path.join(REPO, "build", "macros", d, "SOURCE.txt")):
        R.append(_rule("missing_views_for_wrapper", "info", "%s is a macro of %s but has no exported views" % (d, ", ".join(users)),
                       "build/macros/%s/SOURCE.txt does not exist" % d, "`make views DESIGN=%s` before building the wrapper (`make wrapper`)." % d, SKILL_MD + "#1-the-one-command"))
    # flops removed
    al = _allowances(d)
    if al.get("removed_registers"):
        R.append(_rule("flops_removed", "info", "Synthesis removes %s register bits (allowance)" % al["removed_registers"],
                       "scripts/flow/signoff_allowances.json: removed_registers %s, reason: %s" % (al["removed_registers"], str(al.get("reason", ""))[:240]),
                       "Expected and proven in the allowance. A new removed flop beyond that allowance fails check_signoff (logic lost). Never raise the allowance without a reason verifiable in the RTL.",
                       SKILL_REF + "#10-logic-lost-in-check_signoff"))
    if al.get("undriven_outputs"):
        R.append(_rule("undriven_outputs", "info", "Wrapper outputs left undriven by decision", "signoff_allowances.json undriven_outputs %s" % al["undriven_outputs"],
                       "Owner decision recorded with a reason; tapeout caveat: floating io_oeb.", SKILL_REF + "#11-undriven-outputs-of-a-wrapper"))
    # learning suggestions from NOTES
    notes = _notes_path(d)
    secs = find_sections(parse_sections(notes), "intuitions")
    if secs:
        ls = _lines(notes)
        items = _items(ls[secs[0]["start"]:secs[0]["end"]], secs[0]["start"] + 1)
        heads = []
        for it in items[:8]:
            mm = re.match(r"\d+\.\s+\*\*(.+?)\*\*", it["text"])
            heads.append("%d. %s" % (it["n"], mm.group(1) if mm else it["text"][:100]))
        if heads:
            R.append(_rule("learn_next", "learn", "What to read or try next (from this design's own Intuitions and insights)",
                           "designs/%s/NOTES.md, section \"Intuitions and insights\" (%d items): %s" % (d, len(items), " | ".join(heads[:5])),
                           "Ask me `notes_section {design: %s, section: intuitions, item: N}` to read an item verbatim, then pick one open question (items that say 'I did not' or 'my inference' are untested) and compare with a sibling design via compare_designs." % d,
                           "designs/%s/NOTES.md#intuitions-and-insights" % d))
    return R


@router.post("/suggest", operation_id="suggest", summary="Rule-based suggestions for a design", response_model=None)
def suggest(req: SuggestReq) -> dict:
    """Rule-based suggestions for a design, each with evidence (file/key/number) and a doc link: thin setup/hold margin,
    slew classification, high/low utilisation (die resize hint), stale run, missing macro views, fill/tap area split,
    flops removed by synthesis, and what to read or try next from the design's NOTES. Never suggests loosening
    MAX_TRANSITION/CLOCK_PERIOD or disabling LVS. Relay `rules` (title, evidence, advice, doc)."""
    err = _check_design(req.design)
    if err:
        return {"error": err}
    d = req.design
    run, _e, _how = _resolve_run(d, "current")
    m, _src = _metrics_of(d, run)
    if not m:
        return {"design": d, "rules": [], "summary": "%s is not hardened (no metrics.json); harden it with the harden-design skill first." % d}
    cur = None
    if run:
        try:
            r2, why = _find_run_mod().find_run(d)
            cur = {"current": bool(r2), "reason": why}
        except Exception:  # noqa: BLE001
            cur = None
    rules = suggest_rules(d, m, run, cur, bool(req.classify))
    warn = [r for r in rules if r["severity"] == "warn"]
    return {"design": d, "thresholds": {"setup_thin_ns": SETUP_THIN_NS, "hold_thin_ns": HOLD_THIN_NS, "util_low": UTIL_LOW,
                                        "util_high": UTIL_HIGH, "util_target": UTIL_TARGET, "fill_tap_share": FILL_SHARE},
            "rules": rules, "never": NEVER,
            "summary": "%d suggestion(s) for %s, %d need attention: %s" % (len(rules), d, len(warn), "; ".join(r["title"] for r in (warn or rules)[:4]))}
