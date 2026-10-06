# Read-only EDA tools for a local LLM agent

`tools/eda_tools.py` lets an agent inspect this repo's chip results and layouts. Everything is
read-only; the only writes are PNG renders under `build/agent/renders/`. Design names are validated
against `designs/*/config.json`.

Contract: `TOOLS` (OpenAI/Ollama tool schemas) and `call(name, args) -> dict` (never raises; returns
`{"error": "..."}` on bad input).

## Setup (already done on this machine)

```
/opt/homebrew/bin/python3.12 -m venv build/agent/venv      # build/ is git-ignored
build/agent/venv/bin/pip install klayout mcp pytest
```

Installed versions: Python 3.12.13 (Homebrew), klayout 0.30.12 (macOS arm64 wheel), mcp 2.3.0
(note: mcp 2.x renamed FastMCP to MCPServer; `mcp_server.py` uses the 2.x low-level `Server(on_list_tools=..., on_call_tool=...)`),
pytest 9.1.1. Layer colors come from `$PDK_ROOT/sky130A/libs.tech/klayout/tech/sky130A.lyp`
(`PDK_ROOT` defaults to `~/.volare`).

## Tools

| tool | purpose |
|---|---|
| `list_designs()` | designs, one-line description, hardened?, DESIGN_NAME |
| `read_metrics(design, keys, pattern)` | metrics.json values by exact key or substring, with meanings |
| `compare_designs(metric, designs)` | one metric across designs, sorted, min/max |
| `layout_summary(design)` | GDS: top cell, die bbox um, cells, shapes, top-10 layers, macro instances |
| `layer_stats(design, layer)` | shapes, merged area um^2, bbox for met1..met5, li1, poly, ... or `L/D` |
| `find_pins(design, pattern)` | regex over GDS text labels (LEF fallback), max 50 |
| `render_png(design, out, width_px)` | KLayout headless PNG into `build/agent/renders/<design>.png` (falls back to `output/layout.png`, flagged) |
| `signoff_summary(design)` | DRC/LVS/XOR/antenna, worst slack + corner, slew/cap/fanout, `check_signoff.py` verdict (120 s timeout; sets `DOCKER_HOST` to the colima socket if present) |
| `classify_slew(design, corner)` | wraps `.claude/skills/harden-design/classify_slew.py` (needs a local `designs/<d>/runs/RUN_*`) |
| `precheck_summary()` | newest `build/precheck/results_<date>_<time>/summary.tsv` -> check: PASS/FAIL |

GDS-based tools need `build/results/<d>/<DESIGN_NAME>.gds` (local, git-ignored). Quick CLI:
`build/agent/venv/bin/python tools/eda_tools.py layer_stats '{"design":"tiny_ai_core","layer":"met4"}'`.

## Tests

`build/agent/venv/bin/python -m pytest tests/tools -q` (`tests/tools/test_eda_tools.py`, 14 test functions; GDS tests skip with a reason when the GDS is missing).

## MCP server

`tools/mcp_server.py` is a stdio MCP server exposing the same ten tools.

Claude Code:
```
(`<repo>` is the absolute path of your clone of this repository.)

claude mcp add open-ai-chip-eda -- <repo>/build/agent/venv/bin/python <repo>/tools/mcp_server.py
```
Goose (`~/.config/goose/config.yaml`, or `goose configure` -> Add Extension -> Command-line Extension):
```
extensions:
  open-ai-chip-eda:
    type: stdio
    name: open-ai-chip-eda
    cmd: <repo>/build/agent/venv/bin/python
    args: [<repo>/tools/mcp_server.py]
    enabled: true
    timeout: 300
```
Any other MCP client: command `build/agent/venv/bin/python`, args `tools/mcp_server.py`, stdio transport.

## Hermes local agent

Offline agent: a local Hermes 3 8B model (Ollama) answers questions through `eda_tools` only (read-only).
Run `build/agent/venv/bin/python tools/hermes_agent.py "question"` (add `--mode native` for the Ollama tools field; the default
is the Hermes prompt format, which honours the system prompt). Eval: `python3 tools/eval/ground_truth.py` then
`build/agent/venv/bin/python tools/eval/run_eval.py --mode prompt`. Details, scores and limits: `docs/HERMES_AGENT.md` (prompt mode 13/15, native 6/15). The loop-and-harness example (baseline 13/15, up to 15/15 with a deterministic `pick_extreme` tool) is in `examples/hermes_harness/README.md`; a minimal KLayout demo is in `examples/hermes_klayout_demo/README.md`.
