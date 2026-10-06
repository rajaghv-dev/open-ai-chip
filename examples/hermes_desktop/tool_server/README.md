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
| `rag_search {query, k?, design?}`, `rag_answer {question, k?}`, `rag_index {rebuild?}` | hybrid mini RAG (`rag_tools.py`): BM25 + local Ollama embeddings fused by reciprocal rank over notes, docs, specs, script headers and metrics key facts; `rag_answer` returns verbatim quoted sentences with file:line; falls back to BM25 when Ollama is down (`docs/HERMES_AGENT_INTEGRATION.md`, "Mini RAG") |
| `klayout_view {design, layers?, zoom?, demo_markers?, width?, height?}` | offscreen KLayout render; returns `{png_url, view_bbox_um, visible_layers, markdown}`; the PNG is served at `GET /img/<name>` from `build/agent/klayout_gui/` |
| `list_skills`, `get_skill {name}` | the project skills in `.claude/skills/<name>/SKILL.md` |
| `run_make {target, design?}` | allow-listed `make` target as a background job; returns `{job_id}` |
| `job_status {job_id}`, `job_list`, `job_cancel {job_id}` | state `running/done/failed`, rc, seconds, last 60 log lines (logs in `build/agent/jobs/<id>.log`) |
| `claude_task {instructions, skill?, design?, max_turns?}` | headless Claude Code CLI as a job, edit tools denied; poll with `job_status` |
| `skill_plan {skill, design?}` | ordered steps of one project skill with the tool per step; run skills (harden-design, soc-run, wrapper-build) use `run_make`, edit skills hand off to Claude (`skills_memory_tools.py`) |
| `remember {note, topic?}`, `recall {query?}`, `forget {id}`, `memory_digest`, `context_brief` | local markdown memory in `build/agent/memory/` (MEMORY.md, <topic>.md, runs.md; never committed; bounded, rotated, no secrets); `context_brief` = `docs/AGENT_CONTEXT.md` + digest, called at the start of a chat |
| `run_summary {design?, run?, job_id?}` | `analysis_tools.py`: status per stage, key numbers, current-or-stale, diff vs committed metrics, plain-English `summary`; accepts a finished `run_make` job id |
| `diagnose {design? \| job_id?}` | matches logs/metrics to the harden-design failure table (`reference.md`, as data) with evidence, fix and doc link; `clean: true` when nothing failed |
| `suggest {design, classify?}` | rule-based suggestions (thin setup/hold, slew classification, utilisation, stale run, missing macro views, fill/tap split, removed flops, what to read next); never advises loosening constraints |
| `notes_section {design, section?, item?}`, `explain {design, topic}` | the exact (quoted) text of a NOTES.md section / best matching paragraphs, with file, heading, lines |
| `proof_local`, `show_context {chat_id?}`, `call_log {limit?}` | `proof_tools.py`: evidence that the stack is local (sockets, `ollama ps`, offline env, outbound connections, verdict), what the last turn was sent, and the call log (every tool call with the repo files it read: path, sha256, git blob; `build/agent/proof/calls.jsonl`). Hidden endpoints `proof_turn`, `proof_record` serve the receipt filter and the memory filter. The module also has `install(app)`, called by `tool_server.py`, which adds the logging middleware. See docs/HERMES_DESKTOP.md "Proof: local, repo, context" |
| `list_experiments {group?}`, `run_experiment {id, design?}`, `experiment_result {id, design?, job_id?}`, `engine_pictures {design?, view?}`, `list_demos`, `demo_steps {name}` | `experiments_tools.py`: the catalog of everything the repo can run (per-design flows, families, system sims, pictures), a run that goes through the gated `run_make` (same confirmation, same one-physical-flow rule), results parsed by code (soc-kv prefill/decode, soc-sim cycles, precision 7-format table, KV cache bits vs flip-flops, adapter-test, precheck), OpenROAD engine pictures with explanation, and the numbered demos (`demo_defs.py`). See docs/HERMES_DESKTOP.md "Experiments and demos" |
| `list_logs {design? \| job_id?}`, `read_log {design?, which, tail?, grep?, around?}`, `log_digest {design \| job_id}`, `open_gds {design, viewer}`, `open_file {path, start?, end?, viewer?}` | `logs_tools.py`: every log of a design or job with sizes and times; one log by `which` (flow, error, warning, make, stages, stage:<name>, step:<NN or name>, sim, gl, job:<id>, precheck, repo path) with tail, regex or around-line; a code-built digest (errors and warnings grouped by step, slowest steps, stage outcomes); open the GDS in the KLayout application / live window / Magic / PNG (read-only, logged in `build/agent/proof/opens.jsonl`); a repo text file with a line range or in the OS viewer. Repo-relative paths only, no traversal or symlink escape, 300 lines and 24 KB per reply |
| `capability_map {group?}` | `capability_tools.py`: what the agent can do (answer to `/help`): every slash prompt of `install_prompts.py` (`CHIP_PROMPTS`) with an example and the tool, and every mounted operation, grouped Ask, Run, Analyse, Logs, Layout/GUI, Skills, Memory, Experiments/Demos, Proof |
| `param_info {design, key?, engine?}`, `propose_change {design, changes}`, `whatif_run {design, changes, tag}`, `whatif_result {tag \| job_id \| sweep_id}`, `whatif_sweep {design, key, values}`, `whatif_list`, `whatif_clean {tag \| all}` | `whatif_tools.py`: change synthesis, timing-constraint and OpenROAD-engine settings on a COPY (`build/whatif/<d>__<tag>/`), never on `designs/<d>/`. `param_info` explains a LibreLane 3.0.2 key (value, default, engine, safe range, rule, doc); `propose_change` checks the HARD RULES in code (no loosening of `CLOCK_PERIOD` / `MAX_TRANSITION_CONSTRAINT`, no DISABLE_LVS, no DELAY, `ERROR_ON_SYNTH_CHECKS` true, nothing weaker, wrapper geometry fixed, unknown keys refused) and returns a PATCH TEXT that is never applied; `whatif_run` and `whatif_sweep` use the same "yes, run <id>" gate and the shared one-physical-flow lock as `run_make`, run `scripts/flow/whatif_flow.sh` (Makefile container setup, 600 s cap, then `check_signoff.py`); `whatif_result` compares with the committed `metrics.json` (cells, flip-flops, die, utilisation, setup/hold per corner, DRC/LVS/XOR/antenna, slew, power, wall time) and gives a verdict. See docs/HERMES_DESKTOP.md "What-if experiments: change constraints safely" |
| `health` | versions, Ollama, Docker (colima socket), Claude CLI path, running jobs |

`run_make` targets: `doctor test simulate check gl gl-final gds flow-all views collect view soc-sim soc-kv
adapter-test model-check check-generated test-full caravel-rtl caravel-gl`. Anything else (generate, table, clean,
precheck, all-designs, ...) is refused. `design` must be in the Makefile `ALL_DESIGNS` list.

## Safety model

- Two-step confirmation: `run_make` and `claude_task` start nothing on the first call; they return
  `{needs_confirmation, confirm_id, will_run, say}`. A second call with that `confirm_id` (valid 10 min, single use)
  starts the job. `CHIP_TOOLS_NO_CONFIRM=1` disables the gate for tests and terminal use.

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

`build/agent/venv/bin/python -m pytest -q tests/tools/test_tool_server.py` (no Ollama, Docker or Claude needed); what-if tools: `tests/tools/test_whatif.py` (no Docker: the job start is faked);
`CLAUDE_LIVE=1` adds one real `claude_task`.

## Memory hook for the job system
`skills_memory_tools.record_run(design, command, result, key_numbers=None, log_path=None)` appends a dated line to
`build/agent/memory/runs.md` (never raises). The job manager calls it when a job finishes (import the module like
`_mount_extensions` does, or `POST /record_run`, hidden from the model's tool list). Open WebUI side:
`examples/hermes_desktop/install_prompts.py` (slash prompts and the memory-digest filter `memory_filter.py`);
start.sh hook, after the preset install: `python3 "$REPO/examples/hermes_desktop/install_prompts.py" || echo "prompt install failed" >&2`.
