#!/usr/bin/env python3
# Docs: .claude/skills/harden-design/SKILL.md
"""design_info.py <design> files|incs|top|defs -- read a design's config.json (the single source of its RTL list).

  files   VERILOG_FILES, one repository-relative path per line, in load order
  incs    VERILOG_INCLUDE_DIRS (one per line)
  top     DESIGN_NAME
  defs    VERILOG_DEFINES (one per line)
Paths with a dir:: prefix are resolved against designs/<design>/ and printed relative to the repository root.
design_info.py --list   every design of Makefile ALL_DESIGNS, one per line, in hardening order
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import repo  # noqa: E402  (scripts/lib/repo.py: config.json reading and dir:: resolution)

REPO = repo.REPO


def main():
    if sys.argv[1:] == ["--list"]:
        print("\n".join(repo.all_designs()))
        return
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    design, what = sys.argv[1:3]
    cfg, top, files, incs, defs = repo.rtl(design)
    rel = lambda v: os.path.relpath(v, REPO) if os.path.isabs(v) else v
    if what == "files":
        out = [rel(f) for f in files]
    elif what == "incs":
        out = [rel(f) for f in incs]
    elif what == "defs":
        out = defs
    elif what == "top":
        out = [top]
    else:
        sys.exit(__doc__)
    print("\n".join(out))


if __name__ == "__main__":
    main()
