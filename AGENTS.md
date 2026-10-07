# AGENTS.md: shared memory for coding agents working on open-ai-chip

One file for any coding agent: Codex reads `AGENTS.md`, Claude Code reads `CLAUDE.md` (which points here), and Gemini
CLI / Antigravity (`agy`) read `GEMINI.md` (which points here). Hermes Agent reads `.hermes.md` first, so this file does
not change Hermes. It holds:
- what an agent needs to extend the repo safely;
- the recipes for the usual extensions;
- the lessons that cost time to learn.

The full rules are in `CLAUDE.md` (HARD RULES), the plan in `SPEC.md`, and the Hermes integration in `hermes-agents.md`.

## 1. What this repo is, in five lines

- 25 small digital designs (tiny AI engines, a precision study, KV-cache attention, SoC macros, Caravel wrappers), each
  taken from RTL to signoff-clean GDSII on sky130A with LibreLane 3.0.2 in Docker, on a laptop.
- Evidence is committed per design: `designs/<d>/output/metrics.json`, `reports/`, `layout.png`. All 25 designs are
  **frozen** by sha256 in `designs/FROZEN.json`.
- Python golden models in `model/<x>/` generate ROMs and test vectors; self-checking testbenches live in `designs/<d>/tb/`.
- A local agent (Nous Hermes Agent, profile `chip`) reads, shows, explains and experiments through a tool server
  (`examples/hermes_desktop/tool_server/`).
- Tests: `make test` (fast gate, about 100 to 125 s, no Docker) and `make test-full` (about 5 min).

## 2. Rules that are never bent (summary; the full list is in `CLAUDE.md`)

- **Owner only:**
  - never run `cf login|init|push|submit|confirm`;
  - never publish anything; repository visibility is owner-only. The repo is public (Apache-2.0): never commit
    secrets, tokens, credentials, private keys or private files, and check the diff before every push;
  - never edit `../open-ai-silicon`.
- **Signoff settings:**
  - never loosen `CLOCK_PERIOD` (25 ns) or `MAX_TRANSITION_CONSTRAINT`;
  - never `DISABLE_LVS`, never `SYNTH_STRATEGY DELAY`;
  - keep `ERROR_ON_SYNTH_CHECKS` true;
  - never weaken DRC, LVS, timing or precheck;
  - never accept deleted logic.
- **Frozen designs:**
  - never run `make gds|flow-all|collect|views|freeze` on a frozen design and never edit its files (the Makefile refuses
    `gds`, `collect`, `flow-all` and `flow` on frozen designs; `FROZEN_OK=1` is the owner's override);
  - experiment on a copy (`whatif`, under `build/whatif/`);
  - unfreezing is owner work: change deliberately, re-harden, `make freeze`, commit.
- **Generated files:** ROM `.v`, `vectors.hex` and `weights.json` are never hand-edited. Change `model/<x>/`, run
  `make generate`, confirm with `make check-generated`.
- **Evidence:**
  - every number in a doc comes from a repo file, and the doc names it;
  - mark estimates as estimates;
  - no absolute home paths in committed files.
- **One physical flow at a time:** check `docker ps` first; flows are capped at 10 minutes.
- **Commits:** only with `make test` passing and only when the owner asks. Never commit `build/`, `runs/` or `*.gds`.

## 3. Run the repo

| Goal | Command |
|---|---|
| What is missing on this machine | `make doctor` |
| macOS: verify everything (tests, sims, signoff, gate level) | `bash scripts/run_all_mac.sh` (`--all` adds flows, precheck, agents, GUIs) |
| Linux (Ubuntu/Debian): set up, then verify | `bash scripts/setup_linux.sh && bash scripts/run_all.sh` |
| Fast gate (before any commit) | `make test` |
| Heavy local checks, no new physical flow | `make test-full` |
| One design end to end (only a design that is **not** frozen) | `make flow-all DESIGN=<d>` (simulate, gds, check, gl, gl-final, collect) |
| Re-check a frozen design without touching it | `make simulate|check|gl|gl-final DESIGN=<d>` (these reuse the committed run) |
| The full flow on a frozen design, safely | in Hermes `chip rebuild <d>`, or `whatif_run` with `rebuild: true` (a copy under `build/whatif/`) |
| The Hermes desktop agent (maintained front end; Python packages of the agent venv: `tools/requirements.txt`) | `bash scripts/hermes_start.sh` (= `make hermes-app`) (checks and starts Ollama, the models, Docker, the tool server, then Hermes.app) |
| Every make target | `make help` |

Guides: `docs/RUN_ON_MAC.md`, `docs/RUN_ON_LINUX.md`, `docs/GUI_AND_LOGS.md`, `hermes-agents.md`.

## 4. Where things live (for extending)

| You want to change | Files | Then run |
|---|---|---|
| A design's RTL, config, pins, SDC | `designs/<d>/{rtl,config.json,pin_order.cfg,*.sdc}` (read the `"//KEY"` comments first) | `make flow-all DESIGN=<d>` (unfrozen designs only) |
| A golden model, ROM or vectors | `model/<x>/`, then `make generate` | `make check-generated`, `make model-check` |
| Shared engines and testbench code | `shared/rtl/`, `shared/tb/` (they feed many designs: every user goes stale) | `make test` |
| Flow tooling | `scripts/flow/` (`find_reusable_run.py`, `check_signoff.py`, `whatif_flow.sh`, `frozen.py`) | `make test` |
| A tool for the agents | a new `examples/hermes_desktop/tool_server/<name>_tools.py` with a FastAPI `router` (mounted automatically) | `pytest tests/tools` |
| A Hermes command (`/x` and plain `x`) | `quick_tools.py` (`run_cmd`, `PLAIN_CMDS`, `START_CMDS`, `HELP`) and `scripts/hermes/plugin/open-ai-chip/__init__.py` (`COMMANDS`) | `pytest tests/tools/test_quick_tools.py`; restart Hermes.app |
| The tool list that Hermes models see | `tools/hermes_tools.json` (USE WHEN / NOT FOR / ARGS / EXAMPLE) | `pytest tests/tools/test_hermes_mcp_bridge.py` |
| Hermes context (rules and routing) | `scripts/docs/make_hermes_context.py`, then `make master-prompt` (writes `.hermes.md`) | `make test` (checks it is current) |
| The Hermes profile, hook, approvals | `scripts/hermes/setup_profile.py`, `scripts/hermes/hooks/` | `bash scripts/hermes_agent_setup.sh` (dry run), then the owner runs `--apply` |
| A skill (Claude Code and Hermes both read them) | `.claude/skills/<name>/SKILL.md` (frontmatter `name`, `description`) | `pytest tests/tools/test_tool_server.py` (skill count) |
| A demo card / pinned session | `quick_tools.DEMO_CARDS`, then `bash scripts/hermes/make_demo_sessions.sh` | `pytest tests/tools/test_quick_tools.py` |
| Docs | the page itself; scripts carry a `Docs:` header naming the page that explains them | `make test` (links, make targets, traceability) |

## 5. Recipes

**Add a new tiny engine**
- Follow `.claude/skills/add-tiny-engine/SKILL.md`: model, generated ROM and vectors, RTL, testbench, config.
- Then `harden-design`, then `write-design-notes`.
- Then add the design to the docs index (`README.md` design table) and run `make table`, `make test`.

**Add a number-format variant of the neuron**
- `.claude/skills/precision-variant/SKILL.md`.

**Try a setting without touching a design**
- `.claude/skills/whatif-experiment/SKILL.md`. In Hermes: `params <d> <KEY>`, then `chip whatif <d> KEY=VALUE`, then
  `result <tag>`.
- The HARD RULES are checked in code (`whatif_tools.check_key`).

**Add a Hermes command**
1. Write `cmd_<name>(...)` in `quick_tools.py`, returning markdown that names its source file.
2. Add it to `run_cmd`, `HELP`, `PLAIN_CMDS` (and `START_CMDS` if it starts a long job: `run`, `rebuild`, `whatif`, `experiment` need `chip ` or `/`; `sim` is not in it and starts the cheap RTL simulation at once).
3. Register it in the plugin's `COMMANDS`. Check that the name is not a Hermes built-in: type `/` in Hermes, or see
   `website/docs/reference/slash-commands.md` in the Hermes install. `/loop` is one.
4. Add a test to `tests/tools/test_quick_tools.py`.
5. Restart the tool server (`bash scripts/hermes_start.sh` does it when the code is newer) and Hermes.app.

**Add an agent tool for the models**
1. A FastAPI route in a `*_tools.py` module.
2. A description in `tools/hermes_tools.json`; add it to `core` only if it is worth its context cost.
3. A routing line in `make_hermes_context.py`, then `make master-prompt`.
4. A test in `tests/tools/`.

**Change a frozen design (owner)**
- Unfreeze it in `designs/FROZEN.json` deliberately, change it, run `make flow-all DESIGN=<d>`, check the evidence,
  `make freeze`, `make test`, commit.

## 6. Lessons learned (read before you debug)

**Flow and evidence**
- Any non-`.md` edit in a design directory makes its run stale, so the next `make gds` runs the flow again
  (`find_reusable_run.py`). Files under `model/` are hashed into design inputs too.
- `make gds|flow-all|collect|flow` on a frozen design is refused by the Makefile (they would rewrite frozen evidence, and
  `make check-frozen` inside `make test` would then fail). Use a what-if copy.
- `whatif_flow.sh` refuses any directory outside `build/whatif/`.
- A rebuild of kv_attn_n8 on a copy took 104 s and matched the committed run exactly. vision_all_lit at a 4 ns clock
  makes LibreLane stop with hold violations at the three fast corners (`speed_results.json`).

**Docs**
- `tests/check_docs.py targets` reads any `make <word>` in a doc as a make target, so "make in", "make and" or
  "make jobs" in prose fail the gate. Reword them.
- The RAG corpus is `git ls-files '*.md'`. A newly tracked page that quotes the RAG evaluation questions lowers recall
  and fails `tests/tools/test_rag*.py`. Add such pages to the `EXCLUDE` lists in `examples/hermes_rag/rag.py` and
  `examples/hermes_desktop/tool_server/rag_tools.py`.

**Hermes Agent**
- Hermes loads **one** context file, first match wins: `.hermes.md` > `AGENTS.override.md` > `AGENTS.md` > `CLAUDE.md`.
  `.hermes.md` is generated (`make master-prompt`); edit the generator, never the file.
- Plugin slash-command output is drawn in Hermes.app as a small grey plain-text system line (`system-message.tsx`).
  Commands typed **without** `/` go through the plugin's `pre_llm_call` router and come back as a normal chat reply.
  `transform_llm_output` replaces the model's one-word reply with the exact text.
- A 9B local model that paraphrases tool output invents facts: it once invented a `MAX_TRANSITION_CONSTRAINT` fix, and
  once flipped the sign of a slack. Lookups and launches are done in code; quotes are shown verbatim.
- A plugin cannot take a built-in command name (`/loop`); Hermes skips it silently.
- Sessions with source `oneshot` are hidden in the desktop sidebar, even when pinned. Create them with `--source cli`.
  The CLI output ends lines with `\r`, and Hermes refuses a title that contains one.
- Processes started from inside Hermes inherit its `PYTHON*` and `VIRTUAL_ENV` variables. Clear them before starting
  the repo venv (`env -u PYTHONPATH ...`), or the repo Python imports Hermes's packages and crashes.
- Tool-server jobs live in the server's memory: restarting the server ends running jobs (their logs stay in
  `build/agent/jobs/`). The start script only restarts it when no job is running.
- Ollama resets a model's unload timer on every request (Hermes requests use the 5-minute default), and an empty
  "load" request does not refresh it. `scripts/hermes/keep_models_warm.sh` re-pins with a one-token generation every
  2 minutes while Hermes.app runs.
- `tool_server.py` mounts each `*_tools.py` under its own module name. Tests patch the mounted copy through the route's
  `endpoint.__globals__`, not through `import`.
- Design names may be loose ("kv_attn", "vision lit", "llm"). An ambiguous name gets a numbered question, never a guess.
  Names containing shell syntax are refused.

**Safety layers for agents**
- Hermes approvals (deny globs).
- The `pre_tool_call` hook (arguments, HARD-RULE keys, frozen paths, secrets).
- The confirm guard: a gated run needs the **user's** "yes, run <id>"; the model once confirmed its own run.
- The tool server's own allow-lists. Keep all of them when extending.

## 7. Agent-specific notes

| Agent | Reads | Repo extras |
|---|---|---|
| Claude Code | `CLAUDE.md` (HARD RULES, layout) and this file | skills in `.claude/skills/` (`/harden-design`, `/whatif-experiment`, ...) |
| Codex | this file (`AGENTS.md`) | the skills are plain markdown: read `.claude/skills/<name>/SKILL.md` before that kind of task |
| Gemini CLI / Antigravity (`agy`) | `GEMINI.md`, which points here | same skills as markdown |
| Hermes Agent (local, read and gated runs only) | `.hermes.md` | tools over MCP, plugin commands; see `hermes-agents.md` |

## 8. Before you hand off

- Run `make test`; it must pass. If you touched the flow or a design, also run `make test-full`.
- `make check-frozen` must be clean, unless the owner asked to change a frozen design.
- `git status`: only your files; no `build/`, `runs/` or `*.gds`; no absolute home paths.
- Report in the SPEC.md handoff form: status, commands and exit codes, files changed, evidence paths, measured numbers,
  first failure.
