#!/usr/bin/env python3
# Docs: docs/VALIDATION.md, docs/CODE_MAP.md
"""check_traceability.py -- every code file names the parent document that explains it.

Convention: each code file (*.py *.sh *.tcl *.c *.h *.v *.vh *.mk, Makefile) outside designs/, shared/, third_party/,
build/, runs/ and dot-directories has, in its first 60 lines, a line `Docs: <repo-relative .md>[, <.md> ...]`.
This tool reports files with no such line or whose paths do not exist (header or map). Standard library only, no network.

  python3 tests/check_traceability.py          exit 1 if any problem (prints one line per problem and a summary)
  python3 tests/check_traceability.py --list   print "file -> doc, doc" for every code file
A file whose edit would make runs stale (model/**) is mapped in tests/traceability_map.txt instead.
Also imported by scripts/docs/code_map.py (functions code_files, parse_docs, header_purpose).
"""
import fnmatch, os, re, subprocess, sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXT = (".py", ".sh", ".tcl", ".c", ".h", ".v", ".vh", ".mk")
SKIP_TOP = ("designs", "shared", "build")
HEAD_LINES = 60
DOCS_RE = re.compile(r"\bDocs:\s*(.*)")
MD_RE = re.compile(r"[A-Za-z0-9_./\-]+\.md")


def code_files():
    """Tracked plus untracked-but-not-ignored files that the convention covers, sorted."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=REPO, capture_output=True, text=True, check=True).stdout.split("\n")
    res = []
    for p in out:
        if not p or not os.path.isfile(os.path.join(REPO, p)):
            continue
        parts = p.split("/")
        if parts[0] in SKIP_TOP or parts[0].startswith(".") or "third_party" in parts or "runs" in parts:
            continue
        if p.endswith(EXT) or parts[-1] == "Makefile":
            res.append(p)
    return sorted(set(res))


def head(path):
    with open(os.path.join(REPO, path), errors="replace") as f:
        return [next(f, "") for _ in range(HEAD_LINES)]


def load_map():
    """[(glob, [md, ...])] from tests/traceability_map.txt (files that must not be edited: model/**, third_party, generated)."""
    res = []
    try:
        lines = open(os.path.join(REPO, "tests", "traceability_map.txt")).read().split("\n")
    except OSError:
        return res
    for l in lines:
        l = l.split("#", 1)[0].strip()
        if "->" in l:
            g, d = l.split("->", 1)
            res.append((g.strip(), MD_RE.findall(d)))
    return res


MAP = load_map()


def parse_docs(path):
    """List of .md paths from the first `Docs:` header line, else from tests/traceability_map.txt, else None."""
    for line in head(path):
        m = DOCS_RE.search(line)
        if m:
            return MD_RE.findall(m.group(1))
    for g, d in MAP:
        if fnmatch.fnmatch(path, g):
            return d
    return None


def header_purpose(path):
    """First sentence-ish line of the header comment (the purpose), best effort, without comment markers."""
    for line in head(path):
        s = line.strip()
        if s.startswith("#!") or not s:
            continue
        s = re.sub(r'^(#+|//+|/\*+|\*+|"""|\'\'\')\s*', "", s).strip()
        if not s or s.startswith(("Docs:", "-*-")):
            continue
        s = re.sub(r"\s+--?\s+", " - ", s, count=1) if " -- " in s else s
        return s.rstrip('"\' ')[:160]
    return ""


def main():
    files = code_files()
    if "--list" in sys.argv:
        for f in files:
            d = parse_docs(f)
            print("%s -> %s" % (f, ", ".join(d) if d else "(missing)"))
        return 0
    bad = 0
    for f in files:
        d = parse_docs(f)
        if not d:
            print("MISSING Docs: line   %s" % f)
            bad += 1
            continue
        for md in d:
            if not os.path.isfile(os.path.join(REPO, md)):
                print("NO SUCH DOC          %s -> %s" % (f, md))
                bad += 1
    print("traceability: %d files checked, %d problem(s)" % (len(files), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
