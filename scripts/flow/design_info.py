#!/usr/bin/env python3
"""design_info.py <design> files|incs|top|dir -- read a design's config.json (the single source of its RTL list).

  files   VERILOG_FILES, one repository-relative path per line, in load order
  incs    VERILOG_INCLUDE_DIRS (one per line)
  top     DESIGN_NAME
  defs    VERILOG_DEFINES (one per line)
Paths with a dir:: prefix are resolved against designs/<design>/ and printed relative to the repository root.
"""
import json, os, sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    design, what = sys.argv[1:3]
    cfgp = os.path.join(REPO, "designs", design, "config.json")
    cfg = json.load(open(cfgp))
    base = os.path.dirname(cfgp)

    def fix(v):
        if v.startswith("dir::"):
            return os.path.relpath(os.path.normpath(os.path.join(base, v[5:])), REPO)
        return v
    if what == "files":
        out = [fix(f) for f in cfg["VERILOG_FILES"]]
    elif what == "incs":
        out = [fix(f) for f in cfg.get("VERILOG_INCLUDE_DIRS", [])]
    elif what == "defs":
        out = list(cfg.get("VERILOG_DEFINES", []))
    elif what == "top":
        out = [cfg["DESIGN_NAME"]]
    else:
        sys.exit(__doc__)
    print("\n".join(out))


if __name__ == "__main__":
    main()
