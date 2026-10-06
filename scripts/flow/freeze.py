#!/usr/bin/env python3
# Docs: designs/FROZEN.md, scripts/flow/frozen.py, docs/VALIDATION.md, CLAUDE.md
"""freeze.py -- freeze the validated example designs, and verify that they are still frozen.

    freeze.py write     (make freeze)         write designs/FROZEN.json and designs/FROZEN.md; refuses (exit 1, nothing written)
                                              unless EVERY design of Makefile ALL_DESIGNS has a current, complete run whose
                                              final/metrics.json equals the committed output/metrics.json, an empty error.log
                                              and zero DRC / LVS / antenna errors in those metrics
    freeze.py check     (make check-frozen)   recompute every hash and compare with the manifest; exit 1 listing every changed,
                                              missing or added file and the design it belongs to; no Docker, no flow, fast

Per design the manifest records: the run directory name, the sha256 of every run input, the sha256 of output/metrics.json and
output/layout.png, key numbers from metrics.json, the freeze date and the git commit.
Run inputs = exactly what scripts/flow/find_reusable_run.py treats as inputs (inputs_of(): every repository file under a path in the
run's resolved.json, the design directory without runs/gds/output/model/tb and without *.md, and the dir:: targets of config.json,
which covers shared/rtl and the build/macros/<m>/ views of a wrapper), plus designs/<d>/tb/*, shared/tb/* and the model/<x>/
directories the design's files name ("model/<x>/", generated ROM/vectors headers).
build/ files (macro views) are not committed: when absent (a fresh checkout) they are reported as "not present" and skipped, when
present they must match. Everything else must exist and match; a file added to a watched directory counts as changed.
"""
import glob, hashlib, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)
import repo, find_reusable_run as fr   # noqa: E402

REPO = repo.REPO
MJ = os.path.join(REPO, "designs", "FROZEN.json")
MD = os.path.join(REPO, "designs", "FROZEN.md")
MODEL_DIRS = ("tiny_ai", "audio_pitch", "audio_onset", "image_text_match", "precision_hw", "kv_attention")


def rel(p):
    return os.path.relpath(os.path.realpath(p), os.path.realpath(REPO)).replace(os.sep, "/")


def sha(p):
    return fr.sha256(p)


def tree(path):
    out = []
    for dp, dn, fn in os.walk(path):
        dn[:] = [x for x in dn if x not in ("__pycache__", ".git")]
        out += [os.path.join(dp, f) for f in fn if not f.endswith(".pyc")]
    return out


def design_files(d, run):
    """Every file whose change invalidates design d (repo-relative, sorted). run None: config-derived set only (check mode)."""
    files = set(fr.inputs_of(run or "/nonexistent-run", d))
    files |= set(tree(os.path.join(REPO, "designs", d, "tb")))
    files |= {f for f in tree(os.path.join(REPO, "shared", "tb"))}
    # generated files name their model directory; hash it too
    models = set()
    for f in list(files):
        if f.endswith((".v", ".vh", ".json", ".hex", ".sdc", ".cfg")) and os.path.isfile(f) and os.path.getsize(f) < 4_000_000:
            try:
                txt = open(f, errors="replace").read()
            except OSError:
                continue
            models |= {m for m in re.findall(r"model/(\w+)/", txt) if m in MODEL_DIRS}
    for m in models:
        files |= set(tree(os.path.join(REPO, "model", m)))
    files = {f for f in files if os.path.isfile(f) and not f.endswith(".md")}
    return sorted({rel(f) for f in files})


def key_numbers(m):
    g = lambda k: m.get(k)
    ws = lambda pre: min((v for k, v in m.items() if k.startswith(pre) and isinstance(v, (int, float))), default=None)
    return {"cells": g("design__instance__count__stdcell"), "ff": g("design__instance__count__class:sequential_cell"),
            "setup_ws_ns": ws("timing__setup__ws"), "hold_ws_ns": ws("timing__hold__ws"),
            "drc_magic": g("magic__drc_error__count"), "drc_klayout": g("klayout__drc_error__count"),
            "drc_route": g("route__drc_errors"), "lvs_error": g("lvs__error__count") if "lvs__error__count" in m else g("design__lvs_error__count"),
            "antenna": g("route__antenna_violation__count"), "die_area_um2": g("design__die__area")}


def git(*a):
    try:
        return subprocess.run(["git", "-C", REPO] + list(a), capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def build_manifest():
    problems, designs = [], {}
    for d in repo.all_designs():
        run, why = fr.find_run(d)
        if not run:
            problems.append("%s: not current/complete: %s" % (d, why)); continue
        cm, rm = os.path.join(REPO, "designs", d, "output", "metrics.json"), os.path.join(run, "final", "metrics.json")
        try:
            a, b = json.load(open(cm)), json.load(open(rm))
        except (OSError, ValueError) as e:
            problems.append("%s: cannot read metrics (%s)" % (d, e)); continue
        if a != b:
            problems.append("%s: output/metrics.json differs from %s/final/metrics.json" % (d, os.path.basename(run))); continue
        el = os.path.join(run, "error.log")
        if os.path.exists(el) and os.path.getsize(el):
            problems.append("%s: error.log of %s is not empty" % (d, os.path.basename(run))); continue
        kn = key_numbers(a)
        nz = [k for k in ("drc_magic", "drc_klayout", "drc_route", "lvs_error", "antenna") if kn[k]]
        if nz:
            problems.append("%s: non-zero %s in metrics.json" % (d, ", ".join(nz))); continue
        png = os.path.join(REPO, "designs", d, "output", "layout.png")
        if not os.path.isfile(png):
            problems.append("%s: output/layout.png missing" % d); continue
        designs[d] = {"run": os.path.basename(run),
                      "inputs": {f: sha(os.path.join(REPO, f)) for f in design_files(d, run)},
                      "evidence": {rel(cm): sha(cm), rel(png): sha(png)}, "numbers": kn}
    return problems, designs


def write():
    problems, designs = build_manifest()
    if problems:
        print("freeze: REFUSED, %d design(s) not current/clean (nothing written):" % len(problems))
        for p in problems: print("  " + p)
        return 1
    model = {rel(f): sha(f) for m in MODEL_DIRS for f in tree(os.path.join(REPO, "model", m))}
    dirty = len([l for l in git("status", "--porcelain").splitlines() if l.strip()])
    man = {"format": 1, "frozen_date": time.strftime("%Y-%m-%d"), "git_commit": git("rev-parse", "HEAD"),
           "uncommitted_paths_at_freeze": dirty, "tool": "scripts/flow/freeze.py", "model": model, "designs": designs}
    json.dump(man, open(MJ, "w"), indent=1, sort_keys=True); open(MJ, "a").write("\n")
    with open(MD, "w") as f:
        f.write(markdown(man))
    print("freeze: wrote designs/FROZEN.json (%d designs, %d input hashes, %d model hashes) and designs/FROZEN.md"
          % (len(designs), sum(len(e["inputs"]) for e in designs.values()), len(model)))
    return 0


def markdown(man):
    L = ["# Frozen designs", "",
         "Docs: scripts/flow/freeze.py, scripts/flow/frozen.py, docs/VALIDATION.md, CLAUDE.md", "",
         "Generated by `make freeze` from `designs/FROZEN.json` (do not edit by hand). Freeze date %s, git commit `%s` "
         "(%d paths were uncommitted in the working tree at that moment; the hashes below, not the commit, are the record)."
         % (man["frozen_date"], man["git_commit"][:12], man["uncommitted_paths_at_freeze"]), "",
         "Frozen means: the committed run, `output/metrics.json`, `output/layout.png`, every run input and the model sources feeding the "
         "designs are pinned by sha256. `make check-frozen` (part of `make test`, no Docker) fails and lists every file or design that "
         "changed. Agents and hooks must not edit frozen paths (`scripts/flow/frozen.py`: `is_frozen(path)`).", "",
         "## Unfreeze procedure (owner only)", "",
         "1. Decide deliberately to change a design (RTL, config, SDC, pin order, testbench, model).",
         "2. Make the change, then re-harden it: `make flow-all DESIGN=<d>` (and the wrappers/macros that depend on it); `make test` and `make test-full`.",
         "3. Re-validate: `docs/VALIDATION.md` checklist; every design must be current with committed evidence equal to its run.",
         "4. `make freeze` (refuses unless all %d designs are current and clean) rewrites this file and `FROZEN.json`; `make check-frozen` must PASS." % len(man["designs"]),
         "5. Commit the design change, the new evidence and the new manifest together.", "",
         "`designs/FROZEN.json` itself is protected like the designs: nobody but the owner regenerates it.", "",
         "## Designs", "",
         "| design | run | inputs | cells | ff | setup ws ns | hold ws ns | DRC (magic/klayout/route) | LVS err | metrics.json sha256 | layout.png sha256 |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for d, e in man["designs"].items():
        n = e["numbers"]; ev = list(e["evidence"].items())
        mh = [h for p, h in ev if p.endswith("metrics.json")][0]; ph = [h for p, h in ev if p.endswith("layout.png")][0]
        fmt = lambda x: "n/a" if x is None else ("%.3f" % x if isinstance(x, float) else str(x))
        L.append("| %s | %s | %d | %s | %s | %s | %s | %s/%s/%s | %s | %s | %s |" % (
            d, e["run"], len(e["inputs"]), fmt(n["cells"]), fmt(n["ff"]), fmt(n["setup_ws_ns"]), fmt(n["hold_ws_ns"]),
            fmt(n["drc_magic"]), fmt(n["drc_klayout"]), fmt(n["drc_route"]), fmt(n["lvs_error"]), mh[:16], ph[:16]))
    L += ["", "Hashes are truncated to 16 hex digits here; `FROZEN.json` has the full sha256 of every input file.", "",
          "Model sources hashed: %d files under model/{%s}." % (len(man["model"]), ",".join(MODEL_DIRS)), ""]
    return "\n".join(L)


def check():
    try:
        man = json.load(open(MJ))
    except (OSError, ValueError) as e:
        print("check-frozen: FAIL (no readable designs/FROZEN.json: %s; run make freeze)" % e); return 1
    changed, notes = [], []
    def cmp(owner, path, want):
        p = os.path.join(REPO, path)
        if not os.path.isfile(p):
            if path.startswith("build/"):
                notes.append("%s: %s not present (build/ is not committed), skipped" % (owner, path)); return
            changed.append("%s: MISSING %s" % (owner, path)); return
        if sha(p) != want:
            changed.append("%s: CHANGED %s" % (owner, path))
    for path, h in man.get("model", {}).items():
        cmp("model", path, h)
    for d, e in man["designs"].items():
        for path, h in {**e["inputs"], **e["evidence"]}.items():
            cmp(d, path, h)
        now = set(design_files(d, None))
        for path in sorted(now - set(e["inputs"])):
            if not path.startswith("build/"):
                changed.append("%s: ADDED %s (not in the manifest)" % (d, path))
    for m in MODEL_DIRS:
        for f in tree(os.path.join(REPO, "model", m)):
            if rel(f) not in man.get("model", {}):
                changed.append("model: ADDED %s (not in the manifest)" % rel(f))
    for d in repo.all_designs():
        if d not in man["designs"]:
            changed.append("%s: design is not in the manifest" % d)
    n = sum(len(e["inputs"]) + len(e["evidence"]) for e in man["designs"].values()) + len(man.get("model", {}))
    if changed:
        bad = sorted({c.split(":")[0] for c in changed})
        print("check-frozen: FAIL, %d change(s) in: %s" % (len(changed), ", ".join(bad)))
        for c in changed: print("  " + c)
        print("  (owner only: see designs/FROZEN.md, Unfreeze procedure)")
        return 1
    print("check-frozen: PASS (%d designs, %d hashes verified%s; frozen %s)"
          % (len(man["designs"]), n, ", %d build/ file(s) not present" % len(notes) if notes else "", man["frozen_date"]))
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["write"]: sys.exit(write())
    if a[:1] == ["check"]:
        if "--manifest" in a:                      # test hook: verify against another manifest copy
            MJ = a[a.index("--manifest") + 1]
        sys.exit(check())
    sys.exit(__doc__)
