#!/usr/bin/env python3
# Docs: docs/HERMES_AGENT_INTEGRATION.md
"""Hermes Agent pre_tool_call shell hook for the `chip` profile of open-ai-chip.

Protocol (Hermes v0.21.5, agent/shell_hooks.py, docs user-guide/features/hooks.md):
  stdin : one JSON object {hook_event_name, tool_name, tool_input, session_id, cwd, profile, extra}
  stdout: {} to allow
          {"action": "block",   "message": "..."}   to block
          {"action": "approve", "message": "..."}   to escalate to the human approval gate
  Exit 0 always for a decision. Exit 2 (stderr = message) also blocks. Any crash is treated as a block by
  Hermes because the config sets fail_closed: true, and this script additionally catches its own errors and
  answers "block" so a bug never silently allows.

Policy (CLAUDE.md HARD RULES; Hermes sessions may read and run gated tools but never edit repo files):
  - write_file / patch / skill_manage / execute_code / delegate_task ... : blocked
  - terminal (normally not enabled): cf, git push, rm -rf, docker run/exec, repo mutation, weakened signoff
    keys, frozen designs and direct physical make targets are blocked
  - MCP tools mcp_chip_*: physical flows, what-if runs and ask_claude escalate to approval; claude_task blocked
  - confirm guard (scripts/hermes/hooks/confirm_guard.py): a gated call (run_make, confirm_run, whatif_run, run_experiment, ask_claude ...)
    carrying a confirm_id is blocked unless the user's latest message in that session says "yes, run <id>"; checked first, fail closed
  - reads of credential files (.env, auth.json, pairing/, .ssh) are blocked
Every decision is appended to build/agent/hermes_hook.log (one JSON line, never file contents).
Stdlib only, Python 3.8+. Repo root: env CHIP_REPO or three levels above this file.
"""
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

REPO = Path(os.environ.get("CHIP_REPO") or Path(__file__).resolve().parents[3]).resolve()
LOG = Path(os.environ.get("CHIP_HOOK_LOG") or REPO / "build" / "agent" / "hermes_hook.log")

# Make targets: physical flows (Docker, minutes) must go through the gated MCP run_make tool.
PHYSICAL_TARGETS = {
    "gds", "flow-all", "flow", "check", "gl", "gl-final", "views", "macro-views", "wrapper", "collect",
    "test-full", "soc-sim", "soc-kv", "adapter-test", "caravel-rtl", "caravel-gl", "caravel-fullgl",
    "caravel-sdf-wrapper", "precheck", "all-designs", "tiny",
}
SAFE_TARGETS = {"help", "doctor", "test", "check-generated", "model-check", "simulate", "check-frozen",
                "designs", "view", "code-map"}
WRITE_TARGETS = {"generate", "table", "results", "clean", "freeze", "demo", "hermes", "hermes-stop",
                 "master-prompt"}
PHYSICAL_TOOLS = {"whatif_run", "whatif_sweep", "run_experiment"}
NEVER_TOOLS = {"write_file", "patch", "skill_manage", "execute_code", "delegate_task", "computer_use",
               "cronjob_manage", "manage_connections", "send_message", "claude_task", "memory"}
ASK_CLAUDE_DECISION = "allow"     # the bridge already has a two-step confirm gate (user replies "yes, run <id>"); use "approve" for a second, UI-level gate

WEAKEN_RES = [
    (re.compile(r"\b(CLOCK_PERIOD|MAX_TRANSITION_CONSTRAINT)\b(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?[0-9.]"),
     "sets CLOCK_PERIOD or MAX_TRANSITION_CONSTRAINT (HARD RULE: never loosen them)"),
    (re.compile(r"\bDISABLE_LVS\b", re.I), "mentions DISABLE_LVS (HARD RULE: never)"),
    (re.compile(r"\bSYNTH_STRATEGY\b(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?DELAY", re.I), "SYNTH_STRATEGY DELAY (HARD RULE: never)"),
    (re.compile(r"\bERROR_ON_SYNTH_CHECKS\b(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?(false|0|no)\b", re.I),
     "ERROR_ON_SYNTH_CHECKS false (HARD RULE)"),
    (re.compile(r"fixed_dont_change"), "touches wrapper fixed_dont_change geometry (HARD RULE)"),
]
SECRET_PATH_RE = re.compile(
    r"(^|/)(\.env(\..*)?|auth\.json|auth\.lock|pairing|\.ssh|\.netrc|\.aws|\.gnupg|id_rsa[^/]*|id_ed25519[^/]*"
    r"|\.cf|credentials(\.json)?|shell-hooks-allowlist\.json)(/|$)")
PATH_KEYS = ("path", "file_path", "filepath", "file", "directory", "dir", "root", "cwd", "target_path")
MUTATORS = {"rm", "mv", "cp", "tee", "touch", "mkdir", "rmdir", "truncate", "ln", "install", "dd", "rsync",
            "chmod", "chown", "patch", "unlink", "shred"}
GIT_MUTATING = {"add", "commit", "apply", "checkout", "reset", "clean", "stash", "restore", "rebase", "merge",
                "cherry-pick", "revert", "rm", "mv", "am", "pull", "switch", "tag", "branch", "push", "remote"}
DOCKER_BLOCKED = {"run", "exec", "compose", "build", "rm", "rmi", "system", "volume", "network", "kill", "stop",
                  "cp", "create", "start", "restart", "commit", "load", "import", "prune", "container", "image"}
WRAPPERS = {"sudo", "env", "time", "command", "nohup", "nice", "exec", "xargs", "stdbuf", "timeout"}


def out(decision, message=""):
    if decision == "allow":
        print("{}")
    else:
        print(json.dumps({"action": decision, "message": message}))


def log(payload, tool, decision, reason, summary):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "session": str(payload.get("session_id") or "")[:40],
               "profile": payload.get("profile"), "tool": tool, "decision": decision, "reason": reason,
               "summary": summary[:300]}
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def _frozen_fn():
    try:
        sys.path.insert(0, str(REPO / "scripts" / "flow"))
        import frozen  # type: ignore
        return getattr(frozen, "is_frozen", None)
    except Exception:
        return None
    finally:
        try:
            sys.path.remove(str(REPO / "scripts" / "flow"))
        except ValueError:
            pass


def is_frozen(path):
    fn = _frozen_fn()
    if fn is None:
        return False
    try:
        p = Path(path)
        rel = p.relative_to(REPO) if p.is_absolute() and REPO in p.parents else p
        for arg in (str(rel), rel):
            try:
                if fn(arg):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def design_frozen(name):
    """True when designs/<name> is frozen (scripts/flow/frozen.py): a flow would rewrite its committed evidence."""
    if not name or re.fullmatch(r"[A-Za-z0-9_]+", str(name)) is None:
        return False
    try:
        sys.path.insert(0, str(REPO / "scripts" / "flow"))
        import frozen  # type: ignore
        if hasattr(frozen, "frozen_designs") and str(name) in frozen.frozen_designs():
            return True
    except Exception:
        pass
    finally:
        try:
            sys.path.remove(str(REPO / "scripts" / "flow"))
        except ValueError:
            pass
    return is_frozen(f"designs/{name}/config.json")


def resolve(tok, cwd):
    p = Path(os.path.expanduser(tok))
    if not p.is_absolute():
        p = Path(cwd) / p
    return Path(os.path.normpath(str(p)))


def classify_path(p):
    """Return a short label for a path: repo-build, repo, silicon, other."""
    try:
        rp = p.resolve()
    except OSError:
        rp = p
    sib = REPO.parent / "open-ai-silicon"
    if sib == rp or sib in rp.parents:
        return "silicon"
    if REPO == rp or REPO in rp.parents:
        if (REPO / "build") in rp.parents or rp == REPO / "build":
            return "repo-build"
        return "repo"
    return "other"


def weaken_reason(text, reading_ok=False):
    for rx, why in WEAKEN_RES:
        if reading_ok and 'fixed_dont_change' in rx.pattern:
            continue
        if rx.search(text):
            return why
    return None


def secret_path(tok):
    s = str(tok).replace("\\", "/")
    return bool(SECRET_PATH_RE.search(s))


def split_segments(cmd):
    return [s for s in re.split(r"\n|;|&&|\|\||\||&(?!>)", cmd) if s.strip()]


def argv_of(seg):
    try:
        argv = shlex.split(seg, posix=True)
    except ValueError:
        argv = seg.split()
    while argv and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[0]):
        argv = argv[1:]
    while argv and os.path.basename(argv[0]) in WRAPPERS:
        argv = argv[1:]
        while argv and (argv[0].startswith("-") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[0])
                        or re.fullmatch(r"[0-9.]+[smhd]?", argv[0])):
            argv = argv[1:]
    return argv


def check_make(argv, cwd):
    targets = [a for a in argv[1:] if not a.startswith("-") and "=" not in a]
    design = None
    for a in argv[1:]:
        if a.startswith("DESIGN="):
            design = a.split("=", 1)[1]
    if not targets:
        return "block", "bare `make` builds the default target; use the MCP run_make tool"
    for t in targets:
        if t in PHYSICAL_TARGETS:
            if design_frozen(design):
                return "block", f"design {design} is frozen (designs/FROZEN.json); no flow may run on it"
            return "block", (f"`make {t}` is a physical or long flow: call the gated MCP tool run_make "
                             f"(target={t}) instead; it asks for approval and holds the one-flow lock")
        if t in WRITE_TARGETS:
            return "block", f"`make {t}` writes repo files; Hermes sessions may not edit the repo"
        if t not in SAFE_TARGETS:
            return "approve", f"`make {t}` is not on the read-only list; approve to run it"
    return "allow", ""


def check_segment(seg, cwd):
    argv = argv_of(seg)
    if not argv:
        return "allow", ""
    prog = os.path.basename(argv[0])
    args = argv[1:]
    # shell -c "..." : check the inner script
    if prog in ("bash", "sh", "zsh") and "-c" in args:
        i = args.index("-c")
        if i + 1 < len(args):
            return check_command(args[i + 1], cwd)
    if prog == "cf":
        return "block", "cf (ChipFoundry CLI) is owner-only: login/init/push/submit/confirm are never run by an agent"
    if prog == "git":
        sub = next((a for a in args if not a.startswith("-")), "")
        if sub == "push":
            return "block", "git push is owner-only"
        if sub in GIT_MUTATING:
            return "block", f"git {sub} changes the repo or its remotes; owner-only"
    if prog == "gh":
        if re.search(r"visibility|--public|\bpublic\b|repo\s+(edit|create|delete)", " ".join(args)):
            return "block", "changing repository visibility or creating/deleting repos is owner-only"
    if prog == "rm" and any(re.fullmatch(r"-[a-zA-Z]*[rRf][a-zA-Z]*", a) for a in args):
        return "block", "rm -r / rm -f is not allowed"
    if prog == "docker":
        sub = next((a for a in args if not a.startswith("-")), "")
        if sub in DOCKER_BLOCKED:
            return "block", f"docker {sub} is only allowed inside the repo tools (make via run_make)"
    if prog == "make":
        d, m = check_make(argv, cwd)
        if d != "allow":
            return d, m
    if prog in ("bash", "sh", "python3", "python") and args:
        script = args[0]
        if re.search(r"scripts/flow/(run_capped|whatif_flow|gl_sim)\.sh", script) or script.endswith("run_all.sh"):
            return "block", "physical flow scripts must run through the gated MCP tools"
    # mutation of repo files
    mutates = prog in MUTATORS or (prog in ("sed", "perl") and any(a.startswith("-i") for a in args))
    if mutates:
        for a in args:
            if a.startswith("-") or not a:
                continue
            c = classify_path(resolve(a, cwd))
            if c in ("repo", "silicon"):
                return "block", f"{prog} would modify {'../open-ai-silicon (read-only reference)' if c == 'silicon' else 'repo files'}; Hermes sessions may not edit the repo"
    for a in args:
        if secret_path(a):
            return "block", "credential or secret path; never read by an agent"
        if not a.startswith("-") and ("/" in a or a in ("designs", "shared", "model")) and is_frozen(resolve(a, cwd)) and mutates:
            return "block", f"{a} is frozen (designs/FROZEN.json)"
    return "allow", ""


def check_command(cmd, cwd):
    why = weaken_reason(cmd, reading_ok=True)
    if why:
        return "block", f"command {why}"
    if re.search(r"\.\./open-ai-silicon", cmd) and re.search(r"(>|\btee\b|\bsed\s+-i|\brm\b|\bmv\b|\bcp\b)", cmd):
        return "block", "../open-ai-silicon is read-only reference material"
    # redirections into the repo
    for m in re.finditer(r"(?<![0-9&<])>>?\s*([^\s;&|<>]+)", cmd):
        tgt = m.group(1).strip("'\"")
        if tgt in ("/dev/null", "&1", "&2"):
            continue
        if classify_path(resolve(tgt, cwd)) in ("repo", "silicon"):
            return "block", f"redirection into {tgt} would edit repo files; Hermes sessions may not edit the repo"
    worst = ("allow", "")
    for seg in split_segments(cmd):
        d, m = check_segment(seg, cwd)
        if d == "block":
            return d, m
        if d == "approve" and worst[0] == "allow":
            worst = (d, m)
    return worst


def get_paths(args):
    vals = []
    if isinstance(args, dict):
        for k in PATH_KEYS:
            v = args.get(k)
            if isinstance(v, str):
                vals.append(v)
            elif isinstance(v, list):
                vals.extend(x for x in v if isinstance(x, str))
    return vals


def confirm_guard_message(payload):
    """Block message from confirm_guard.check (a gated call with a confirm_id needs the USER's 'yes, run <id>'); fail closed."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import confirm_guard  # type: ignore
        return confirm_guard.check(payload)
    except Exception as exc:  # noqa: BLE001  (a broken guard must never allow)
        return f"confirm guard unavailable ({exc}); ask the user to confirm in a new message"
    finally:
        try:
            sys.path.remove(str(Path(__file__).resolve().parent))
        except ValueError:
            pass


GRAFANA_READ_TOOLS = {"search_dashboards", "get_dashboard_summary", "get_dashboard_panel_queries", "list_datasources"}


def decide(payload):
    tool = str(payload.get("tool_name") or "")
    msg = confirm_guard_message(payload)
    if msg:
        return tool, "block", msg, "confirm guard"
    args = payload.get("tool_input")
    if not isinstance(args, dict):
        args = {}
    cwd = payload.get("cwd") or str(REPO)
    summary = tool
    m = re.match(r"^mcp_+chip(?:_eda)?_+(.+)$", tool)   # Hermes names MCP tools mcp__chip__<tool> (v0.21.5 measured); older docs show mcp_chip_<tool>
    short = m.group(1) if m else tool

    g = re.match(r"^mcp_+grafana_+(.+)$", tool)   # local Grafana MCP (docs/GRAFANA.md): only the four read tools, never a write
    if g:
        if g.group(1) in GRAFANA_READ_TOOLS:
            return tool, "allow", "", tool
        return tool, "block", "only the Grafana read tools are allowed (search, dashboard summary, panel queries, datasources)", tool

    for p in get_paths(args):
        if secret_path(p):
            return tool, "block", "credential or secret path; never read by an agent", p

    if tool in ("write_file", "patch") or short in NEVER_TOOLS or tool in NEVER_TOOLS:
        paths = get_paths(args)
        blob = json.dumps(args)[:20000]
        extra = ""
        for p in paths:
            rp = resolve(p, cwd)
            if is_frozen(rp):
                extra = f" ({p} is also frozen, designs/FROZEN.json)"
            elif classify_path(rp) == "silicon":
                extra = " (../open-ai-silicon is read-only reference material)"
        why = weaken_reason(blob)
        if why:
            extra += f"; the content {why}"
        if tool in ("write_file", "patch"):
            return tool, "block", ("Hermes sessions may not edit repo files; ask the owner or use ask_claude to "
                                   "request a change" + extra), ",".join(paths) or tool
        if short == "ask_claude":
            pass
        else:
            return tool, "block", f"{tool} is not available in the chip profile (read and gated runs only)" + extra, tool

    if tool in ("terminal", "process_manage"):
        cmd = str(args.get("command") or args.get("cmd") or "")
        d, msg = check_command(cmd, cwd)
        return tool, d, msg, cmd

    if short == "ask_claude":
        msg = "ask_claude sends the question (and any text you include) to Claude, a cloud model; approve to proceed"
        return tool, ASK_CLAUDE_DECISION, msg if ASK_CLAUDE_DECISION != "allow" else "", str(args.get("instructions") or args.get("question") or "")[:200]

    if short == "run_make":
        target = str(args.get("target") or "")
        design = args.get("design")
        blob = json.dumps(args)
        why = weaken_reason(blob)
        if why:
            return tool, "block", f"arguments {why}", target
        if target in PHYSICAL_TARGETS:
            if design_frozen(design):
                return tool, "block", f"design {design} is frozen (designs/FROZEN.json); no flow may run on it", f"{target} {design}"
            return tool, "approve", f"physical flow: make {target}" + (f" DESIGN={design}" if design else "") + " (one flow at a time)", f"{target} {design or ''}".strip()
        if target in SAFE_TARGETS:
            return tool, "allow", "", f"{target} {design or ''}".strip()
        return tool, "block", f"make target '{target}' is not on the agent allow-list", f"{target} {design or ''}".strip()

    if short in PHYSICAL_TOOLS:
        blob = json.dumps(args)
        why = weaken_reason(blob)
        if why and not re.search(r"CLOCK_PERIOD|MAX_TRANSITION", why):
            return tool, "block", f"arguments {why}", short
        design = args.get("design")  # what-if runs on a copy under build/whatif, so a frozen design is fine
        return tool, "approve", f"{short} starts a physical flow on a copy; approve to run (one flow at a time)", f"{short} {design or ''}".strip()

    if short in ("propose_change", "param_info"):
        why = weaken_reason(json.dumps(args))
        if why and not re.search(r"CLOCK_PERIOD|MAX_TRANSITION", why):
            return tool, "block", f"arguments {why}", short

    for p in get_paths(args):
        summary = p
    return tool, "allow", "", summary


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
    except Exception as exc:
        out("block", f"chip hook could not parse its input: {exc}")
        return 0
    if payload.get("hook_event_name") not in (None, "pre_tool_call"):
        print("{}")
        return 0
    try:
        tool, decision, reason, summary = decide(payload)
    except Exception as exc:  # fail closed
        tool, decision, reason, summary = str(payload.get("tool_name")), "block", f"chip hook error: {exc}", ""
    log(payload, tool, decision, reason, summary)
    out(decision, reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
