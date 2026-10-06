# hermes_desktop

Open WebUI + the chip tool server + a Mac window for Hermes 3 8B. Full guide: [docs/HERMES_DESKTOP.md](../../docs/HERMES_DESKTOP.md).

| File | Purpose |
|---|---|
| `setup_webui.sh` | one-time: Open WebUI into `build/webui/venv` (python3.12), pywebview into `build/agent/venv` |
| `start.sh` / `stop.sh` | start Ollama (if needed), tool server, Open WebUI, preset; stop only those (pid files in `build/webui/pids`) |
| `tool_server_connection.json` | the tool server registration passed as `TOOL_SERVER_CONNECTIONS` |
| `preset.json`, `system_prompt.txt`, `install_preset.py` | the "Hermes chip agent" model preset (API import); system prompt = generated `tools/prompts/master_prompt.txt` + `system_prompt.txt` (tool-use details) |
| `prompt_suggestions.json` | Open WebUI default prompt suggestions (env `DEFAULT_PROMPT_SUGGESTIONS`, set by `start.sh`) |
| `tools_prompt.txt` | tool-routing prompt for Open WebUI's prompt-based function calling |
| `desktop/make_app.sh`, `desktop/app.py` | build `build/desktop/Hermes Chip Agent.app` (`--install` copies to ~/Applications) |
| `demos.py`, `demo_defs.py`, `demos/` | the numbered demos (`python3 examples/hermes_desktop/demos.py` = menu, `... 2` or `... kv` runs one) and their transcripts |
| `tool_server/` | the FastAPI tool server (separate owner) |

Quick start: `bash examples/hermes_desktop/setup_webui.sh && bash examples/hermes_desktop/start.sh`, then http://127.0.0.1:8080.

## Master prompt check

Measured through `POST /api/chat/completions` (2026-10-06, hermes3:8b; answers and tools in the table below): repo overview, the KV-cache
family question, the off-topic redirect and "unknown" for tapeout yield worked. Known weakness: the 8B routing call still starts
`run_make` for "How do I run the full flow for kv_attn_n8?" (the final answer text gives the right command `make flow-all DESIGN=kv_attn_n8`, but a job
was also started; here it was a reuse of the existing run, no Docker, and in other tries `make test-full`, a check-only suite). Treat
run_make from chat questions as a risk: `run_make` is allow-listed and physical flows are one at a time, but a flow with no reusable
run would start. Tighten it (for example only expose `run_make` to explicit "run ..." sessions) before leaving the UI unattended.

| Question | Tools called | Answer |
|---|---|---|
| What is this repo about? | none | repo summary from the prompt (25 designs, sky130A, LibreLane 3.0.2, 25 ns / 40 MHz, signoff-clean) |
| Which designs are in the KV-cache family and how big is the biggest? | list_designs, read_metrics | the 5 kv_attn designs; biggest kv_attn_n16, 4169 standard cells (`designs/kv_attn_n16/output/metrics.json`) |
| How do I run the full flow for kv_attn_n8? | run_make (unwanted) | `make flow-all DESIGN=kv_attn_n8` |
| write me a poem about cats | none | polite redirect to the repo scope |
| What is the tapeout yield? | none | unknown, no tool or file has it |
