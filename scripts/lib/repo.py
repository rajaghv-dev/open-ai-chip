#!/usr/bin/env python3
"""repo.py -- shared facts about this repository for the Python scripts and tools (standard library only).
Docs: docs/CODE_MAP.md, CLAUDE.md

Shell twin: scripts/lib/common.sh. Import with
    sys.path.insert(0, os.path.join(<repo>, "scripts", "lib")); import repo
  REPO                    repository root
  all_designs()           Makefile ALL_DESIGNS, in hardening order (the list `make all-designs` uses)
  design_dirs()           sorted names of designs/<d>/ that have a config.json
  config_path(d), config(d)   designs/<d>/config.json path / parsed
  resolve(d, v)           a config value: "dir::<rel>" -> absolute normalised path against designs/<d>/; other values unchanged
  rtl(d)                  (cfg, DESIGN_NAME, VERILOG_FILES, VERILOG_INCLUDE_DIRS, VERILOG_DEFINES) with dir:: resolved (absolute)
  load_json(path, default)    parsed JSON of a file (relative paths: against REPO), default when missing or invalid
  librelane_image()       env DOCKER_IMAGE, else LIBRELANE_IMAGE of versions.lock
  docker_env(environ)     {"DOCKER_HOST": ...} to add for a docker call, {} when DOCKER_HOST is already in environ or no
                          socket applies: the Colima VM "osl" socket if it exists (the Makefile rule), else on Linux
                          /var/run/docker.sock if it exists
Not used by model/ (its sources are hashed into generated files) nor by tests/check_docs.py (an independent checker).
"""
import glob, json, os, re, sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
COLIMA_SOCK = os.path.join(os.path.expanduser("~"), ".colima", "osl", "docker.sock")
LINUX_SOCK = "/var/run/docker.sock"


def all_designs():
    with open(os.path.join(REPO, "Makefile")) as f:
        m = re.search(r"^ALL_DESIGNS\s*:=((?:.*\\\n)*.*)$", f.read(), re.M)
    return m.group(1).replace("\\", " ").split() if m else []


def design_dirs():
    return sorted(os.path.basename(os.path.dirname(c)) for c in glob.glob(os.path.join(REPO, "designs", "*", "config.json")))


def config_path(design):
    return os.path.join(REPO, "designs", design, "config.json")


def config(design):
    with open(config_path(design)) as f:
        return json.load(f)


def resolve(design, v):
    if isinstance(v, str) and v.startswith("dir::"):
        return os.path.normpath(os.path.join(os.path.dirname(config_path(design)), v[5:]))
    return v


def rtl(design):
    cfg = config(design)
    files = [resolve(design, f) for f in cfg["VERILOG_FILES"]]
    incs = [resolve(design, f) for f in cfg.get("VERILOG_INCLUDE_DIRS", [])]
    return cfg, cfg["DESIGN_NAME"], files, incs, list(cfg.get("VERILOG_DEFINES", []))


def load_json(path, default=None):
    try:
        with open(os.path.join(REPO, path)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def librelane_image():
    if os.environ.get("DOCKER_IMAGE"):
        return os.environ["DOCKER_IMAGE"]
    with open(os.path.join(REPO, "versions.lock")) as f:
        m = re.search(r"^LIBRELANE_IMAGE=([^\s#]+)", f.read(), re.M)
    return m.group(1)


def docker_env(environ=None):
    environ = os.environ if environ is None else environ
    if "DOCKER_HOST" in environ:
        return {}
    if os.path.exists(COLIMA_SOCK):
        return {"DOCKER_HOST": "unix://" + COLIMA_SOCK}
    if sys.platform != "darwin" and os.path.exists(LINUX_SOCK):
        return {"DOCKER_HOST": "unix://" + LINUX_SOCK}
    return {}


if __name__ == "__main__":
    print("\n".join(all_designs()))
