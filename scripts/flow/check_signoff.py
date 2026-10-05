#!/usr/bin/env python3
"""check_signoff.py -- "no logic lost" sign-off check for one design or all of them.

    scripts/flow/check_signoff.py <design>            design directory name, or upe for user_proj_example
    scripts/flow/check_signoff.py --all               every design, then a table; exit 1 if any fails
    options: --metrics FILE   use this metrics.json instead of the newest run / designs/<design>/output/

Reads the design's final metrics.json (newest complete designs/<d>/runs/RUN_*/final/metrics.json, or
designs/<d>/output/metrics.json, whichever is newer) and fails, with one line per reason, if
  * magic / KLayout DRC, route DRC, LVS, XOR or antenna counts are non-zero,
  * any corner's setup or hold worst slack is negative, or any timing violation count is non-zero,
  * synthesis__check_error__count, unmapped cells or inferred latches are non-zero,
  * the sequential cells that survive are fewer than the flip-flops the RTL elaborates to
    (minus the commented per-design allowance in scripts/flow/signoff_allowances.json),
  * Yosys reports "multiple conflicting drivers" or "is used but has no driver" while elaborating the RTL (`proc; check`).
What each sub-check catches and does not:
  * The register count (after `synth -flatten -noabc`) catches logic that synthesis removes for being unused or constant.
    It does NOT catch a multiply-driven register: Yosys has already resolved and dropped it before the count (a register
    driven from two places counts 1 here, however many bits it had). That case is caught by the driver-warning check above, by
    synthesis__check_error__count (needs ERROR_ON_SYNTH_CHECKS to see it) and by the gate-level simulation.
  * Neither catches logic that is kept but functionally wrong, nor a design whose RTL registers were already removed
    by hand; only simulation (RTL and gate level) covers function.
The RTL register count is not hard-coded: the design's own file list, include dirs and defines are read from
designs/<d>/config.json and elaborated with Yosys (`synth -flatten`, no ABC, then count the distinct flip-flop and
latch output bits). That is the same register count LibreLane's synthesis starts from, so the check is exact.
Also printed, never failing: max-slew / max-cap counts, peak memory and wall time (resources.json), and the
resolved MAX_TRANSITION_CONSTRAINT when it is not the PDK default.
RTL count: computed afresh on every run (no cache). Cells of the sky130_fd_sc_hd library that the RTL instantiates directly
are given to Yosys as black-box stubs generated from the RTL itself, so no PDK is needed (and the PDK is not read);
instantiated sequential library cells (df*/sdf*/edf*/dl*) are added to the count one each.
Macros: a design with MACROS in its config.json (an elaborate-only wrapper) has each macro that is a design of this repository read
with `read_verilog -lib`, i.e. as a black box with the ports of its RTL module header; the macro's own files are dropped from the
design's file list if the config lists them. The macro's registers are not counted (they are checked in the macro's own run), so an
elaborate-only wrapper has 0 RTL registers. Only when the design has SYNTH_ELABORATE_ONLY true AND the metric is absent are these
skipped (noted, never silently): synthesis check / unmapped / inferred-latch counts, and the sequential-cell count (taken as 0).
DRC, LVS, XOR, antenna and setup/hold timing are never skipped.
Yosys: `yosys` on PATH when USE_DOCKER=0, otherwise the LibreLane image (honours DOCKER_HOST, CPUSET).
"""
import argparse, glob, json, os, re, subprocess, sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
IMAGE = os.environ.get("DOCKER_IMAGE", "ghcr.io/librelane/librelane:3.0.2")
PDK_MAX_TRANSITION = 0.75

DESIGNS = sorted(os.path.basename(os.path.dirname(c)) for c in glob.glob(os.path.join(REPO, "designs", "*", "config.json")))
ALIAS = {"upe": "user_proj_example"}


def rel(p):
    return os.path.relpath(p, REPO)


def macro_designs(cfg):
    """MACROS keys that are designs of this repository (their RTL is black-boxed in the wrapper)."""
    return [k for k in (cfg.get("MACROS") or {}) if os.path.isfile(os.path.join(REPO, "designs", k, "config.json"))]


def elaborate_only(cfg):
    return cfg.get("SYNTH_ELABORATE_ONLY") is True


# ---------------------------------------------------------------- RTL register count
def read_config(design):
    path = os.path.join(REPO, "designs", design, "config.json")
    cfg = json.load(open(path))
    base = os.path.dirname(path)

    def fix(v):
        if isinstance(v, str) and v.startswith("dir::"):
            return os.path.normpath(os.path.join(base, v[5:]))
        return v
    files = [fix(f) for f in cfg["VERILOG_FILES"]]
    incs = [fix(f) for f in cfg.get("VERILOG_INCLUDE_DIRS", [])]
    defs = cfg.get("VERILOG_DEFINES", [])
    return cfg, cfg["DESIGN_NAME"], files, incs, defs


def run_yosys(script_path):
    sock = os.path.expanduser("~/.colima/osl/docker.sock")      # same default as the Makefile
    if "DOCKER_HOST" not in os.environ and os.path.exists(sock):
        os.environ["DOCKER_HOST"] = "unix://" + sock
    if os.environ.get("USE_DOCKER", "1") == "0":
        cmd = ["yosys", "-q", script_path]
    else:
        cmd = ["docker", "run", "--rm", "-i"]
        if os.environ.get("CPUSET"):
            cmd += ["--cpuset-cpus=" + os.environ["CPUSET"]]
        home = os.path.expanduser("~")
        cmd += ["-v", f"{home}:{home}", "-v", f"{REPO}:{REPO}", "-w", REPO, IMAGE, "yosys", "-q", script_path]
    return subprocess.run(cmd, capture_output=True, text=True)


LIB_PREFIX = "sky130_fd_sc_hd__"
# sequential library cells (flip-flops, latches, clock-gate latches): families df*, sdf*, edf*, sedf*, dl*, sdl*
LIB_SEQ = re.compile(r"^" + LIB_PREFIX + r"(s?e?df|s?dl)\w*$")
# output pin names of sky130_fd_sc_hd cells; every other pin of a stub is declared an input
LIB_OUT = {"X", "Y", "Q", "Q_N", "COUT", "SUM", "GCLK", "HI", "LO", "CON", "SUM_N", "COUT_N"}


def lib_stubs(files):
    """Black-box stubs for every sky130_fd_sc_hd cell the RTL instantiates directly (RTL that builds structures from
    library cells). The ports are taken from the named connections of the instances, so no PDK is needed:
    the register count is the same with and without $PDK_ROOT. Returns Verilog text."""
    ports = {}
    for f in files:
        t = re.sub(r"//[^\n]*|/\*.*?\*/", "", open(f, errors="replace").read(), flags=re.S)
        for m in re.finditer(r"\b(" + LIB_PREFIX + r"\w+)\s*(?:#\s*\([^)]*\)\s*)?(?:\\?\S+\s*)\(", t):
            i, depth = m.end(), 1
            while i < len(t) and depth:
                depth += {"(": 1, ")": -1}.get(t[i], 0); i += 1
            ports.setdefault(m.group(1), set()).update(re.findall(r"\.(\w+)\s*\(", t[m.end():i]))
    out = []
    for cell, ps in sorted(ports.items()):
        decl = ", ".join(("output " if p in LIB_OUT else "input ") + p for p in sorted(ps))
        out.append(f"(* blackbox *) module {cell}({decl}); endmodule")
    return "\n".join(out) + ("\n" if out else "")


def rtl_registers(design):
    """(register_bits, driver_warnings, breakdown): the flip-flop / latch bits in the elaborated, flattened RTL, plus one
    per instantiated sequential library cell. Computed on every call (no cache)."""
    cfg, top, files, incs, defs = read_config(design)
    macro_files = []                                   # RTL of the macros: black boxes (header only), never counted
    for m in macro_designs(cfg):
        _, _, mf, mi, md = read_config(m)
        macro_files.append((mf, mi, md))
    mset = {os.path.realpath(f) for mf, _, _ in macro_files for f in mf}
    files = [f for f in files if os.path.realpath(f) not in mset]
    work = os.path.join(REPO, "build", "check", "rtl_ff", design)
    os.makedirs(work, exist_ok=True)
    ys = os.path.join(work, "ff.ys")
    out = os.path.join(work, "ff.json")
    if os.path.exists(out):
        os.remove(out)
    opts = " ".join(["-sv"] + [f"-I{i}" for i in incs] + [f"-D{d}" for d in defs])
    stubs = lib_stubs(files)
    lib = ""
    if stubs:
        open(os.path.join(work, "lib_stubs.v"), "w").write(stubs)
        lib = f"read_verilog -lib {os.path.join(work, 'lib_stubs.v')}\n"
    for n, (mf, mi, md) in enumerate(macro_files):
        mopts = " ".join(["-sv"] + [f"-I{i}" for i in mi] + [f"-D{d}" for d in md])
        lib += f"read_verilog -lib {mopts} {' '.join(mf)}\n"
    open(ys, "w").write(
        lib + f"read_verilog {opts} {' '.join(files)}\n"
        f"hierarchy -check -top {top}\n"
        f"proc\ncheck\n"
        f"synth -flatten -noabc\n"
        f"write_json {out}\n")
    r = run_yosys(ys)
    if (r.returncode != 0 and "Can't open output file" in (r.stdout + r.stderr)):
        # the Colima VM's view of a directory the host just wrote can lag; one retry (seen once in 15 runs)
        r = run_yosys(ys)
    if r.returncode != 0 or not os.path.exists(out):
        msg = (r.stderr or r.stdout).strip().splitlines()
        raise RuntimeError("Yosys elaboration failed: " + (msg[-1][:200] if msg else "no output"))
    mod = json.load(open(out))["modules"]
    mod = mod.get(top) or next(iter(mod.values()))
    bits = set()
    libseq = 0
    for cell in mod["cells"].values():
        t = cell["type"]
        if t.startswith("$_") and ("DFF" in t or "DLATCH" in t or "SR_" in t) and "Q" in cell["connections"]:
            bits.update(b for b in cell["connections"]["Q"] if isinstance(b, int))
        elif LIB_SEQ.match(t):
            libseq += 1
    names = {}
    for nn, n in mod["netnames"].items():
        if nn.startswith("$"):
            continue
        for b in n["bits"]:
            if b in bits and b not in names:
                names[b] = re.sub(r"\[\d+\]$", "", nn)
    breakdown = {}
    for b in bits:
        k = names.get(b, "(unnamed)"); breakdown[k] = breakdown.get(k, 0) + 1
    if libseq:
        breakdown["(instantiated library sequential cells)"] = libseq
    warn = sorted({ln.strip()[:120] for ln in (r.stdout + r.stderr).splitlines()
                   if "multiple conflicting drivers" in ln or "is used but has no driver" in ln})
    return len(bits) + libseq, warn, breakdown


# ---------------------------------------------------------------- metrics
def find_metrics(design, explicit=None):
    if explicit:
        return explicit, None
    cands = []
    for d in glob.glob(os.path.join(REPO, "designs", design, "runs", "RUN_*")):
        m = os.path.join(d, "final", "metrics.json")
        if os.path.exists(m):
            try:
                if "magic__drc_error__count" in json.load(open(m)):   # a complete flow, not a synthesis-only run
                    cands.append((os.path.getmtime(m), m, d))
            except ValueError:
                pass
    o = os.path.join(REPO, "designs", design, "output", "metrics.json")
    if os.path.exists(o):
        cands.append((os.path.getmtime(o), o, None))
    if not cands:
        return None, None
    cands.sort()
    return cands[-1][1], cands[-1][2]


def check(design, explicit=None, quiet=False):
    """Returns (failures, info) -- failures is a list of one-line reasons."""
    fails, notes = [], []
    info = {"design": design, "src": "-", "rtl": None, "allow": 0, "seq": None}
    mpath, rundir = find_metrics(design, explicit)
    if not mpath:
        return ["no metrics.json (no complete run under designs/%s/runs/ and no designs/%s/output/metrics.json)" % (design, design)], info
    info["src"] = rel(mpath)
    m = json.load(open(mpath))

    def val(k):
        return m.get(k)

    cfg0 = read_config(design)[0]
    elab = elaborate_only(cfg0)

    def must_zero(keys, what, skippable=False):
        for k in keys:
            v = val(k)
            if v is None and skippable and elab:
                notes.append(f"{what}: {k} absent; skipped (SYNTH_ELABORATE_ONLY design)")
            elif v is None:
                fails.append(f"{what}: {k} missing from metrics (not a complete flow result)")
            elif v != 0:
                fails.append(f"{what}: {k} = {v}")

    must_zero(["magic__drc_error__count"], "DRC (Magic)")
    must_zero(["klayout__drc_error__count"], "DRC (KLayout)")
    must_zero(["route__drc_errors"], "DRC (router)")
    lvs = sorted(k for k in m if k.startswith("design__lvs_") and k.endswith("count"))
    must_zero(lvs or ["design__lvs_error__count"], "LVS")
    must_zero(["design__xor_difference__count"], "XOR")
    must_zero(["antenna__violating__nets", "antenna__violating__pins", "route__antenna_violation__count"], "antenna")

    # timing: every corner's worst slack and every violation count
    ws = {k: v for k, v in m.items() if (k.startswith("timing__setup__ws") or k.startswith("timing__hold__ws"))}
    if not ws:
        fails.append("timing: no setup/hold worst-slack metrics")
    for k, v in sorted(ws.items()):
        if v < 0:
            fails.append(f"timing: {k} = {v:.3f} ns (negative slack)")
    for k, v in sorted(m.items()):
        if k.startswith("timing__") and "_vio__count" in k and v:
            fails.append(f"timing: {k} = {v} violating endpoints")
    for k in ("timing__setup__wns", "timing__hold__wns", "timing__setup__tns", "timing__hold__tns"):
        if (val(k) or 0) < 0:
            fails.append(f"timing: {k} = {val(k)}")

    must_zero(["synthesis__check_error__count"], "synthesis checks", True)
    must_zero(["design__instance_unmapped__count"], "unmapped cells", True)
    must_zero(["design__inferred_latch__count"], "inferred latches", True)

    # no logic lost
    seq = val("design__instance__count__class:sequential_cell")
    if seq is None and elab:
        seq = 0
        notes.append("sequential cells: metric absent in an elaborate-only design; counted as 0 (the macro's registers are in the macro)")
    info["seq"] = seq
    try:
        rtl, drvwarn, bd = rtl_registers(design)
        info['breakdown'] = bd
        info["rtl"] = rtl
        if drvwarn:
            fails.append(f"RTL drivers: Yosys reports {len(drvwarn)} conflicting/missing driver warning(s), e.g. '{drvwarn[0]}'")
        allow = 0
        try:
            allow = json.load(open(os.path.join(REPO, "scripts", "flow", "signoff_allowances.json"))
                              )["designs"].get(design, {}).get("removed_registers", 0)
        except (OSError, ValueError):
            pass
        info["allow"] = allow
        if seq is None:
            fails.append("sequential cells: design__instance__count__class:sequential_cell missing from metrics")
        elif seq < rtl - allow:
            fails.append(f"logic lost: {seq} sequential cells survive but the RTL elaborates to {rtl} registers"
                         f" (allowance {allow}) -> {rtl - allow - seq} registers removed by synthesis")
        elif seq > rtl:
            notes.append(f"{seq - rtl} more sequential cells than RTL registers (clock gating / synthesis duplicates)")
    except Exception as e:                                   # elaboration problems must fail, not pass silently
        fails.append(f"RTL register count: {e}")

    # informational, never failing
    for tag, base in (("max-slew", "design__max_slew_violation__count"), ("max-cap", "design__max_cap_violation__count")):
        if val(base) is not None:
            notes.append(f"{tag} violations: {val(base)}")
    res = None
    for p in (os.path.join(os.path.dirname(mpath), "resources.json"), os.path.join(REPO, "designs", design, "output", "resources.json")):
        if os.path.exists(p):
            res = json.load(open(p)); break
    if res:
        notes.append(f"peak memory {res.get('container_peak_mem_gb')} GB, wall time {res.get('wall_s_total')} s (resources.json)")
    mt = None
    cfg = cfg0
    for rj in ([os.path.join(rundir, "resolved.json")] if rundir else []):
        if os.path.exists(rj):
            mt = json.load(open(rj)).get("MAX_TRANSITION_CONSTRAINT")
    if mt is None:
        mt = cfg.get("MAX_TRANSITION_CONSTRAINT")
    if mt is not None and mt != PDK_MAX_TRANSITION:
        notes.append(f"MAX_TRANSITION_CONSTRAINT = {mt} (PDK default {PDK_MAX_TRANSITION})")
    # stale metrics: RTL or config edited after the run that produced them
    try:
        views = [os.path.join(REPO, "build", "macros", m, "SOURCE.txt") for m in macro_designs(cfg0)]
        newest = max(os.path.getmtime(f) for f in read_config(design)[2] + [os.path.join(REPO, "designs", design, "config.json")]
                     + [v for v in views if os.path.exists(v)])
        if newest > os.path.getmtime(mpath):
            notes.append("RTL/config/macro views newer than these metrics: re-run the flow before trusting this result")
    except OSError:
        pass
    info["notes"] = notes
    return fails, info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("design", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--metrics")
    ap.add_argument("--breakdown", action="store_true", help="print the RTL register count per register name")
    a = ap.parse_args()
    if not a.all and not a.design:
        ap.error("give a design or --all")
    names = DESIGNS if a.all else [ALIAS.get(a.design, a.design)]
    rows, bad = [], 0
    for d in names:
        if not os.path.isfile(os.path.join(REPO, "designs", d, "config.json")):
            print(f"check_signoff: unknown design '{d}'", file=sys.stderr); sys.exit(2)
        print(f"== {d}")
        fails, info = check(d, a.metrics)
        print(f"   metrics: {info['src']}")
        if info["rtl"] is not None:
            print(f"   registers: RTL {info['rtl']} (allowance {info['allow']}), surviving sequential cells {info['seq']}")
        if a.breakdown:
            for k, v in sorted(info.get("breakdown", {}).items(), key=lambda kv: -kv[1]):
                print(f"   rtl register {k}: {v}")
        for n in info.get("notes", []):
            print(f"   note: {n}")
        for f in fails:
            print(f"   FAIL: {f}")
        print(f"   => {'FAIL' if fails else 'PASS'}")
        bad += bool(fails)
        rows.append((d, info, fails))
    if a.all:
        print("\n%-18s %8s %9s %6s  %s" % ("design", "RTL regs", "seq cells", "allow", "result"))
        for d, i, f in rows:
            print("%-18s %8s %9s %6s  %s" % (d, i["rtl"] if i["rtl"] is not None else "-",
                  i["seq"] if i["seq"] is not None else "-", i["allow"], "PASS" if not f else "FAIL (%d): %s" % (len(f), f[0][:90])))
        print(f"\ncheck-all: {len(rows) - bad} of {len(rows)} pass")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
