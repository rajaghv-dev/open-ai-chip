#!/usr/bin/env python3
"""Log and file tools for the Hermes tool server (a FastAPI APIRouter, auto-mounted by tool_server.py). Read-only.

Tools (POST /<name>, operationId == name):
  list_logs {design? | job_id?}   every log of a design (run dir flow/error/warning, per-step logs, build/flow stage logs, make log,
                                  simulation and gate-level logs, precheck/caravel logs) or of a job, with sizes and times
  read_log {design?, which, tail?, grep?, around?, context?}
                                  which = flow | error | warning | make | stage:<name> | step:<NN or name> | sim | gl | job:<id> | precheck
                                  | a repo-relative path; the last `tail` lines, or the lines matching `grep`, or `around` a line
  log_digest {design | job_id}    code-built digest: errors and warnings grouped by tool/step with counts and the first example line,
                                  the slowest steps (runtime.txt), the stage outcomes (build/flow/<d>/stages.txt)
  open_gds {design, viewer}       klayout-app (the KLayout desktop application with the sky130 layer file) | klayout | magic (the
                                  controllable windows of gui_start) | png (offscreen render)
  open_file {path, start?, end?, viewer?}
                                  the text of a repo file (md/txt/rpt/json/log/...) with a line range, or open it in the OS viewer
Every reply carries a `markdown` code block or text for the chat. Safety: paths are repo-relative, resolved with realpath and must stay
under the repo root (no traversal, no symlink escape, no .git or secrets); only text extensions; size and line caps; nothing is written
except one line per viewer launch in build/agent/proof/opens.jsonl. The proof call log (proof_tools.py) records the files read here
because every file is read with the builtin open().
Docs: docs/HERMES_DESKTOP.md ("Everything you can ask"), docs/GUI_AND_LOGS.md, examples/hermes_desktop/tool_server/README.md
Tests: tests/tools/test_logs_open.py
"""
import collections
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ROOT = os.environ.get("CHIP_LOGS_ROOT") or REPO          # tests point this at a fixture tree
for _p in (os.path.join(REPO, "tools"), os.path.join(REPO, "scripts", "lib"), os.path.join(REPO, "examples", "hermes_klayout_gui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

router = APIRouter()
PORT = int(os.environ.get("CHIP_TOOLS_PORT", "8770"))
TEXT_EXT = {".log", ".txt", ".rpt", ".md", ".json", ".csv", ".err", ".out", ".v", ".sv", ".vh", ".py", ".sh", ".sdc", ".cfg", ".tcl",
            ".yaml", ".yml", ".lef", ".c", ".h", ".mk", ".s", ".xml", ".drc", ".lyp", ".hex"}
TEXT_NAMES = {"Makefile", "COMMANDS", "LICENSE"}
DENY_PARTS = {".git", ".ssh", ".gnupg", ".aws", ".env", "node_modules", "__pycache__", "venv"}
MAX_READ_BYTES = 200_000_000     # a larger file is only tailed
MAX_LINES = 300                  # lines returned by read_log / open_file
DEFAULT_TAIL = 60
MAX_LINE = 400                   # characters kept per line
MAX_CHARS = 24_000
DESIGN_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}$")
JOB_RE = re.compile(r"^[0-9]{8}_[0-9]{6}-[0-9]{1,3}$")
STEP_RE = re.compile(r"^(\d{2,3})-(.+)$")
# lines that are errors / warnings in LibreLane step logs, yosys, openroad, magic, klayout, netgen, iverilog
ERR_RE = re.compile(r"(\[ERROR[^\]]*\]|\bERROR\b|\bError:|\bFATAL\b|\bfailed\b|Traceback|\[FLW-\d+\]|\[GPL-0301\]|\[GRT-0116\])")
WARN_RE = re.compile(r"(\[WARNING[^\]]*\]|\bWARNING\b|\bWarning:)")
CODE_RE = re.compile(r"\[([A-Z]{2,5}-\d{3,4})\]")


class LogError(Exception):
    pass


# ---------------------------------------------------------------- path safety
def safe_path(rel: str, must_exist: bool = True, text_only: bool = True) -> str:
    """Repo-relative path -> absolute path under ROOT, or LogError. No absolute paths, no '..', no NUL, no symlink escape,
    no .git/secret parts, text extensions only."""
    if not isinstance(rel, str) or not rel.strip():
        raise LogError("path is empty")
    rel = rel.strip()
    if "\x00" in rel or "\\" in rel:
        raise LogError("bad character in path")
    if os.path.isabs(rel) or rel.startswith("~"):
        # an absolute path inside the repo is accepted by turning it into a repo-relative one
        r0 = os.path.realpath(ROOT)
        if os.path.isabs(rel) and os.path.realpath(rel).startswith(r0 + os.sep):
            rel = os.path.relpath(os.path.realpath(rel), r0)
        else:
            raise LogError("only repo-relative paths are allowed (got %r)" % rel)
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise LogError("'..' is not allowed in a path")
    if any(p in DENY_PARTS or p.startswith(".env") for p in parts):
        raise LogError("this path is not readable (%s)" % rel)
    root = os.path.realpath(ROOT)
    ap = os.path.realpath(os.path.join(root, *parts))
    if ap != root and not ap.startswith(root + os.sep):
        raise LogError("path leaves the repo")
    if must_exist and not os.path.isfile(ap):
        raise LogError("no such file: %s" % rel)
    if text_only:
        ext = os.path.splitext(ap)[1].lower()
        if ext not in TEXT_EXT and os.path.basename(ap) not in TEXT_NAMES:
            raise LogError("only text files can be read (%s); allowed: %s" % (ext or "no extension", " ".join(sorted(TEXT_EXT))))
    return ap


def rel(ap: str) -> str:
    return os.path.relpath(ap, os.path.realpath(ROOT))


def check_design(d: Optional[str]) -> str:
    if not d or not DESIGN_RE.match(d):
        raise LogError("design must be a name like kv_attn_n8")
    if not os.path.isdir(os.path.join(ROOT, "designs", d)):
        names = sorted(os.listdir(os.path.join(ROOT, "designs"))) if os.path.isdir(os.path.join(ROOT, "designs")) else []
        raise LogError("unknown design %r; have: %s" % (d, ", ".join(names[:30])))
    return d


def run_dirs(d: str) -> List[str]:
    return sorted(glob.glob(os.path.join(ROOT, "designs", d, "runs", "RUN_*")), reverse=True)


def latest_run(d: str) -> Optional[str]:
    """Newest complete run (has final/metrics.json), else the newest directory."""
    runs = run_dirs(d)
    for r in runs:
        if os.path.isfile(os.path.join(r, "final", "metrics.json")):
            return r
    return runs[0] if runs else None


def step_dirs(run: str) -> List[str]:
    return sorted(p for p in glob.glob(os.path.join(run, "*")) if os.path.isdir(p) and STEP_RE.match(os.path.basename(p)))


def step_logs(sd: str) -> List[str]:
    return sorted(glob.glob(os.path.join(sd, "*.log")))


def _fmt_size(n: int) -> str:
    return "%d B" % n if n < 1024 else "%.1f KB" % (n / 1024) if n < 1048576 else "%.1f MB" % (n / 1048576)


def _stat(ap: str) -> Dict[str, Any]:
    st = os.stat(ap)
    return {"path": rel(ap), "bytes": st.st_size, "size": _fmt_size(st.st_size),
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))}


def _fence(text: str) -> str:
    return "```\n%s\n```" % text.replace("```", "'''")


# ---------------------------------------------------------------- listing
def _design_logs(d: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def add(kind, ap, name=None):
        if os.path.isfile(ap):
            e = _stat(ap)
            e["kind"] = kind
            e["name"] = name or os.path.basename(ap)
            out.append(e)

    run = latest_run(d)
    if run:
        for n, k in (("flow.log", "flow"), ("error.log", "error"), ("warning.log", "warning")):
            add(k, os.path.join(run, n), "%s (%s)" % (n, os.path.basename(run)))
        for sd in step_dirs(run):
            for lg in step_logs(sd):
                add("step", lg, "step:%s" % os.path.basename(sd))
    for lg in sorted(glob.glob(os.path.join(ROOT, "build", "flow", d, "stage_*.log"))):
        add("stage", lg, "stage:" + os.path.basename(lg)[6:-4])
    add("stages", os.path.join(ROOT, "build", "flow", d, "stages.txt"))
    add("make", os.path.join(ROOT, "build", "flow_%s.log" % d), "make (build/flow_%s.log)" % d)
    add("sim", os.path.join(ROOT, "build", "sim", d, "sim.log"), "sim")
    for lg in sorted(glob.glob(os.path.join(ROOT, "build", "gl", d, "sim", "*.log"))) + [os.path.join(ROOT, "build", "gl", d, "result.txt"),
                                                                                      os.path.join(ROOT, "build", "gl", d, "synth_checks.txt")]:
        add("gl", lg, "gl:" + os.path.basename(lg))
    for lg in sorted(glob.glob(os.path.join(ROOT, "build", "precheck", "*.log")))[-3:]:
        add("precheck", lg, "precheck:" + os.path.basename(lg))
    return out


def list_logs_for(design: Optional[str] = None, job_id: Optional[str] = None) -> Dict[str, Any]:
    if job_id:
        if not JOB_RE.match(job_id):
            raise LogError("job_id looks like 20261006_193459-01")
        ap = os.path.join(ROOT, "build", "agent", "jobs", job_id + ".log")
        if not os.path.isfile(ap):
            names = sorted(os.listdir(os.path.join(ROOT, "build", "agent", "jobs")))[-5:] if os.path.isdir(os.path.join(ROOT, "build", "agent", "jobs")) else []
            raise LogError("no log for job %s; recent: %s" % (job_id, ", ".join(n[:-4] for n in names) or "none"))
        e = _stat(ap)
        e.update(kind="job", name="job:" + job_id)
        return {"job_id": job_id, "logs": [e], "markdown": _fence("job:%s  %s  %s  %s" % (job_id, e["path"], e["size"], e["modified"]))}
    if design:
        check_design(design)
        logs = _design_logs(design)
        rows = ["%-8s %-42s %9s  %s" % (e["kind"], e["name"][:42], e["size"], e["modified"]) for e in logs]
        shown = rows[:45]
        if len(rows) > 45:
            shown.append("... %d more (step logs); read one with which=step:<NN or name>" % (len(rows) - 45))
        run = latest_run(design)
        head = "logs of %s (run %s)" % (design, os.path.basename(run) if run else "none")
        return {"design": design, "run": os.path.basename(run) if run else None, "count": len(logs), "logs": logs,
                "markdown": _fence(head + "\n" + "\n".join(shown)) if logs else "No logs for %s yet: run make flow-all DESIGN=%s." % (design, design)}
    # neither: the newest jobs, the stage logs and precheck logs, overall
    jobs = sorted(glob.glob(os.path.join(ROOT, "build", "agent", "jobs", "*.log")))[-8:]
    logs = []
    for ap in jobs:
        e = _stat(ap)
        e.update(kind="job", name="job:" + os.path.basename(ap)[:-4])
        logs.append(e)
    for ap in sorted(glob.glob(os.path.join(ROOT, "build", "flow_*.log")))[-8:] + sorted(glob.glob(os.path.join(ROOT, "build", "precheck", "*.log")))[-3:]:
        e = _stat(ap)
        e.update(kind="make" if "flow_" in ap else "precheck", name=os.path.basename(ap))
        logs.append(e)
    rows = ["%-8s %-34s %9s  %s" % (e["kind"], e["name"][:34], e["size"], e["modified"]) for e in logs]
    return {"count": len(logs), "logs": logs, "markdown": _fence("recent logs (pass design or job_id for more)\n" + "\n".join(rows))}


# ---------------------------------------------------------------- which -> path
def resolve_which(design: Optional[str], which: str) -> Tuple[str, str]:
    """(absolute path, label). `which` is a keyword, a prefix form (stage:, step:, job:) or a repo-relative path."""
    w = (which or "").strip()
    if not w:
        raise LogError("which is required (flow, error, warning, make, stage:<name>, step:<NN or name>, sim, gl, job:<id> or a path)")
    wl = w.lower()
    if wl.startswith("job:"):
        jid = w[4:].strip()
        if not JOB_RE.match(jid):
            raise LogError("job id looks like 20261006_193459-01")
        ap = os.path.join(ROOT, "build", "agent", "jobs", jid + ".log")
        if not os.path.isfile(ap):
            raise LogError("no log for job %s" % jid)
        return ap, "job:" + jid
    if wl in ("precheck", "precheck:latest"):
        c = sorted(glob.glob(os.path.join(ROOT, "build", "precheck", "run*.log")))
        if not c:
            raise LogError("no precheck log in build/precheck/")
        return c[-1], "precheck"
    if wl in ("flow.log", "error.log", "warning.log"):
        wl = w = wl[:-4]
    if "/" in w or (os.path.splitext(w)[1] and wl not in ("flow", "error", "warning")):
        return safe_path(w), "path"
    d = check_design(design) if design else None
    if d is None:
        raise LogError("design is required for which=%s" % w)
    run = latest_run(d)
    if wl in ("flow", "error", "warning", "warnings", "errors"):
        if not run:
            raise LogError("%s has no run directory yet (designs/%s/runs/)" % (d, d))
        n = {"flow": "flow.log", "error": "error.log", "errors": "error.log", "warning": "warning.log", "warnings": "warning.log"}[wl]
        return os.path.join(run, n), "%s %s" % (os.path.basename(run), n)
    if wl == "make":
        ap = os.path.join(ROOT, "build", "flow_%s.log" % d)
        if os.path.isfile(ap):
            return ap, "make log"
        raise LogError("no build/flow_%s.log" % d)
    if wl == "stages":
        return os.path.join(ROOT, "build", "flow", d, "stages.txt"), "stages"
    if wl.startswith("stage:"):
        nm = re.sub(r"[^a-z0-9_]", "_", w[6:].strip().lower().replace("-", "_"))
        ap = os.path.join(ROOT, "build", "flow", d, "stage_%s.log" % nm)
        if os.path.isfile(ap):
            return ap, "stage " + nm
        have = [os.path.basename(p)[6:-4] for p in glob.glob(os.path.join(ROOT, "build", "flow", d, "stage_*.log"))]
        raise LogError("no stage %r for %s; have: %s" % (nm, d, ", ".join(sorted(have)) or "none"))
    if wl.startswith("step:"):
        if not run:
            raise LogError("%s has no run directory yet" % d)
        key = w[5:].strip().lower()
        sds = step_dirs(run)
        hit = [s for s in sds if os.path.basename(s).split("-")[0].lstrip("0") == key.lstrip("0") and key.isdigit()] if key.isdigit() else []
        if not hit:
            hit = [s for s in sds if key and key in os.path.basename(s).lower()]
        if not hit:
            raise LogError("no step matching %r in %s; e.g. step:06 or step:synthesis" % (key, os.path.basename(run)))
        sd = hit[0]
        logs = step_logs(sd)
        if not logs:
            raise LogError("step %s has no .log file" % os.path.basename(sd))
        pref = [lg for lg in logs if os.path.basename(lg)[:-4] == "-".join(os.path.basename(sd).split("-")[1:2]) + "-" + "-".join(os.path.basename(sd).split("-")[2:])]
        return (pref or logs)[0], "step " + os.path.basename(sd)
    if wl == "sim":
        ap = os.path.join(ROOT, "build", "sim", d, "sim.log")
        if os.path.isfile(ap):
            return ap, "simulation log"
        raise LogError("no build/sim/%s/sim.log (run make simulate DESIGN=%s)" % (d, d))
    if wl in ("gl", "gl-final", "gl_final"):
        c = [p for p in sorted(glob.glob(os.path.join(ROOT, "build", "gl", d, "sim", "gl.log"))) + [os.path.join(ROOT, "build", "gl", d, "result.txt")] if os.path.isfile(p)]
        if c:
            return c[0], "gate-level log"
        raise LogError("no gate-level log under build/gl/%s/" % d)
    raise LogError("unknown which=%r; use flow, error, warning, make, stages, stage:<name>, step:<NN or name>, sim, gl, job:<id>, precheck or a repo path" % w)


# ---------------------------------------------------------------- reading
def read_lines(ap: str, tail: Optional[int] = None, grep: Optional[str] = None, around: Optional[int] = None,
               context: int = 10, limit: int = MAX_LINES) -> Dict[str, Any]:
    """Select lines of a text file in one pass. Returns {total, selected: [(lineno, text)], mode, truncated}."""
    size = os.path.getsize(ap)
    big = size > MAX_READ_BYTES
    rx = None
    if grep:
        if len(grep) > 200:
            raise LogError("grep pattern too long (max 200)")
        try:
            rx = re.compile(grep, re.I)
        except re.error as e:
            raise LogError("bad grep regex: %s" % e)
    total = 0
    sel: List[Tuple[int, str]] = []
    ring = collections.deque(maxlen=max(1, min(int(tail or DEFAULT_TAIL), limit)))
    around = int(around) if around is not None else None
    context = max(0, min(int(context), 100))
    matches = 0
    with open(ap, "r", errors="replace") as f:
        if big:
            f.seek(max(0, size - MAX_READ_BYTES // 20))
            f.readline()
        for i, line in enumerate(f, 1):
            total = i
            t = line.rstrip("\n")
            if len(t) > MAX_LINE:
                t = t[:MAX_LINE] + " ..."
            if rx is not None:
                if rx.search(t):
                    matches += 1
                    if len(sel) < limit:
                        sel.append((i, t))
            elif around is not None:
                if around - context <= i <= around + context:
                    sel.append((i, t))
            else:
                ring.append((i, t))
    if rx is None and around is None:
        sel = list(ring)
        mode = "tail %d" % len(sel)
    elif rx is not None:
        mode = "grep %r: %d matching lines%s" % (grep, matches, " (first %d shown)" % limit if matches > limit else "")
    else:
        mode = "around line %d (+-%d)" % (around, context)
    return {"total": total, "selected": sel, "mode": mode, "truncated": big, "matches": matches}


def read_log_for(design: Optional[str], which: str, tail: Optional[int] = None, grep: Optional[str] = None,
                 around: Optional[int] = None, context: int = 10) -> Dict[str, Any]:
    ap, label = resolve_which(design, which)
    if not os.path.isfile(ap):
        raise LogError("%s does not exist (%s)" % (label, rel(ap)))
    r = read_lines(safe_path(rel(ap)), tail, grep, around, context)
    lines = ["%5d  %s" % (n, t) for n, t in r["selected"]]
    body = "\n".join(lines)
    if len(body) > MAX_CHARS:
        body = body[-MAX_CHARS:]
    head = "%s  (%s, %d lines in file; %s)" % (rel(ap), label, r["total"], r["mode"])
    out = {"path": rel(ap), "label": label, "total_lines": r["total"], "mode": r["mode"], "shown": len(lines),
           "lines": [t for _, t in r["selected"]], "line_numbers": [n for n, _ in r["selected"]],
           "markdown": _fence(head + "\n" + (body or "(no lines)"))}
    if which.lower().startswith("step:"):
        sd = os.path.dirname(ap)
        out["other_logs_in_step"] = [os.path.basename(p) for p in step_logs(sd) if p != ap]
        rt = os.path.join(sd, "runtime.txt")
        if os.path.isfile(rt):
            out["step_runtime"] = open(rt).read().strip()
    return out


# ---------------------------------------------------------------- digest
def _hms(s: str) -> float:
    m = re.match(r"^(\d+):(\d\d):(\d\d(?:\.\d+)?)$", s.strip())
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def _scan(ap: str, cap: int = 4_000_000) -> Tuple[collections.Counter, Dict[str, str], collections.Counter, Dict[str, str]]:
    """(error count by code, first error line by code, warning count by code, first warning line by code) of one file."""
    ec, ew = collections.Counter(), collections.Counter()
    fe, fw = {}, {}
    n = 0
    with open(ap, "r", errors="replace") as f:
        for line in f:
            n += len(line)
            if n > cap:
                break
            t = line.strip()
            if not t:
                continue
            if WARN_RE.search(t) and not ERR_RE.search(t.replace("WARNING", "")):
                m = CODE_RE.search(t)
                k = m.group(1) if m else re.sub(r"[0-9]+", "N", t)[:50]
                ew[k] += 1
                fw.setdefault(k, t[:200])
            elif ERR_RE.search(t) and "ERROR_ON_" not in t and "error.log" not in t and not re.search(r"\b0 errors?\b|Errors?: 0\b|errors? *= *0\b", t, re.I):
                m = CODE_RE.search(t)
                k = m.group(1) if m else re.sub(r"[0-9]+", "N", t)[:50]
                ec[k] += 1
                fe.setdefault(k, t[:200])
    return ec, fe, ew, fw


def digest_design(d: str) -> Dict[str, Any]:
    check_design(d)
    run = latest_run(d)
    res: Dict[str, Any] = {"design": d, "run": os.path.basename(run) if run else None, "sources": []}
    steps: List[Dict[str, Any]] = []
    if run:
        for sd in step_dirs(run):
            name = os.path.basename(sd)
            m = STEP_RE.match(name)
            tool, _, step = m.group(2).partition("-")
            e = {"step": name, "tool": tool, "errors": 0, "warnings": 0, "first_error": None, "first_warning": None, "seconds": None}
            rt = os.path.join(sd, "runtime.txt")
            if os.path.isfile(rt):
                e["seconds"] = round(_hms(open(rt).read()), 2)
                res["sources"].append(rel(rt))
            for lg in step_logs(sd):
                ec, fe, ew, fw = _scan(lg)
                res["sources"].append(rel(lg))
                e["errors"] += sum(ec.values())
                e["warnings"] += sum(ew.values())
                if ec and not e["first_error"]:
                    k = ec.most_common(1)[0][0]
                    e["first_error"] = fe[k]
                if ew and not e["first_warning"]:
                    k = ew.most_common(1)[0][0]
                    e["first_warning"] = fw[k]
            steps.append(e)
        # flow-level error.log / warning.log (what LibreLane summarised)
        for n in ("error.log", "warning.log"):
            ap = os.path.join(run, n)
            if os.path.isfile(ap):
                res["sources"].append(rel(ap))
        ap = os.path.join(run, "error.log")
        res["error_log_lines"] = sum(1 for _ in open(ap, errors="replace")) if os.path.isfile(ap) else None
        ap = os.path.join(run, "warning.log")
        wc = collections.Counter()
        first = {}
        if os.path.isfile(ap):
            for line in open(ap, errors="replace"):
                t = line.strip()
                if not t:
                    continue
                m = CODE_RE.search(t)
                k = m.group(1) if m else re.sub(r"[0-9]+", "N", t)[:40]
                wc[k] += 1
                first.setdefault(k, t[:200])
        res["warning_log"] = [{"code": k, "count": c, "example": first[k]} for k, c in wc.most_common(8)]
    res["steps"] = steps
    res["steps_with_errors"] = [s for s in steps if s["errors"]]
    res["steps_with_warnings"] = [s for s in steps if s["warnings"]]
    res["slowest_steps"] = [{"step": s["step"], "seconds": s["seconds"]} for s in sorted(steps, key=lambda s: -(s["seconds"] or 0))[:5]]
    res["total_step_seconds"] = round(sum(s["seconds"] or 0 for s in steps), 1)
    st = os.path.join(ROOT, "build", "flow", d, "stages.txt")
    stages = []
    if os.path.isfile(st):
        res["sources"].append(rel(st))
        for line in open(st, errors="replace"):
            p = line.split()
            if len(p) >= 3 and p[1] in ("PASS", "FAIL", "SKIP"):
                stages.append({"stage": p[0], "outcome": p[1], "seconds": p[2]})
    res["stages"] = stages
    return res


def digest_job(jid: str) -> Dict[str, Any]:
    if not JOB_RE.match(jid or ""):
        raise LogError("job_id looks like 20261006_193459-01")
    ap = os.path.join(ROOT, "build", "agent", "jobs", jid + ".log")
    if not os.path.isfile(ap):
        raise LogError("no log for job %s" % jid)
    ec, fe, ew, fw = _scan(ap)
    n = sum(1 for _ in open(ap, errors="replace"))
    cmd = open(ap, errors="replace").readline().strip()
    dm = re.search(r"DESIGN=([A-Za-z0-9_.-]+)", cmd)
    res = {"job_id": jid, "path": rel(ap), "lines": n, "command": cmd, "errors": [{"code": k, "count": c, "example": fe[k]} for k, c in ec.most_common(8)],
           "warnings": [{"code": k, "count": c, "example": fw[k]} for k, c in ew.most_common(8)], "sources": [rel(ap)]}
    if dm:
        try:
            d = digest_design(dm.group(1))
            res["design"] = d["design"]
            res["stages"] = d["stages"]
            res["slowest_steps"] = d["slowest_steps"]
        except LogError:
            pass
    return res


def digest_markdown(r: Dict[str, Any]) -> str:
    L: List[str] = []
    if "job_id" in r:
        L.append("job %s: %s (%d log lines)" % (r["job_id"], r["command"], r["lines"]))
        L.append("errors: %s" % ("none" if not r["errors"] else ""))
        L += ["  %3d x %s  %s" % (e["count"], e["code"], e["example"][:110]) for e in r["errors"]]
        L.append("warnings: %s" % ("none" if not r["warnings"] else ""))
        L += ["  %3d x %s  %s" % (e["count"], e["code"], e["example"][:110]) for e in r["warnings"][:5]]
    else:
        L.append("log digest of %s, run %s" % (r["design"], r["run"]))
        if r.get("stages"):
            L.append("stages: " + ", ".join("%s %s %ss" % (s["stage"], s["outcome"], s["seconds"]) for s in r["stages"]))
    if r.get("slowest_steps"):
        L.append("slowest steps (runtime.txt): " + ", ".join("%s %ss" % (s["step"], s["seconds"]) for s in r["slowest_steps"]))
    if "steps" in r:
        L.append("error.log lines: %s; steps with errors: %d; steps with warnings: %d (of %d)" % (
            r.get("error_log_lines"), len(r["steps_with_errors"]), len(r["steps_with_warnings"]), len(r["steps"])))
        if r.get("error_log_lines") == 0 and r["steps_with_errors"]:
            L.append("note: error.log is empty (the flow reported no failure); the ERR lines are tool messages that contain the word Error")
        for s in r["steps_with_errors"][:8]:
            L.append("  ERR  %-34s %3d  %s" % (s["step"], s["errors"], (s["first_error"] or "")[:90]))
        for s in r["steps_with_warnings"][:8]:
            L.append("  WARN %-34s %3d  %s" % (s["step"], s["warnings"], (s["first_warning"] or "")[:90]))
        for w in r.get("warning_log", [])[:4]:
            L.append("  warning.log %3d x %s  %s" % (w["count"], w["code"], w["example"][:90]))
    return _fence("\n".join(L))


# ---------------------------------------------------------------- open file / open gds
def _log_open(kind: str, target: str, how: str, ok: bool, extra: str = "") -> None:
    try:
        d = os.path.join(ROOT, "build", "agent", "proof")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "opens.jsonl"), "a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "target": target, "how": how, "ok": ok, "note": extra}) + "\n")
    except OSError:
        pass


def _popen_detached(cmd: List[str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _http(name: str, body: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    req = urllib.request.Request("http://127.0.0.1:%d/%s" % (PORT, name), data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


KLAYOUT_APP_MAC = os.environ.get("KLAYOUT_APP", "/Applications/KLayout/klayout.app")


def _find_gds(design: str) -> str:
    import view_api  # examples/hermes_klayout_gui
    return view_api.find_gds(design)


def _find_lyp() -> Optional[str]:
    import eda_tools
    for c in (eda_tools.LYP, os.path.expanduser("~/.volare/sky130A/libs.tech/klayout/tech/sky130A.lyp")):
        if c and os.path.isfile(c):
            return c
    c = sorted(glob.glob(os.path.join(ROOT, "build", "**", "sky130A", "libs.tech", "klayout", "tech", "sky130A.lyp"), recursive=True))
    return c[0] if c else None


def open_gds_for(design: str, viewer: str = "klayout-app") -> Dict[str, Any]:
    check_design(design)
    v = (viewer or "klayout-app").strip().lower().replace("_", "-")
    if v in ("klayout-app", "app", "klayout-desktop"):
        try:
            gds = _find_gds(design)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "no GDS for %s: %s" % (design, e)}
        lyp = _find_lyp()
        if sys.platform == "darwin":
            app = KLAYOUT_APP_MAC
            if not os.path.exists(app):
                return {"ok": False, "error": "KLayout application not found at %s (set KLAYOUT_APP)" % app}
            cmd = ["open", "-n", "-a", app, "--args", gds] + (["-l", lyp] if lyp else [])
        else:
            exe = shutil.which("klayout")
            if not exe:
                return {"ok": False, "error": "klayout is not installed (apt/brew install klayout)"}
            cmd = [exe, gds] + (["-l", lyp] if lyp else [])
        try:
            p = _popen_detached(cmd)
        except OSError as e:
            _log_open("gds", design, v, False, str(e))
            return {"ok": False, "error": "could not start KLayout: %s" % e}
        _log_open("gds", design, v, True, "pid %s" % p.pid)
        g = os.path.relpath(gds, os.path.realpath(ROOT)) if gds.startswith(os.path.realpath(ROOT)) else os.path.basename(gds)
        return {"ok": True, "viewer": "klayout-app", "design": design, "gds": g, "layer_properties": os.path.basename(lyp) if lyp else None,
                "command": " ".join(os.path.basename(c) if c.startswith("/") else c for c in cmd), "pid": p.pid,
                "markdown": "Opened the KLayout application with `%s` and the sky130 layer file. It is a normal desktop window; nothing is written." % g}
    if v in ("klayout", "magic"):
        try:
            r = _http("gui_start", {"tool": v, "design": design})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "gui_start failed: %s" % e}
        _log_open("gds", design, v, bool(r.get("ok", True)), str(r.get("error", ""))[:100])
        r.setdefault("viewer", v)
        r.setdefault("markdown", "Opened the controllable %s window for %s. Drive it with gui_command or klayout_live/magic_live." % (v, design))
        return r
    if v in ("png", "image", "render"):
        try:
            r = _http("klayout_view", {"design": design}, timeout=120)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "klayout_view failed: %s" % e}
        _log_open("gds", design, "png", "png_url" in r)
        r.setdefault("viewer", "png")
        return r
    return {"ok": False, "error": "viewer must be klayout-app, klayout, magic or png"}


def open_file_for(path: str, start: Optional[int] = None, end: Optional[int] = None, viewer: str = "text") -> Dict[str, Any]:
    ap = safe_path(path)
    v = (viewer or "text").lower()
    if v in ("os", "app", "finder", "default"):
        exe = "open" if sys.platform == "darwin" else shutil.which("xdg-open")
        if not exe:
            return {"ok": False, "error": "no OS opener (open / xdg-open) found"}
        try:
            _popen_detached([exe, ap])
        except OSError as e:
            _log_open("file", rel(ap), "os", False, str(e))
            return {"ok": False, "error": str(e)}
        _log_open("file", rel(ap), "os", True)
        return {"ok": True, "path": rel(ap), "viewer": "os", "markdown": "Opened %s in the system viewer." % rel(ap)}
    s = max(1, int(start or 1))
    e = int(end) if end else s + 79
    e = min(e, s + MAX_LINES - 1)
    out, total = [], 0
    with open(ap, "r", errors="replace") as f:
        for i, line in enumerate(f, 1):
            total = i
            if s <= i <= e:
                t = line.rstrip("\n")
                out.append("%5d  %s" % (i, t[:MAX_LINE] + (" ..." if len(t) > MAX_LINE else "")))
    body = "\n".join(out)[:MAX_CHARS]
    return {"ok": True, "path": rel(ap), "total_lines": total, "start": s, "end": min(e, total), "lines": out,
            "markdown": _fence("%s  (lines %d-%d of %d)\n%s" % (rel(ap), s, min(e, total), total, body))}


# ---------------------------------------------------------------- endpoints
class ListLogsReq(BaseModel):
    design: Optional[str] = Field(None, description="design name, e.g. kv_attn_n8 (omit for the newest jobs and make logs)")
    job_id: Optional[str] = Field(None, description="a run_make job id like 20261006_193459-01")


class ReadLogReq(BaseModel):
    design: Optional[str] = Field(None, description="design name (not needed for job:<id>, precheck or a path)")
    which: str = Field(..., description='"flow" | "error" | "warning" | "make" | "stages" | "stage:<name>" | "step:<NN or name>" | "sim" | "gl" | "job:<id>" | "precheck" | a repo-relative path')
    tail: Optional[int] = Field(None, description="last N lines (default 60, max 300)")
    grep: Optional[str] = Field(None, description="case-insensitive regex: only the matching lines")
    around: Optional[int] = Field(None, description="line number: show the lines around it")
    context: Optional[int] = Field(10, description="lines on each side for around")


class DigestReq(BaseModel):
    design: Optional[str] = Field(None, description="design name")
    job_id: Optional[str] = Field(None, description="a run_make job id")


class OpenGdsReq(BaseModel):
    design: str = Field(..., description="design name, e.g. kv_attn_n8")
    viewer: str = Field("klayout-app", description='"klayout-app" (full KLayout application) | "klayout" or "magic" (controllable window) | "png" (render)')


class OpenFileReq(BaseModel):
    path: str = Field(..., description="repo-relative path, e.g. designs/kv_attn_n8/NOTES.md or docs/LESSONS.md")
    start: Optional[int] = Field(None, description="first line (default 1)")
    end: Optional[int] = Field(None, description="last line (default start+79, max 300 lines)")
    viewer: Optional[str] = Field("text", description='"text" returns the lines; "os" opens the file in the system viewer')


def post(name: str, summary: str):
    return router.post("/" + name, operation_id=name, summary=summary, response_model=None)


def _err(e: Exception) -> dict:
    return {"ok": False, "error": str(e), "markdown": "Error: %s" % e}


@post("list_logs", "List the logs of a design or job with sizes and times")
def list_logs(req: ListLogsReq = ListLogsReq()) -> dict:
    """List every log of one design (the newest run's flow/error/warning and per-step logs, the stage logs, the make log, the
    simulation and gate-level logs, precheck logs) or of one job, with size and modified time. Use when the user asks which logs
    exist. Show the `markdown` field verbatim."""
    try:
        return list_logs_for(req.design, req.job_id)
    except LogError as e:
        return _err(e)


@post("read_log", "Read a log: the tail, matching lines, or lines around a line")
def read_log(req: ReadLogReq) -> dict:
    """Read one log of a design: which = flow, error, warning, make, stages, stage:<name> (e.g. stage:gds), step:<NN or name>
    (e.g. step:06 or step:synthesis), sim, gl, job:<id>, precheck, or a repo-relative path. tail = last N lines, grep = regex,
    around = a line number. Show the `markdown` field verbatim; it is real file content with line numbers."""
    try:
        return read_log_for(req.design, req.which, req.tail, req.grep, req.around, req.context or 10)
    except LogError as e:
        return _err(e)
    except OSError as e:
        return _err(e)


@post("log_digest", "Digest of a run's logs: errors, warnings, slowest steps, stage outcomes")
def log_digest(req: DigestReq) -> dict:
    """Code-built digest of a design's newest run or of a job log: errors and warnings grouped by tool/step with counts and the
    first example line, the slowest steps (runtime.txt), the stage outcomes (simulate, gds, check, gl, collect). Use for 'what
    went wrong', 'any errors', 'slowest steps'. Show the `markdown` field verbatim."""
    try:
        r = digest_job(req.job_id) if req.job_id else digest_design(req.design or "")
        r["markdown"] = digest_markdown(r)
        return r
    except LogError as e:
        return _err(e)


@post("open_gds", "Open a design's GDS in the KLayout application, a live window, Magic or as a PNG")
def open_gds(req: OpenGdsReq) -> dict:
    """Open the layout of a design on the user's screen. viewer klayout-app = the full KLayout desktop application with the sky130
    layer file; klayout / magic = the controllable windows (then use gui_command); png = an image in the chat. Read-only, no
    confirmation needed; the launch is logged in build/agent/proof/opens.jsonl."""
    try:
        return open_gds_for(req.design, req.viewer or "klayout-app")
    except LogError as e:
        return _err(e)


@post("open_file", "Show a repo text file (lines) or open it in the system viewer")
def open_file(req: OpenFileReq) -> dict:
    """Return the text of a repo file (NOTES.md, a report .rpt, metrics.json, a doc) with a line range, or open it in the system
    viewer with viewer=os. Repo-relative paths only. Show the `markdown` field verbatim."""
    try:
        return open_file_for(req.path, req.start, req.end, req.viewer or "text")
    except LogError as e:
        return _err(e)
    except OSError as e:
        return _err(e)
