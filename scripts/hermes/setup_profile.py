#!/usr/bin/env python3
# Docs: docs/HERMES_AGENT_INTEGRATION.md
"""Prepare the Hermes Agent `chip` profile for this repo (called by scripts/hermes_agent_setup.sh).

Default is a DRY RUN: it prints a unified diff of every file it would create or change under the Hermes home and the
exact `hermes` commands it would run. Nothing is written without --apply. --apply first backs up every file it will
touch to <home>/backups/open-ai-chip-<timestamp>/ (with a manifest), then applies. --uninstall (with --apply) undoes it.
Re-running is idempotent. It never reads or prints .env, auth.json, pairing/ or any secret, and never copies a key:
only the non-secret model settings provider/base_url are taken from the existing config, and only if the URL is loopback.

Hermes home: --hermes-home DIR, else $HERMES_HOME, else ~/.hermes. Profile dir: <home>/profiles/<profile>.
Facts it relies on (Hermes v0.21.5 source and docs under ~/.hermes/hermes-agent):
  - `hermes -p NAME` selects a profile; `hermes profile create NAME --no-alias --no-skills` makes an empty one.
  - shell hooks: `hooks:` in the profile config.yaml; first use needs consent recorded in <profile>/shell-hooks-allowlist.json
  - cron --script must live in <profile>/scripts/; --no-agent runs it without an LLM; --deliver local keeps output local
  - MCP tools are named mcp_<server>_<tool>; `tools.include` filters by the server's own tool names
Stdlib only.
"""
import argparse
import datetime as dt
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MARK_BEGIN = "# >>> open-ai-chip managed block (scripts/hermes_agent_setup.sh); edit the script, not this block >>>"
MARK_END = "# <<< open-ai-chip managed block <<<"
MANAGED_KEYS = ["model", "providers", "agent", "tools", "platform_toolsets", "mcp_servers", "skills", "approvals", "hooks", "memory",
                "plugins"]
PLUGIN = "open-ai-chip"   # scripts/hermes/plugin/open-ai-chip: slash commands + pre_llm_call fast path (no model turn), linked into the profile
MODEL = "qwen3.5-64k:9b"
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
JOB_TEST = "open-ai-chip nightly make test"
JOB_FULL = "open-ai-chip weekly make test-full"
PROJECT = "open-ai-chip"

DISABLED_TOOLSETS = [
    "terminal", "code_execution", "computer_use", "delegation", "kanban", "image_gen", "video_gen", "memory",
    "search", "web", "browser", "vision", "spotify", "x_search", "todo", "cronjob", "tts", "discord",
    "discord_admin", "feishu_doc", "feishu_drive", "homeassistant", "yuanbao",
]
# Measured by the tool-calling eval (build/agent/tool_eval_PLAN.md section 6): with the "file" toolset the model read files instead of calling
# the MCP tools (1/8 cases); without it, with tool_search off and the "core" MCP tools, 8/8. So no "file" here.
ENABLED_TOOLSETS = ["skills", "session_search"]

# Local demo models, selectable in the Hermes.app model picker (and `hermes -p chip -m <model>`). All installed in Ollama (checked with
# `ollama list`); nothing is pulled and no cloud model is listed. The default stays MODEL (the one evaluated: 87.9%, see docs).
DEMO_TOOLS = False
GRAFANA = False      # --grafana: add the read-only local Grafana MCP server (docs/GRAFANA.md)
DEMO_EXTRA_TOOLS = ["log_digest", "param_info", "propose_change", "whatif_run", "whatif_result"]
PROVIDER_NAME = "ollama-local"
DEMO_MODELS = ["qwen3.5-64k:9b", "hermes3:8b", "gemma3:4b-it-qat", "gemma4:12b", "mistral-nemo:latest"]

DENY = [
    "cf", "cf *",
    "git push*",
    "rm -rf*", "rm -fr*", "rm -r *", "rm -f *",
    "docker run*", "docker exec*", "docker rm*", "docker rmi*", "docker system prune*", "docker compose*",
    "make gds*", "make flow-all*", "make flow *", "make precheck*", "make gl *", "make gl-final*",
    "make test-full*", "make caravel-*", "make wrapper*", "make views*", "make collect*", "make soc-*",
    "make adapter-test*", "make generate*", "make table*", "make freeze*", "make clean*",
    "*DISABLE_LVS*", "*gh repo*visibility*", "*gh repo edit*", "*gh repo delete*",
    "sed -i*designs/*", "sed -i*shared/*", "sed -i*model/*",
    "*> designs/*", "*>> designs/*", "*> shared/*", "*>> shared/*", "*> model/*", "*>> model/*",
    "tee *designs/*", "tee *shared/*", "tee *model/*",
    "rm *designs/*", "rm *shared/*", "rm *model/*",
    "mv *designs/*", "mv *shared/*", "mv *model/*",
    "cp * designs/*", "cp * shared/*", "cp * model/*",
    "*open-ai-silicon*>*",
]


# ---------------------------------------------------------------- tiny YAML writer / chunker (no PyYAML needed)
def y_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(str(v))


def y_emit(obj, indent=0):
    pad = " " * indent
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.extend(y_emit(v, indent + 2))
            elif isinstance(v, list):
                lines.append(f"{pad}{k}: []")
            elif isinstance(v, dict):
                lines.append(f"{pad}{k}: {{}}")
            else:
                lines.append(f"{pad}{k}: {y_scalar(v)}")
    elif isinstance(obj, list):
        for it in obj:
            if isinstance(it, dict):
                sub = y_emit(it, indent + 2)
                lines.append(f"{pad}- {sub[0].lstrip()}")
                lines.extend(sub[1:])
            else:
                lines.append(f"{pad}- {y_scalar(it)}")
    return lines


TOP_KEY = re.compile(r"^([A-Za-z_][\w.-]*)\s*:")


def split_chunks(text):
    """[(key or None, text)] split at column-0 keys; a chunk keeps its indented lines, comments and blanks."""
    chunks = []
    cur_key, cur = None, []
    for line in text.splitlines():
        m = TOP_KEY.match(line)
        if m:
            chunks.append((cur_key, cur))
            cur_key, cur = m.group(1), [line]
        else:
            cur.append(line)
    chunks.append((cur_key, cur))
    return [(k, "\n".join(ls)) for k, ls in chunks if ls]


def strip_managed(text):
    out, skip = [], False
    for line in text.splitlines():
        if line.startswith("# >>> open-ai-chip managed block"):
            skip = True
            continue
        if line.startswith("# <<< open-ai-chip managed block"):
            skip = False
            continue
        if not skip:
            out.append(line)
    return "\n".join(out)


def merge_config(existing, managed):
    base = strip_managed(existing)
    kept = [t for k, t in split_chunks(base) if k not in managed]
    keep_txt = "\n".join(t.rstrip() for t in kept if t.strip()).strip("\n")
    block = "\n".join([MARK_BEGIN] + y_emit(managed) + [MARK_END])
    return (keep_txt + "\n\n" if keep_txt else "") + block + "\n"


# ---------------------------------------------------------------- inputs
def read_source_model(home):
    """Non-secret model routing from the existing default config, local URLs only. Never reads api_key."""
    out = {"provider": "custom", "base_url": DEFAULT_BASE_URL}
    cfg = home / "config.yaml"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return out
    for k, t in split_chunks(text):
        if k != "model":
            continue
        for key in ("provider", "base_url"):
            m = re.search(rf"^  {key}:\s*['\"]?([^'\"#\n]+?)['\"]?\s*(#.*)?$", t, re.M)
            if m:
                out[key] = m.group(1).strip()
    if not re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/|$)", out["base_url"]):
        out = {"provider": "custom", "base_url": DEFAULT_BASE_URL}   # local-only rule: never inherit a remote URL
    return out


def tool_include():
    try:
        cur = json.loads((REPO / "tools" / "hermes_tools.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    names = [k for k in (cur.get("core") or cur.get("include", {})) if not k.startswith("//")]   # "core" = the measured default subset
    names += [k for k in cur.get("aliases", {}) if not k.startswith("//") and k not in names]      # ask_claude, claude_status
    if DEMO_TOOLS:   # --demo-tools: the what-if and log-digest tools that only the demos need (outside the measured set)
        names += [k for k in DEMO_EXTRA_TOOLS if k in cur.get("include", {}) and k not in names]
    return names


def grafana_server():
    """--grafana: the local Grafana MCP entry from scripts/hermes/grafana_mcp.json (docs/GRAFANA.md). Read-only: the wrapper
    starts mcp-grafana with -disable-write and a Viewer token from the Keychain, and only four read tools are included."""
    if not GRAFANA:
        return {}
    e = json.loads((REPO / "scripts" / "hermes" / "grafana_mcp.json").read_text(encoding="utf-8"))["mcp_servers"]["grafana"]
    return {"grafana": json.loads(json.dumps(e).replace("__REPO__", str(REPO)))}


def managed_config(home):
    src = read_source_model(home)
    server = {
        "command": str(REPO / "build" / "agent" / "venv" / "bin" / "python"),
        "args": [str(REPO / "tools" / "hermes_mcp_bridge.py")],
        "timeout": 300,
        "connect_timeout": 90,
    }
    inc = tool_include()
    tools = {"prompts": False, "resources": False}
    if inc:
        tools["include"] = inc
    server["tools"] = tools
    hook = str(REPO / "scripts" / "hermes" / "hooks" / "pre_tool_call.py")
    return {
        "model": {"default": MODEL, "provider": PROVIDER_NAME, "base_url": src["base_url"],
                  "context_length": 65536, "ollama_num_ctx": 65536},
        "providers": {PROVIDER_NAME: {
            "name": "Local Ollama (demo models)", "api": src["base_url"], "default_model": MODEL, "models": DEMO_MODELS,
            "discover_models": False}},
        "agent": {"max_turns": 40, "disabled_toolsets": DISABLED_TOOLSETS},
        "tools": {"tool_search": {"enabled": "off"}},
        "platform_toolsets": {"cli": ENABLED_TOOLSETS, "tui": ENABLED_TOOLSETS, "acp": ENABLED_TOOLSETS},
        "mcp_servers": {"chip": server, **grafana_server()},
        "skills": {"external_dirs": [str(REPO / ".claude" / "skills")]},
        "approvals": {"mode": "manual", "cron_mode": "deny", "single_query_mode": "deny", "unattended_mode": "deny",
                      "deny": DENY},
        "hooks": {"pre_tool_call": [{"command": hook, "timeout": 10, "fail_closed": True}]},
        "memory": {"memory_enabled": False, "user_profile_enabled": False},
        "plugins": {"enabled": [PLUGIN]},
    }, hook


def iso_mtime(path):
    return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def allowlist_text(existing, hook):
    try:
        data = json.loads(existing) if existing else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    ap = data.get("approvals")
    if not isinstance(ap, list):
        ap = []
    if not any(isinstance(e, dict) and e.get("event") == "pre_tool_call" and e.get("command") == hook for e in ap):
        ap.append({"event": "pre_tool_call", "command": hook,
                   "approved_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                   "script_mtime_at_approval": iso_mtime(Path(hook))})
    data["approvals"] = ap
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def wrapper_text(kind):
    return ("#!/bin/sh\n# open-ai-chip: Hermes cron job body (generated by scripts/hermes_agent_setup.sh)\n"
            f'exec bash "{REPO}/scripts/hermes/cron_run.sh" {kind}\n')


def env_recipe_text():
    return json.dumps({
        "version": 1,
        "recipe": {"name": "open-ai-chip (make test only)", "kind": "make", "bootstrap": [], "build": [],
                   "test": ["make test"], "start": None, "port": None, "readinessPath": "/",
                   "evidence": ["Makefile target test = tests/run_tests.sh (no Docker, about 65 s)",
                                "make check needs a design and Docker results, so it is not part of the recipe"]},
    }, indent=2) + "\n"


# ---------------------------------------------------------------- hermes CLI helpers
class Ctx:
    def __init__(self, a):
        self.home = Path(a.hermes_home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser().resolve()
        self.profile = a.profile
        self.explicit_home = bool(a.hermes_home or os.environ.get("HERMES_HOME"))
        self.pdir = self.home / "profiles" / self.profile

    def env(self):
        e = dict(os.environ)
        if self.explicit_home:
            e["HERMES_HOME"] = str(self.home)
        return e

    def prefix(self):
        return f"HERMES_HOME={self.home} " if self.explicit_home else ""

    def hermes(self, *args, check=True, timeout=300):
        r = subprocess.run(["hermes", *args], env=self.env(), capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise RuntimeError(f"hermes {' '.join(args)} failed ({r.returncode}): {(r.stderr or r.stdout)[-400:]}")
        return r


def shown(ctx, p):
    s = str(p)
    return s.replace(str(ctx.home), "$HERMES_HOME") if s.startswith(str(ctx.home)) else s.replace(str(REPO), "<repo>")


def read(p):
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def diff(ctx, p, new):
    old = read(p)
    if old == new:
        return None
    a = "/dev/null" if not p.exists() else "a/" + shown(ctx, p)
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), a, "b/" + shown(ctx, p)))


def plan_files(ctx):
    cfg, hook = managed_config(ctx.home)
    pdir = ctx.pdir
    files = {
        pdir / "config.yaml": (merge_config(read(pdir / "config.yaml"), cfg), 0o600),
        pdir / "shell-hooks-allowlist.json": (allowlist_text(read(pdir / "shell-hooks-allowlist.json"), hook), 0o600),
        pdir / "scripts" / "chip_nightly_test.sh": (wrapper_text("test"), 0o755),
        pdir / "scripts" / "chip_weekly_test_full.sh": (wrapper_text("test-full"), 0o755),
    }
    repo_files = {REPO / ".hermes" / "environment.json": (env_recipe_text(), 0o644)}
    return files, repo_files


def commands(ctx):
    p = ctx.prefix()
    return [
        (f"{p}hermes profile create {ctx.profile} --no-alias --no-skills --description "
         f"\"open-ai-chip: read-only chip agent, local qwen, gated MCP tools\"", "only if the profile does not exist"),
        (f"{p}hermes -p {ctx.profile} project create {PROJECT} <repo> --primary <repo>", "only if the project does not exist"),
        (f"{p}hermes -p {ctx.profile} cron create '30 2 * * *' --name '{JOB_TEST}' --no-agent "
         f"--script chip_nightly_test.sh --deliver local --workdir <repo>", "only if the job does not exist"),
        (f"{p}hermes -p {ctx.profile} cron create '0 3 * * 0' --name '{JOB_FULL}' --no-agent "
         f"--script chip_weekly_test_full.sh --deliver local --workdir <repo>", "only if the job does not exist"),
    ]


def running_sessions():
    try:
        r = subprocess.run(["pgrep", "-fl", r"hermes( |-agent ).*(chat|tui|gateway|acp)|Hermes.app"],
                           capture_output=True, text=True, timeout=5)
        return [ln for ln in r.stdout.splitlines() if "pgrep" not in ln and "setup_profile" not in ln][:5]
    except Exception:
        return []


# ---------------------------------------------------------------- actions
def write_file(p, text, mode):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    os.chmod(p, mode)


def existing_names(ctx, sub):
    r = ctx.hermes("-p", ctx.profile, *sub, check=False)
    return r.stdout + r.stderr


def plugin_link(ctx):
    return ctx.pdir / "plugins" / PLUGIN


def install_plugin(ctx):
    """Symlink the repo plugin into the profile, so repo edits apply without re-running the setup."""
    link, src = plugin_link(ctx), REPO / "scripts" / "hermes" / "plugin" / PLUGIN
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() and Path(os.readlink(link)) == src:
        print(f"plugin link exists: {shown(ctx, link)}")
        return
    if link.exists() and not link.is_symlink():
        print(f"not replacing {shown(ctx, link)}: it is not a link made by this script", file=sys.stderr)
        return
    if link.is_symlink():
        link.unlink()
    link.symlink_to(src)
    print(f"plugin linked: {shown(ctx, link)}")


def do_install(ctx, apply):
    files, repo_files = plan_files(ctx)
    print(f"Hermes home : {ctx.home}{'' if ctx.explicit_home else '  (default)'}")
    print(f"Profile     : {ctx.profile}  ({'exists' if ctx.pdir.is_dir() else 'will be created'})")
    print(f"Repo        : {REPO}")
    print(f"Mode        : {'APPLY' if apply else 'DRY RUN (nothing is written; add --apply)'}")
    sess = running_sessions()
    if sess:
        print("WARNING: Hermes appears to be running; config changes apply to new sessions only:")
        for s in sess:
            print("   ", s[:120])
    print()
    n_changes = 0
    if not ctx.pdir.is_dir():
        print("# profile does not exist yet; `hermes profile create` writes its own skeleton (SOUL.md, .env, dirs).")
        print("# The diff below is against an empty config.yaml; the real file is the skeleton with the model block replaced.\n")
    for p, (text, _m) in {**files, **repo_files}.items():
        d = diff(ctx, p, text)
        if d is None:
            print(f"== unchanged: {shown(ctx, p)}")
        else:
            n_changes += 1
            print(f"== {'create' if not p.exists() else 'change'}: {shown(ctx, p)}")
            print(d if d.endswith("\n") else d + "\n")
    print("== hermes commands that --apply runs (after the backup):")
    for c, note in commands(ctx):
        print(f"  {c}\n      ({note})")
    print(f"== link: {shown(ctx, plugin_link(ctx))} -> <repo>/scripts/hermes/plugin/{PLUGIN}  (slash commands /klayout /timing /run ..., no model turn)")
    print("\n== not touched: .env, auth.json, pairing/, the default profile's config.yaml, any other profile.")
    print("== the allowlist entry pre-approves exactly one hook command (the repo hook above) for this profile.")
    if not apply:
        print(f"\nDry run finished: {n_changes} file(s) would change. Review, then run again with --apply.")
        return 0

    if shutil.which("hermes") is None:
        print("hermes not found on PATH", file=sys.stderr)
        return 1
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = ctx.home / "backups" / f"open-ai-chip-{ts}"
    manifest = {"profile": ctx.profile, "created_at": ts, "profile_existed": ctx.pdir.is_dir(),
                "home": str(ctx.home), "files": [], "repo_files": [], "jobs": [JOB_TEST, JOB_FULL], "project": PROJECT}
    for p in list(files):
        existed = p.exists()
        manifest["files"].append({"rel": str(p.relative_to(ctx.home)), "existed": existed})
        if existed:
            dst = bdir / p.relative_to(ctx.home)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
    for p in repo_files:
        manifest["repo_files"].append({"rel": str(p.relative_to(REPO)), "existed": p.exists()})
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nBackup written: {shown(ctx, bdir)}")

    if not ctx.pdir.is_dir():
        ctx.hermes("profile", "create", ctx.profile, "--no-alias", "--no-skills", "--description",
                   "open-ai-chip: read-only chip agent, local qwen, gated MCP tools", timeout=900)
        print("profile created")
        files, repo_files = plan_files(ctx)   # re-plan against the real skeleton
    for p, (text, mode) in {**files, **repo_files}.items():
        write_file(p, text, mode)
        print(f"wrote {shown(ctx, p)}")
    install_plugin(ctx)
    cron_out = existing_names(ctx, ["cron", "list"])
    for name, sched, script in ((JOB_TEST, "30 2 * * *", "chip_nightly_test.sh"),
                                (JOB_FULL, "0 3 * * 0", "chip_weekly_test_full.sh")):
        if name in cron_out:
            print(f"cron job exists: {name}")
            continue
        ctx.hermes("-p", ctx.profile, "cron", "create", sched, "--name", name, "--no-agent", "--script", script,
                   "--deliver", "local", "--workdir", str(REPO))
        print(f"cron job created: {name}")
    if PROJECT in existing_names(ctx, ["project", "list"]):
        print(f"project exists: {PROJECT}")
    else:
        ctx.hermes("-p", ctx.profile, "project", "create", PROJECT, str(REPO), "--primary", str(REPO))
        print(f"project created: {PROJECT}")
    print("\nDone. Next: hermes -p %s status | hermes -p %s hooks doctor | hermes -p %s approvals test -- \"cf push\"" %
          (ctx.profile, ctx.profile, ctx.profile))
    print("Cron fires only while a Hermes scheduler runs (gateway process); see docs/HERMES_AGENT_INTEGRATION.md.")
    return 0


def latest_backup(ctx):
    root = ctx.home / "backups"
    cands = sorted(root.glob("open-ai-chip-*/manifest.json")) if root.is_dir() else []
    cands = [c for c in cands if json.loads(c.read_text()).get("profile") == ctx.profile]
    return cands[-1].parent if cands else None


def do_uninstall(ctx, apply):
    b = latest_backup(ctx)
    print(f"Hermes home : {ctx.home}\nProfile     : {ctx.profile}\nMode        : {'APPLY' if apply else 'DRY RUN (add --apply)'}")
    if b is None:
        print("No open-ai-chip backup found; nothing recorded to restore.")
        return 1
    m = json.loads((b / "manifest.json").read_text())
    print(f"Backup      : {shown(ctx, b)}")
    plan = []
    for name in (JOB_TEST, JOB_FULL):
        plan.append(f"{ctx.prefix()}hermes -p {ctx.profile} cron remove '{name}'   (by id found with `cron list`)")
    if not m["profile_existed"]:
        plan.append(f"{ctx.prefix()}hermes profile delete {ctx.profile} --yes   (profile was created by the setup script)")
    else:
        plan.append(f"{ctx.prefix()}hermes -p {ctx.profile} project archive {PROJECT}")
        for f in m["files"]:
            plan.append(("restore " if f["existed"] else "remove  ") + shown(ctx, ctx.home / f["rel"]))
    for f in m["repo_files"]:
        plan.append(("restore " if f["existed"] else "remove  ") + "<repo>/" + f["rel"])
    plan.append("remove  " + shown(ctx, plugin_link(ctx)) + "   (plugin link)")
    print("Plan:")
    for s in plan:
        print("  " + s)
    if not apply:
        return 0
    # cron jobs: find ids by name ("<id> [status]" line, then a "Name:" line)
    out = existing_names(ctx, ["cron", "list"])
    cur = None
    for line in out.splitlines():
        mid = re.match(r"^\s*([0-9a-f]{8,})\s+\[", line)
        if mid:
            cur = mid.group(1)
        mn = re.match(r"^\s*Name:\s*(.+?)\s*$", line)
        if mn and cur and mn.group(1) in (JOB_TEST, JOB_FULL):
            ctx.hermes("-p", ctx.profile, "cron", "remove", cur, check=False)
            print(f"cron job removed: {mn.group(1)}")
    link = plugin_link(ctx)
    if link.is_symlink():
        link.unlink()
        print("plugin link removed")
    if not m["profile_existed"]:
        ctx.hermes("profile", "delete", ctx.profile, "--yes", check=False)
        print("profile deleted")
    else:
        ctx.hermes("-p", ctx.profile, "project", "archive", PROJECT, check=False)
        for f in m["files"]:
            tgt = ctx.home / f["rel"]
            if f["existed"]:
                shutil.copy2(b / f["rel"], tgt)
                print("restored", shown(ctx, tgt))
            elif tgt.exists():
                tgt.unlink()
                print("removed", shown(ctx, tgt))
    for f in m["repo_files"]:
        tgt = REPO / f["rel"]
        if not f["existed"] and tgt.exists():
            tgt.unlink()
            print("removed <repo>/" + f["rel"])
    print("Uninstall done. The backup directory is kept.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run, print the diff only)")
    ap.add_argument("--uninstall", action="store_true", help="undo the last apply (needs --apply to act)")
    ap.add_argument("--profile", default="chip")
    ap.add_argument("--hermes-home", help="alternative Hermes home (default $HERMES_HOME or ~/.hermes)")
    ap.add_argument("--demo-tools", action="store_true",
                    help="also expose log_digest, param_info, propose_change, whatif_run, whatif_result (needed by demos 3 and 6; not in the measured core set)")
    ap.add_argument("--grafana", action="store_true",
                    help="also add the local Grafana MCP server (read-only; needs scripts/grafana/setup_grafana.sh first, docs/GRAFANA.md)")
    a = ap.parse_args()
    global DEMO_TOOLS, GRAFANA
    DEMO_TOOLS = a.demo_tools
    GRAFANA = a.grafana
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", a.profile) or a.profile == "default":
        print("profile must be a lowercase name other than 'default'", file=sys.stderr)
        return 2
    ctx = Ctx(a)
    return do_uninstall(ctx, a.apply) if a.uninstall else do_install(ctx, a.apply)


if __name__ == "__main__":
    sys.exit(main())
