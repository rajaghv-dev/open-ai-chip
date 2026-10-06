# Hermes tool server

A local FastAPI server that gives the Hermes agent (Open WebUI in the browser, or the Mac desktop app) this repo's
tools. Every tool is `POST /<name>` with a JSON body; the OpenAPI `operationId` equals the tool name, so Open WebUI
turns each operation into a model tool.

Run (127.0.0.1 only, port from `CHIP_TOOLS_PORT`, default 8770):

```
build/agent/venv/bin/pip install fastapi uvicorn httpx      # once
build/agent/venv/bin/python examples/hermes_desktop/tool_server/tool_server.py
```

Open WebUI: Settings, Tools, add the tool server `http://127.0.0.1:8770` (spec at `/openapi.json`). CORS allows
`http://127.0.0.1:8080` and `http://localhost:8080` only.

## Endpoints

| tool | purpose |
|---|---|
| `list_designs`, `read_metrics`, `compare_designs`, `layout_summary`, `layer_stats`, `find_pins`, `render_png`, `signoff_summary`, `classify_slew`, `precheck_summary` | the 10 read-only EDA tools of `tools/eda_tools.py` (same names and arguments; see `tools/README.md`) |
| `search_docs {query, k}` | BM25 search over the repo markdown (`examples/hermes_rag`, v2) |
| `klayout_view {design, layers?, zoom?, demo_markers?, width?, height?}` | offscreen KLayout render; returns `{png_url, view_bbox_um, visible_layers, markdown}`; the PNG is served at `GET /img/<name>` from `build/agent/klayout_gui/` |
| `list_skills`, `get_skill {name}` | the project skills in `.claude/skills/<name>/SKILL.md` |
| `run_make {target, design?}` | allow-listed `make` target as a background job; returns `{job_id}` |
| `job_status {job_id}`, `job_list`, `job_cancel {job_id}` | state `running/done/failed`, rc, seconds, last 60 log lines (logs in `build/agent/jobs/<id>.log`) |
| `claude_task {instructions, skill?, design?, max_turns?}` | headless Claude Code CLI as a job, edit tools denied; poll with `job_status` |
| `health` | versions, Ollama, Docker (colima socket), Claude CLI path, running jobs |

`run_make` targets: `doctor test simulate check gl gl-final gds flow-all views collect view soc-sim soc-kv
adapter-test model-check check-generated test-full caravel-rtl caravel-gl`. Anything else (generate, table, clean,
precheck, all-designs, ...) is refused. `design` must be in the Makefile `ALL_DESIGNS` list.

## Safety model

- Binds 127.0.0.1 only; CORS limited to the two Open WebUI origins. No authentication, so do not expose the port.
- Tools never take paths or shell text: design names are checked against an allow-list, make targets against a
  fixed list, image names against `^[A-Za-z0-9_][A-Za-z0-9_.-]*\.png$` plus a realpath check under
  `build/agent/klayout_gui/`. Commands run as argument lists (no shell).
- Jobs: at most 2 running in total; `gds flow-all views collect test-full` hold a global lock (one physical flow at
  a time, each flow also caps itself with `FLOW_TIMEOUT`); one `claude_task` at a time; hard timeouts
  (make 3 h, claude 1 h); `DOCKER_HOST=unix://$HOME/.colima/osl/docker.sock` is set for the child. Cancel kills the
  process group. Jobs are in memory (a server restart forgets them; the logs stay).
- Claude bridge: Claude may run flows but not edit files. The command is
  `claude -p <prompt> --output-format stream-json --verbose --permission-mode dontAsk --max-turns 30
  --tools Read,Grep,Glob,Bash --strict-mcp-config --disallowedTools <Edit, Write, NotebookEdit, git, rm, mv, cp, sed,
  tee, touch, mkdir, curl, wget, cf, docker, any redirect `*>*`, make clean/generate/table/precheck>
  --allowedTools Read,Grep,Glob,Bash(make *),Bash(python3 scripts/flow/*),Bash(cat *),Bash(ls *),Bash(head *),
  Bash(tail *),Bash(wc *) --append-system-prompt <no-edit rule>`. `dontAsk` never prompts and denies everything not
  allowed, so there is no way to escalate; the exact command line is stored on the job and shown by `job_status`.
  Verified: a Bash `echo hi > file` was denied by the harness and the file was not created
  (`tests/tools/test_tool_server.py`, live test `CLAUDE_LIVE=1`).

## Limits

- `make` allowed to Claude (`Bash(make *)`) still includes any non-denied target (for example `make gds`), as intended;
  it shares the owner's Claude plan, so keep tasks short.
- `klayout_view` needs a GDS (`designs/<d>/output/*.gds`, `build/results/<d>/` or a run dir); the images accumulate
  under `build/agent/klayout_gui/`.
- No auth and no persistence of job state across restarts.

## Tests

`build/agent/venv/bin/python -m pytest -q tests/tools/test_tool_server.py` (no Ollama, Docker or Claude needed);
`CLAUDE_LIVE=1` adds one real `claude_task`.
