#!/usr/bin/env python3
"""check_docs.py <links|targets|inventory|evidence> -- repository consistency checks used by tests/run_tests.sh. Standard library only,
no Docker, no build/. Each check prints one line per problem and exits 1 when there is any.

  links      every relative link [text](path) in every git-tracked *.md resolves to a file or directory (anchors are not checked;
             links into git-ignored build outputs (build/, runs/) and URLs are skipped; code spans and fenced code are ignored)
  targets    every `make <target>` named in a code span or code block of the docs exists in the Makefile, and every target
             listed by `make help` exists
  inventory  every design of Makefile ALL_DESIGNS has config.json, NOTES.md, README.md, tb/<d>_tb.v, output/{metrics.json,layout.png,
             flow.log}, is in scripts/docs/tables.py ORDER and named in README.md; the design directories, ALL_DESIGNS, ORDER and the
             design count words in README.md / CLAUDE.md / make help agree; every model dir is in Makefile MODELS and check_generated.sh
  evidence   numbers quoted in a design README "Status" paragraph (std cells, flip-flops, die, setup/hold slack) equal
             designs/<d>/output/metrics.json (the README rounds: a tolerance of half a unit of the last quoted digit is allowed)"""
import json, os, re, subprocess, sys

REPO = os.path.abspath(os.environ.get("CHECK_DOCS_ROOT") or os.path.join(os.path.dirname(__file__), ".."))   # CHECK_DOCS_ROOT: self-test on a scratch tree
os.chdir(REPO)
problems = []


def bad(msg):
    problems.append(msg)


def tracked(suffix):
    """git-tracked files ending in suffix (a plain walk without build/runs when git is not available)."""
    try:
        out = subprocess.run(["git", "ls-files", "*" + suffix], capture_output=True, text=True, check=True).stdout.split("\n")
        out = [f for f in out if f]
        if out:
            return out
    except Exception:
        pass
    res = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in (".git", "build", "runs", "node_modules", "venv", "__pycache__")]
        res += [os.path.join(root, f)[2:] for f in files if f.endswith(suffix)]
    return res


def makefile_designs():
    t = open("Makefile").read()
    m = re.search(r"^ALL_DESIGNS\s*:=((?:.*\\\n)*.*)$", t, re.M)
    return m.group(1).replace("\\", " ").split()


def strip_code(text):
    text = re.sub(r"^(```|~~~).*?^(\1)", "", text, flags=re.S | re.M)       # fenced blocks
    return re.sub(r"`[^`\n]*`", "", text)                                   # inline code spans


def links():
    n = 0
    mds = tracked(".md")
    for f in mds:
        if not os.path.isfile(f):
            continue
        text = strip_code(open(f, errors="replace").read())
        for m in re.finditer(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text):
            tgt = m.group(1).strip("<>")
            if re.match(r"^[a-z][a-z0-9+.-]*:", tgt) or tgt.startswith("#") or tgt.startswith("//"):
                continue
            path = tgt.split("#")[0].split("?")[0]
            if not path:
                continue
            if path.startswith("/"):
                bad("%s: absolute link %s" % (f, tgt)); continue
            full = os.path.normpath(os.path.join(os.path.dirname(f), path))
            rel = os.path.relpath(full, REPO)
            if rel.startswith("build" + os.sep) or "/runs/" in rel:
                continue
            n += 1
            if not os.path.exists(full):
                bad("%s: broken link %s" % (f, tgt))
    return "%d relative links in %d markdown files resolve" % (n, len(mds))


def make_targets():
    t = open("Makefile").read()
    tg = set()
    for m in re.finditer(r"^([A-Za-z][A-Za-z0-9_ -]*):(?![=])", t, re.M):    # also "caravel-rtl caravel-gl:" rules
        tg |= set(m.group(1).split())
    for m in re.finditer(r"^\.PHONY:(.*)$", t, re.M):
        tg |= set(m.group(1).split())
    return tg


def targets():
    tg = make_targets()
    helped = set(re.findall(r"^  ([a-z][a-z0-9-]*)\b", subprocess.run(["make", "help"], capture_output=True, text=True).stdout, re.M))
    for t in sorted(helped - tg):
        bad("make help lists '%s' which is not a Makefile target" % t)
    seen = set()
    # not checked: historic plans and other projects' targets (SPEC.md plan body, LOCAL_RUN_PLAN.md and docs/SOC_PLAN.md name the upstream
    # template's targets, docs/slides/README.md describes the sibling repository's), and the agent tests (tests/tools/)
    SKIP = ("SPEC.md", "LOCAL_RUN_PLAN.md", "docs/SOC_PLAN.md", "docs/slides/README.md")
    PROSE = {"a", "the", "command", "it", "sure", "this", "that", "to", "sense", "your", "them", "an", "any", "each", "one", "all", "use"}
    for f in tracked(".md"):
        if f.startswith("tests/tools/") or f in SKIP or not os.path.isfile(f):
            continue
        text = open(f, errors="replace").read()
        spans = re.findall(r"`([^`\n]*\bmake\b[^`\n]*)`", text)
        for blk in re.findall(r"^(?:```|~~~)[^\n]*\n(.*?)^(?:```|~~~)", text, re.S | re.M):
            spans += blk.split("\n")
        for s in spans:
            s = re.sub(r"\s#.*$", "", s)
            for m in re.finditer(r"(?:^|[\s;&|(])make\s+((?:-\S+\s+(?:\S+\s+)?)*)([A-Za-z][A-Za-z0-9_-]*)(?![A-Za-z0-9_<-])", s):
                opts, tgt = m.group(1), m.group(2)
                if "-C" in opts or tgt in PROSE or tgt.endswith("-"):
                    continue
                seen.add(tgt)
                if tgt not in tg:
                    bad("%s: `make %s` is not a Makefile target" % (f, tgt))
    return "%d distinct make targets named in the docs all exist (%d targets in the Makefile)" % (len(seen), len(tg))


WORDS = {20: "Twenty", 21: "Twenty-one", 22: "Twenty-two", 23: "Twenty-three", 24: "Twenty-four", 25: "Twenty-five", 26: "Twenty-six",
         27: "Twenty-seven", 28: "Twenty-eight", 29: "Twenty-nine", 30: "Thirty"}


def inventory():
    all_d = makefile_designs()
    dirs = sorted(d for d in os.listdir("designs") if os.path.isfile("designs/%s/config.json" % d))
    order = re.findall(r'"([a-z0-9_]+)"', re.search(r"ORDER = \[(.*?)\]", open("scripts/docs/tables.py").read(), re.S).group(1))
    readme, mk = open("README.md").read(), open("Makefile").read()
    if sorted(all_d) != dirs:
        bad("Makefile ALL_DESIGNS != designs/*/config.json: %s" % sorted(set(all_d) ^ set(dirs)))
    if sorted(order) != sorted(all_d):
        bad("tables.py ORDER != Makefile ALL_DESIGNS: %s" % sorted(set(order) ^ set(all_d)))
    if len(set(all_d)) != len(all_d):
        bad("duplicate in ALL_DESIGNS")
    for d in all_d:
        for f in ("config.json", "NOTES.md", "tb/%s_tb.v" % d, "output/metrics.json", "output/layout.png", "output/flow.log"):
            if not os.path.isfile("designs/%s/%s" % (d, f)):
                bad("%s: missing designs/%s/%s" % (d, d, f))
        if not re.search(r"(?<![\w])%s(?![\w])" % d, readme):
            bad("%s: not named in README.md" % d)
    n = len(all_d)
    for f in ("README.md", "CLAUDE.md"):
        head = "\n".join(open(f).read().split("\n")[:6])
        if WORDS.get(n, str(n)) not in head:
            bad("%s: the first lines do not say '%s' designs (ALL_DESIGNS has %d)" % (f, WORDS.get(n, n), n))
    if "all %d designs" % n not in subprocess.run(["make", "help"], capture_output=True, text=True).stdout:
        bad("make help does not say 'all %d designs'" % n)
    models = re.search(r"^MODELS\s*:=(.*)$", mk, re.M).group(1).split()
    gen = open("scripts/check_generated.sh").read()
    for m in models:
        if not os.path.isdir("model/%s" % m):
            bad("MODELS names model/%s which does not exist" % m)
        if m not in gen and not (m == "kv_attention" and "kv_attn" in gen):
            bad("model %s is not covered by scripts/check_generated.sh" % m)
    for m in sorted(os.listdir("model")):
        if os.path.isdir("model/%s" % m) and m not in models and m not in ("examples", "__pycache__"):
            bad("model/%s is not in Makefile MODELS" % m)
    return "%d designs: files, tables.py ORDER, README, Makefile and %d model dirs agree" % (n, len(models))


def metric_min(m, prefix):
    v = [x for k, x in m.items() if k.startswith(prefix) and isinstance(x, (int, float))]
    return min(v) if v else None


def tol_of(q):
    return 0.5 * 10 ** -(len(q.split(".")[1]) if "." in q else 0) + 1e-9


def evidence():
    n = 0
    for d in makefile_designs():
        mp, rp = "designs/%s/output/metrics.json" % d, "designs/%s/README.md" % d
        if not (os.path.isfile(mp) and os.path.isfile(rp)):
            continue
        m = json.load(open(mp))
        mm = re.search(r"^[ >*]*Status\b.*?(?:\n\n|\Z)|^\*\*Status[:*].*?(?:\n\n|\Z)", open(rp).read(), re.S | re.M)
        if not mm:
            continue
        st = re.split(r"\b(?:Compared with|sibling|Before it)\b", mm.group(0))[0]   # later sentences may quote another design
        checks = []
        for g in re.finditer(r"([\d,]+) (?:std cells|cells)\b", st):
            checks.append(("std cells", int(g.group(1).replace(",", "")), m.get("design__instance__count__stdcell"), 0))
        for g in re.finditer(r"([\d,]+) flip-flops", st):
            checks.append(("flip-flops", int(g.group(1).replace(",", "")), m.get("design__instance__count__class:sequential_cell"), 0))
        g = re.search(r"(\d+) x (\d+) um(?! ?2)", st)
        if g:
            bb = str(m.get("design__die__bbox", "")).split()
            if len(bb) == 4:
                checks.append(("die width", int(g.group(1)), float(bb[2]) - float(bb[0]), 0))
                checks.append(("die height", int(g.group(2)), float(bb[3]) - float(bb[1]), 0))
        for g in re.finditer(r"\bsetup (?:slack|WNS) \+?(-?[\d.]+) ns", st):
            checks.append(("worst setup slack", float(g.group(1)), metric_min(m, "timing__setup__ws__corner:"), tol_of(g.group(1))))
        for g in re.finditer(r"\bhold (?:slack |WNS )?\+?(\d[\d.]*) ns", st):
            checks.append(("worst hold slack", float(g.group(1)), metric_min(m, "timing__hold__ws__corner:"), tol_of(g.group(1))))
        for g in re.finditer(r"\b(max_ss|nom_ss|min_ss) (-?[\d.]+) ns", st):
            c = "timing__setup__ws__corner:%s_100C_1v60" % g.group(1)
            checks.append(("setup WNS " + g.group(1), float(g.group(2)), m.get(c), tol_of(g.group(2))))
        for what, quoted, actual, tol in checks:
            n += 1
            if actual is None:
                bad("%s: README quotes %s %s but metrics.json has no such value" % (d, what, quoted))
            elif abs(quoted - actual) > tol:
                bad("%s: README Status says %s %s, %s says %s" % (d, what, quoted, mp, actual))
    return "%d numbers quoted in design README Status paragraphs match output/metrics.json" % n


if __name__ == "__main__":
    fns = {"links": links, "targets": targets, "inventory": inventory, "evidence": evidence}
    if len(sys.argv) != 2 or sys.argv[1] not in fns:
        sys.exit(__doc__)
    msg = fns[sys.argv[1]]()
    for p in problems:
        print("    " + p)
    if problems:
        sys.exit(1)
    print(msg)
