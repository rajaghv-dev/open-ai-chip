# open-ai-chip: instructions for Claude

## What this repo is
Twenty-three small digital designs (tiny AI engines, a precision study, KV-cache attention, a Wishbone counter, SoC macros and Caravel
wrappers), each taken from RTL to clean GDSII on sky130A with LibreLane 3.0.2 in Docker, as a learning build toward
a ChipFoundry ChipIgnite (Caravel) tapeout. `README.md` has the design index, the run commands and the generated
results tables; `SPEC.md` is the plan, phases, acceptance criteria and "Agent operating rules" (the rules below
come from it). `LOCAL_RUN_PLAN.md`, `provenance/SOURCES.md` and `versions.lock` record origin and pinned versions.
Sibling `../open-ai-silicon` is reference material only: never edit it.

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
  `run_capped.sh`, `gl_sim.sh`, `collect.sh`, `summary.py`); `scripts/docs/tables.py` writes the README tables;
  `scripts/doctor.sh`, `scripts/check_generated.sh`.
- `firmware/` + `soc_sim/`: PicoRV32 SoC simulation (`make soc-sim`; `make soc-kv` runs the KV-attention firmware, `firmware/kv/`). `caravel_sim/`: full Caravel RTL/GL sims
  (`make caravel-rtl`, `caravel-gl`, `caravel-fullgl`, `caravel-sdf-wrapper`; need `build/caravel` downloads, SDF also the amd64 CVC image). `precheck/`: ChipFoundry precheck (`make precheck`, 14/14 PASS, docs/PRECHECK.md).
- KV-cache attention family `kv_attn_{n4,n8,n16,n8_int4,n8_ring}`: shared engine `shared/rtl/kv_attn_core.v`, testbench `shared/tb/kv_attn_tb.vh`, model `model/kv_attention/`; background `docs/LLM_INFERENCE.md`. Agent/harness demos: `examples/hermes_klayout_demo/`, `examples/hermes_harness/`.
- `tests/run_tests.sh` (`make test`), `tests/adapter/`, `docs/` (architecture, SoC plan, Caravel sim, precision study).
- `build/` is git-ignored: stage logs `build/flow/`, results `build/results/<d>/`, macro views `build/macros/<m>/`.

## First commands
- `make help` lists every target (`DESIGN=<name>` selects the design; default `user_proj_example`).
- `make doctor` checks host tools, Docker daemon, LibreLane image, PDK. Run it before any physical flow.
- `make test` is the fast gate (about 65 s, no Docker): structure, configs, lint of every design, model checks,
  regeneration reproducibility, RTL sims, adapter, SoC sim, notes headings, negative tests.
- `make flow-all DESIGN=<d>`: simulate, gds, check, gl, gl-final, collect (the one command).

## Workflows (project skills in `.claude/skills/`)
- `harden-design`: RTL to clean GDSII, results, the failure table, what never to do.
- `add-tiny-engine`: a new stream engine (model, generated ROM and vectors, RTL, tb, config).
- `precision-variant`: a new number-format variant of the 9-input neuron in `model/precision_hw/`.
- `write-design-notes`: `designs/<d>/NOTES.md` with the required headings and sourced numbers.
- `wrapper-build`: macro views, `user_project_wrapper*` builds around a macro (`make views`, `make wrapper`).
- `soc-run`: `make soc-sim`, `adapter-test`, `caravel-rtl`, `caravel-gl`.
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

## Conventions
- Clock 25 ns (40 MHz) on `wb_clk_i`; sky130_fd_sc_hd; `PROFILE=tight` container (2 CPUs, 8 GB).
- Results tables in `README.md` between `<!-- results:begin ... -->` markers are generated: run `make table`, do
  not edit them by hand.
- Handoff form for phase reports is in `SPEC.md` "Agent operating rules": status, commands and exit codes, files
  changed, evidence paths, measured budgets, first failure.
- Other agents may work in the same tree: change only files you own for the task and say what you touched.

Read-only EDA/KLayout tools for agents: `tools/eda_tools.py` (also `tools/mcp_server.py`); local Hermes agent and its evaluation: `docs/HERMES_AGENT.md` (venv at `build/agent/venv`, tests in `tests/tools/`).
