# open-ai-chip: instructions for Claude

## What this repo is
Twenty-five small digital designs (tiny AI engines, a precision study, KV-cache attention, a Wishbone counter, SoC macros and Caravel
wrappers), each taken from RTL to clean GDSII on sky130A with LibreLane 3.0.2 in Docker, as a learning build toward
a ChipFoundry ChipIgnite (Caravel) tapeout. `README.md` has how to open and run everything, the design index and links (keep it short: links, no duplicated content);
`docs/RESULTS.md` has the generated
results tables; `SPEC.md` is the plan, phases, acceptance criteria and "Agent operating rules" (the rules below
come from it). `LOCAL_RUN_PLAN.md`, `provenance/SOURCES.md` and `versions.lock` record origin and pinned versions.
Sibling `../open-ai-silicon` is reference material only: never edit it.

Shared agent memory (any agent: Claude Code, Codex, Gemini CLI / Antigravity): `AGENTS.md` (how to run, where to extend, recipes, lessons learned).

## Layout
- `designs/<name>/`: one design per directory.
  - `config.json` LibreLane config (single source of the RTL file list; `"//KEY"` entries are comments with the
    reasoning for the key they annotate: read them before changing settings)
  - `rtl/`, `tb/` (self-checking testbench, `vectors.hex`), optional `pin_order.cfg`, `*.sdc`
  - `output/` committed evidence: `metrics.json`, `resources.json`, `flow.log`, `layout.png`, `<top>.lef`, `reports/`
  - `runs/` LibreLane run dirs, git-ignored (`RUN_<date>_<time>/`)
  - `NOTES.md` the design page with required headings (checked by `make test`); some designs also have `README.md`
- `model/<x>/` Python golden models and generators (`tiny_ai`, `audio_pitch`, `audio_onset`, `image_text_match`,
  `precision_hw`, `kv_attention` with `spec.md`). They generate the ROM `.v`, `vectors.hex`, `weights.json`.
- `shared/rtl/wb_stream_adapter.v`, `shared/tb/*.vh`: adapter and shared testbench code.
- `scripts/flow/` flow tooling (`find_reusable_run.py`, `check_signoff.py`, `signoff_allowances.json`,
  `run_capped.sh`, `gl_sim.sh`, `collect.sh`, `summary.py`); `scripts/docs/tables.py` writes the tables in `docs/RESULTS.md` (`make table`);
  `scripts/doctor.sh`, `scripts/check_generated.sh`; shared helpers `scripts/lib/common.sh` (shell) and `scripts/lib/repo.py`
  (Python): repo root, design list, `dir::` paths, LibreLane image pin, DOCKER_HOST default.
- `firmware/` + `soc_sim/`: PicoRV32 SoC simulation (`make soc-sim`; `make soc-kv` runs the KV-attention firmware, `firmware/kv/`). `caravel_sim/`: full Caravel RTL/GL sims
  (`make caravel-rtl`, `caravel-gl`, `caravel-fullgl`, `caravel-sdf-wrapper`; need `build/caravel` downloads, SDF also the amd64 CVC image). `precheck/`: ChipFoundry precheck (`make precheck`, 14/14 PASS, docs/PRECHECK.md).
- OpenROAD engines and how LibreLane chains them: `docs/OPENROAD_ENGINES.md`. GUIs: `bash scripts/gui/open_gui.sh openroad|magic <d>` (XQuartz/X11, setup in `docs/GUI_AND_LOGS.md`).
- KV-cache attention family `kv_attn_{n4,n8,n16,n8_int4,n8_ring}` (SoC macro `soc_kv_attn_n8`, wrapper `user_project_wrapper_soc_kv`): shared engine `shared/rtl/kv_attn_core.v`, testbench `shared/tb/kv_attn_tb.vh`, model `model/kv_attention/`; background `docs/LLM_INFERENCE.md`. Agent/harness demos: `examples/hermes_klayout_demo/`, `examples/hermes_harness/`, `examples/hermes_rag/` (BM25 retrieval over the docs).
- `tests/run_tests.sh` (`make test`), `tests/adapter/`, `docs/` (architecture, SoC plan, Caravel sim, precision study).
- `build/` is git-ignored: stage logs `build/flow/`, results `build/results/<d>/`, macro views `build/macros/<m>/`.

## First commands
- `make help` lists every target (`DESIGN=<name>` selects the design; default `user_proj_example`).
- `make doctor` checks host tools, Docker daemon, LibreLane image, PDK. Run it before any physical flow.
- Opening GUIs and finding logs (macOS and Linux): `docs/GUI_AND_LOGS.md`.
- `make test` is the fast gate (about 100 to 125 s, no Docker): structure, configs, lint of every design, model checks,
  regeneration reproducibility, RTL sims, adapter, SoC sims, notes headings, negative tests for every design family,
  docs checks (links, make targets, inventory, README numbers vs metrics.json) and the agent/tools pytest (`tests/tools`).
- `make test-full` (`tests/test_full.sh`, about 5 min): per design run-state + simulate + check + gl-final, plus adapter,
  soc-sim, soc-kv, caravel-rtl/gl; never re-runs a physical flow. Coverage map: `tests/TEST_MATRIX.md`; results: `docs/VALIDATION.md`.
- `make flow-all DESIGN=<d>`: simulate, gds, check, gl, gl-final, collect (the one command).
- `bash scripts/run_all_mac.sh [--all]`: every stage in order from a Mac terminal (guide `docs/RUN_ON_MAC.md`).
- Linux (Ubuntu/Debian): `bash scripts/setup_linux.sh` once, then `bash scripts/run_all.sh [--all]` (OS-aware; `run_all_mac.sh` calls it); guide `docs/RUN_ON_LINUX.md`.

## Workflows (project skills in `.claude/skills/`)
- `harden-design`: RTL to clean GDSII, results, the failure table, what never to do.
- `add-tiny-engine`: a new stream engine (model, generated ROM and vectors, RTL, tb, config).
- `precision-variant`: a new number-format variant of the 9-input neuron in `model/precision_hw/`.
- `write-design-notes`: `designs/<d>/NOTES.md` with the required headings and sourced numbers.
- `wrapper-build`: macro views, `user_project_wrapper*` builds around a macro (`make views`, `make wrapper`).
- `soc-run`: `make soc-sim`, `adapter-test`, `caravel-rtl`, `caravel-gl`.
- `chip-demos`: the 15 narrated Hermes demos ("run demo 4").
- `tune-synthesis`, `tune-timing-sdc`, `tune-openroad-engines`: change and judge SYNTH_*, clock/SDC/margins, and OpenROAD
  engine settings (HARD RULES apply: never loosen CLOCK_PERIOD or MAX_TRANSITION_CONSTRAINT, never DELAY, never DISABLE_LVS).
- `whatif-experiment`: try such a change on a copy (`build/whatif/<d>__<tag>/`), compare with the committed run; never edits `designs/<d>/`.
Load the matching skill before starting that kind of task.
A learner's guide to these skills (fundamentals, insights, how they fit together) is in `docs/SKILLS.md`.

## HARD RULES
Security and ownership
- Never run `cf login`, `cf init`, `cf push`, submit, reserve or `cf confirm`, and never publish anything; those
  need the owner's direct action. Never change the GitHub repository from private to public.
- Do not edit `../open-ai-silicon`. No external model downloads or nondeterministic data in tests.

Physical flow
- Never loosen `MAX_TRANSITION_CONSTRAINT` or `CLOCK_PERIOD` (25 ns) to make a gate pass; never `DISABLE_LVS`;
  keep `ERROR_ON_SYNTH_CHECKS` true (only the elaborate-only Caravel wrappers set it false); never
  `SYNTH_STRATEGY` DELAY; never weaken DRC, LVS, timing or precheck settings; never accept deleted logic
  (a `signoff_allowances.json` entry needs a reason verifiable in the RTL).
- Do not modify fixed wrapper geometry or pin locations (`designs/user_project_wrapper*/fixed_dont_change`).
- Run physical flows one at a time (each is capped at 10 min by `FLOW_TIMEOUT`, default 600 s). Check `docker ps`
  first: other sessions may be running. After the first decisive failure stop, keep the log and run dir.

Generated files
- Never hand-edit generated files: ROM `.v`, `vectors.hex`, `weights.json`. Change the model under `model/<x>/`,
  run `make generate`, and confirm with `make check-generated`.

Evidence and hygiene
- Every number in a doc comes from a file in this repo, and the doc names that file (`metrics.json`,
  `resources.json`, `build/sim/<d>/sim.log`, ...). Report only measured numbers; mark estimates as estimates.
- No absolute home paths (`/Users/...`) in committed files (`make test` structure check fails on them).
- Commit only with `make test` passing, and only when asked. Never commit `runs/`, `build/`, `*.gds`.
- Staleness: `*.md` files are excluded from the run-reuse check, so documentation edits do not make a run stale;
  any other edit in a design dir (`config.json`, `rtl/`, `pin_order.cfg`, `*.sdc`) does, and the next
  `make gds` runs the flow again (`scripts/flow/find_reusable_run.py`). Do not touch a design's files casually
  if its committed evidence must stay current.
- Upstream template RTL (`user_proj_example`) must stay byte-identical (`tests/upstream.sha256`).
- FROZEN: all 25 validated designs are pinned by sha256 in `designs/FROZEN.json` (`make check-frozen`, part of `make test`; guard API `scripts/flow/frozen.py` `is_frozen(path)`). The Makefile refuses `gds`/`collect`/`flow-all`/`flow` on frozen designs (`FROZEN_OK=1` is the owner's override). Agents never edit frozen paths or run `make freeze`; unfreeze is owner-only: change deliberately, re-harden, `make freeze`, commit (`designs/FROZEN.md`).

## Conventions
- Clock 25 ns (40 MHz) on `wb_clk_i`; sky130_fd_sc_hd; `PROFILE=tight` container (2 CPUs, 8 GB).
- Results tables in `docs/RESULTS.md` between `<!-- results:begin ... -->` markers are generated: run `make table`, do
  not edit them by hand.
- Handoff form for phase reports is in `SPEC.md` "Agent operating rules": status, commands and exit codes, files
  changed, evidence paths, measured budgets, first failure.
- Other agents may work in the same tree: change only files you own for the task and say what you touched.

The maintained agent front end is the Nous Hermes desktop app (`make hermes-app` = `bash scripts/hermes_start.sh`; docs: `hermes-agents.md`, `docs/HERMES_AGENT_INTEGRATION.md`); Open WebUI and the wrapper app (`examples/hermes_desktop/`, `docs/HERMES_DESKTOP.md`) are kept unmaintained. Running every Hermes example from the terminal: `docs/HERMES_FROM_TERMINAL.md`. Read-only EDA/KLayout tools for agents: `tools/eda_tools.py` (also `tools/mcp_server.py`); local Hermes agent and its evaluation: `docs/HERMES_AGENT.md` (venv at `build/agent/venv`, tests in `tests/tools/`).
Hermes Agent (Nous, installed on this Mac) mapped to this repo, with the desktop-app workflow and safety plan (statuses in the page; setup: `bash scripts/hermes_agent_setup.sh`, review the diff, then `--apply`): `docs/HERMES_AGENT_INTEGRATION.md`.

## For Hermes (Nous Hermes Agent, profile `chip`; ignore if you are Claude Code)
- The whole integration, point by point: `hermes-agents.md`.
- Start (checks and starts Ollama, models, Docker, tool server, Hermes.app): `bash scripts/hermes_start.sh`; pinned demo sessions: `--demo-sessions`.
- This session is read plus gated runs. You have no terminal and cannot edit files: a hook blocks `write_file`/`patch`. Do not try; say what change is needed and who should make it.
- Use the `mcp_chip_*` tools: read tools freely; `run_make` and `whatif_run` start nothing until the user answers "yes, run <confirm_id>", and the hook asks for approval on physical flows. One physical flow at a time.
- Frozen designs (`designs/FROZEN.json`) get no flows; use `whatif_run` on a copy instead. Never suggest loosening `CLOCK_PERIOD` / `MAX_TRANSITION_CONSTRAINT`.
- Use `ask_claude` only when the user asks for Claude or a local tool was tried and is inconclusive; it costs the owner's Claude plan. Cite the file for every number.
