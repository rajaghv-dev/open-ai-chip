# hermes_desktop

Open WebUI + the chip tool server + a Mac window for Hermes 3 8B. Full guide: [docs/HERMES_DESKTOP.md](../../docs/HERMES_DESKTOP.md).

| File | Purpose |
|---|---|
| `setup_webui.sh` | one-time: Open WebUI into `build/webui/venv` (python3.12), pywebview into `build/agent/venv` |
| `start.sh` / `stop.sh` | start Ollama (if needed), tool server, Open WebUI, preset; stop only those (pid files in `build/webui/pids`) |
| `tool_server_connection.json` | the tool server registration passed as `TOOL_SERVER_CONNECTIONS` |
| `preset.json`, `system_prompt.txt`, `install_preset.py` | the "Hermes chip agent" model preset (API import) |
| `tools_prompt.txt` | tool-routing prompt for Open WebUI's prompt-based function calling |
| `desktop/make_app.sh`, `desktop/app.py` | build `build/desktop/Hermes Chip Agent.app` (`--install` copies to ~/Applications) |
| `tool_server/` | the FastAPI tool server (separate owner) |

Quick start: `bash examples/hermes_desktop/setup_webui.sh && bash examples/hermes_desktop/start.sh`, then http://127.0.0.1:8080.
