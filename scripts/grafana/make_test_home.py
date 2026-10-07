#!/usr/bin/env python3
"""Create an ISOLATED Hermes home (default build/hermes_grafana_home) with local Ollama qwen3.5-64k:9b, the chip MCP
bridge (private tool server port) and the read-only Grafana MCP server. Never touches ~/.hermes. Then:
  HERMES_HOME=build/hermes_grafana_home hermes mcp test grafana
  HERMES_HOME=build/hermes_grafana_home hermes -z "List the Grafana dashboards"
Docs: docs/GRAFANA.md.
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
home = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(REPO, "build", "hermes_grafana_home")
if os.path.realpath(home) == os.path.realpath(os.path.expanduser("~/.hermes")) or not home.startswith(REPO + os.sep):
    sys.exit("refusing: the test home must live under the repo build/ directory")
os.makedirs(home, exist_ok=True)

gm = json.load(open(os.path.join(REPO, "scripts", "hermes", "grafana_mcp.json")))["mcp_servers"]["grafana"]
gm = json.loads(json.dumps(gm).replace("__REPO__", REPO))
cur = json.load(open(os.path.join(REPO, "tools", "hermes_tools.json")))
core = [k for k in (cur.get("core") or cur.get("include", {})) if not k.startswith("//")]
core += [k for k in cur.get("aliases", {}) if not k.startswith("//") and k not in core]
chip = {"command": os.path.join(REPO, "build", "agent", "venv", "bin", "python"),
        "args": [os.path.join(REPO, "tools", "hermes_mcp_bridge.py")], "timeout": 300, "connect_timeout": 90,
        "env": {"CHIP_TOOLS_PORT": "8831"}, "tools": {"prompts": False, "resources": False, "include": core}}
cfg = {
    "_config_version": 49,   # same schema generation as the installed Hermes (avoids the "predates version 12" notice)
    "model": {"default": "qwen3.5-64k:9b", "provider": "custom", "base_url": "http://127.0.0.1:11434/v1",
              "context_length": 65536, "ollama_num_ctx": 65536},
    "agent": {"max_turns": 20, "disabled_toolsets": [
        "terminal", "code_execution", "computer_use", "delegation", "kanban", "image_gen", "video_gen", "memory",
        "search", "web", "browser", "vision", "spotify", "x_search", "todo", "cronjob", "tts", "file"]},
    "tools": {"tool_search": {"enabled": "off"}},
    "platform_toolsets": {"cli": ["skills"]},
    "mcp_servers": {"grafana": gm, "chip": chip},
    "approvals": {"mode": "manual", "cron_mode": "deny", "single_query_mode": "deny", "unattended_mode": "deny",
                  "deny": ["cf", "cf *", "git push*"]},
    "memory": {"memory_enabled": False, "user_profile_enabled": False},
}
with open(os.path.join(home, "config.yaml"), "w") as f:
    json.dump(cfg, f, indent=2)   # JSON is valid YAML
    f.write("\n")
print("wrote", os.path.join(home, "config.yaml"))
