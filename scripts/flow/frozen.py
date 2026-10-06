#!/usr/bin/env python3
# Docs: designs/FROZEN.md, scripts/flow/freeze.py, CLAUDE.md
"""frozen.py -- the guard API for tools and hooks: is a path part of the frozen, validated example designs?

    import sys; sys.path.insert(0, "<repo>/scripts/flow"); import frozen
    frozen.is_frozen(path)       True when path (absolute or relative to the repo root, symlinks resolved) is frozen
    frozen.reason(path)          a short string ("design kv_attn_n8: run input", "design kv_attn_n8: evidence", "model", "manifest") or None
    frozen.frozen_designs()      sorted design names listed in designs/FROZEN.json ([] when there is no manifest)
    frozen.frozen_files()        {repo-relative path: reason} of every file the manifest hashes, plus the manifest itself

A path is frozen when it is
  * a file the manifest hashes (run inputs, tb, vectors, model/ sources, output/metrics.json, output/layout.png), or
  * anything inside designs/<d>/ of a frozen design except runs/ (flow scratch), or
  * designs/FROZEN.json / designs/FROZEN.md.
No manifest -> nothing is frozen (is_frozen is False). Paths outside the repository are never frozen.
Command line: frozen.py <path>...  prints "FROZEN <path> (<reason>)" or "free <path>"; exit 1 when any path is frozen.
Standard library only; never touches the network, Docker or the design files.
"""
import json, os, sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
MANIFEST = os.path.join(REPO, "designs", "FROZEN.json")


def _load():
    try:
        with open(MANIFEST) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _rel(path):
    p = os.path.realpath(path if os.path.isabs(path) else os.path.join(REPO, path))
    base = os.path.realpath(REPO)
    if p != base and not p.startswith(base + os.sep):
        return None
    return os.path.relpath(p, base).replace(os.sep, "/")


def frozen_designs():
    m = _load()
    return sorted((m or {}).get("designs", {}))


def frozen_files():
    m = _load()
    if not m:
        return {}
    out = {"designs/FROZEN.json": "manifest", "designs/FROZEN.md": "manifest"}
    for f in m.get("model", {}):
        out[f] = "model"
    for d, e in m.get("designs", {}).items():
        for f in e.get("inputs", {}):
            out.setdefault(f, "design %s: run input" % d)
        for f in e.get("evidence", {}):
            out[f] = "design %s: evidence" % d
    return out


def reason(path):
    rel = _rel(path)
    if rel is None:
        return None
    files = frozen_files()
    if rel in files:
        return files[rel]
    parts = rel.split("/")
    if len(parts) >= 3 and parts[0] == "designs" and parts[1] in frozen_designs() and parts[2] != "runs":
        return "design %s: inside a frozen design directory" % parts[1]
    return None


def is_frozen(path):
    return reason(path) is not None


if __name__ == "__main__":
    bad = 0
    for p in sys.argv[1:]:
        r = reason(p)
        print(("FROZEN %s (%s)" % (p, r)) if r else "free %s" % p)
        bad |= bool(r)
    sys.exit(1 if bad else 0)
