# open-ai-chip

Twenty-five small digital designs (tiny AI engines, a precision study, KV-cache attention, SoC macros and Caravel
wrappers), each taken from RTL to signoff-clean GDSII on sky130A with LibreLane 3.0.2 in Docker, on a laptop. Local
Hermes agents read the results, drive KLayout and OpenROAD, and can hand flows to Claude.

## Open and run

```bash
cd open-ai-chip
make doctor                                  # tools, Docker, LibreLane image, PDK: what is missing and how to get it
bash scripts/run_all_mac.sh                  # macOS: verify everything (tests, sims, signoff, gate level), ~10 min (estimate)
bash scripts/run_all_mac.sh --all            # + flows, precheck, Hermes agents, GUI windows
bash scripts/setup_linux.sh && bash scripts/run_all.sh   # Linux: set up, then the same run
```

Order for a new or owner-unfrozen design: `make doctor`, `make test`, then `make flow-all DESIGN=<d>`. Fast gate: `make test`. All targets: `make help`.

All 25 designs are frozen (`designs/FROZEN.json`): the Makefile refuses `gds`, `collect`, `flow-all` and `flow` on them. On a
frozen design use `make simulate DESIGN=kv_attn_n8`, `make view DESIGN=kv_attn_n8` or a what-if copy; unfreeze is owner-only
([designs/FROZEN.md](designs/FROZEN.md)).

## Hermes Agent desktop app

The front end is Nous Research's **Hermes Agent** desktop app (Hermes.app), connected to this repo over MCP with a
`chip` profile (local Ollama `qwen3.5-64k:9b`, no file edits, gated runs, Claude only via `ask_claude` when needed).

```bash
bash scripts/hermes_agent_setup.sh            # dry run: shows the diff it would make in ~/.hermes (review it)
bash scripts/hermes_agent_setup.sh --apply    # backup ~/.hermes, create the chip profile, MCP bridge, skills, hook, cron
bash scripts/hermes_start.sh                   # every time: checks and starts Ollama + models, Docker, tool server, then opens Hermes.app
bash scripts/hermes_start.sh --demo-sessions   # once before a class: nine pinned "Demo N" sessions with the commands to type
```

Instant commands in Hermes (no model turn, about a second; partial names work): `/klayout kv_attn show only met1`,
`/magic vision lit`, `/gds kv8`, `/timing kv_attn`, `/synth vision lit`, `/drc kv8`, `/lvs prec bf16`, `/signoff caravel kv`,
`/compare kv4 kv8 kv16`, `/sim kv8`, `/run synth vision_block`, `/rebuild kv8`, `/jobs`; loop and harness demos
`/loopdemo signoff kv`, `/loopdemo layers kv8`, `/harness facts kv`; `/chip` lists them all. An ambiguous name
(`/timing audio`) gets a numbered question: answer `2` or `/pick 2`. Top-down guide: [hermes-agents.md](hermes-agents.md); class script: [docs/HERMES_CLASS_SHOWCASE.md](docs/HERMES_CLASS_SHOWCASE.md).

Ask in Hermes: "How many cells does kv_attn_n8 have?", "Why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8?",
"Show the errors in kv_attn_n8's logs", "Open kv_attn_n8 in KLayout and show met1", "Run the simulation for vision_block" (asks
for approval, then `yes, run <id>`), "Summarize the last run", "What should I improve in prec_bf16?", "What if the clock
were 20 ns for vision_block?", "Ask Claude to ...". Every part of the integration, point by point: [hermes-agents.md](hermes-agents.md). Full map, safety and status:
[docs/HERMES_AGENT_INTEGRATION.md](docs/HERMES_AGENT_INTEGRATION.md). Fifteen narrated demos to run in the app ("run demo 4"): [docs/HERMES_DEMOS.md](docs/HERMES_DEMOS.md). Undo: `bash scripts/hermes_agent_setup.sh --uninstall --apply`.

Older front ends (Open WebUI with `make hermes` / `make demo*`, the repo's wrapper app, terminal agent loops) remain in
the repo unmaintained; what is incomplete is listed in [SPEC.md](SPEC.md#agent-front-ends-decision-2026-10-07) and
[docs/HERMES_DESKTOP.md](docs/HERMES_DESKTOP.md).

## Open by hand

| Open | Command |
|---|---|
| Hermes Agent desktop app (maintained) | `make hermes-app` (= `bash scripts/hermes_start.sh`; `ARGS=--check` or `--demo-sessions`) |
| Legacy Open WebUI front end (unmaintained) | `make hermes` (or `bash scripts/hermes.sh`) |
| Hermes in the terminal | `build/agent/venv/bin/python tools/hermes_agent.py "How many standard cells does vision_block have?"` |
| KLayout driven by Hermes | `bash examples/hermes_klayout_gui/start_live.sh`, then `build/agent/venv/bin/python examples/hermes_klayout_gui/agent.py --backend live "open kv_attn_n8, show met1"` |
| OpenROAD GUI, live heat maps | `bash scripts/gui/open_gui.sh heatmaps kv_attn_n8` |
| Magic | `bash scripts/gui/open_gui.sh magic kv_attn_n8` |
| Layout picture | `open designs/kv_attn_n8/output/layout.png` |

Step-by-step guides: [macOS](docs/RUN_ON_MAC.md), [Linux](docs/RUN_ON_LINUX.md),
[Hermes from the terminal](docs/HERMES_FROM_TERMINAL.md), [Hermes desktop and browser UI](docs/HERMES_DESKTOP.md),
[every GUI and log](docs/GUI_AND_LOGS.md),
[Hermes Agent integration and workflow](docs/HERMES_AGENT_INTEGRATION.md).

## The designs

Each name links to its design page (architecture, data flow, every flow step, what was learned).

| Family | Designs |
|---|---|
| Baseline | [user_proj_example](designs/user_proj_example/NOTES.md) |
| Tiny AI engines | [vision_all_lit](designs/vision_all_lit/NOTES.md), [vision_block](designs/vision_block/NOTES.md), [text_sentiment](designs/text_sentiment/NOTES.md) |
| Audio | [audio_pitch](designs/audio_pitch/NOTES.md), [audio_onset](designs/audio_onset/NOTES.md) |
| Multimodal | [image_text_match](designs/image_text_match/NOTES.md) |
| Precision study | [prec_bin](designs/prec_bin/NOTES.md), [prec_tern](designs/prec_tern/NOTES.md), [prec_int4](designs/prec_int4/NOTES.md), [prec_int8](designs/prec_int8/NOTES.md), [prec_fp8](designs/prec_fp8/NOTES.md), [prec_fp16](designs/prec_fp16/NOTES.md), [prec_bf16](designs/prec_bf16/NOTES.md) |
| KV-cache attention | [kv_attn_n4](designs/kv_attn_n4/NOTES.md), [kv_attn_n8](designs/kv_attn_n8/NOTES.md), [kv_attn_n16](designs/kv_attn_n16/NOTES.md), [kv_attn_n8_int4](designs/kv_attn_n8_int4/NOTES.md), [kv_attn_n8_ring](designs/kv_attn_n8_ring/NOTES.md) |
| SoC macros | [tiny_ai_core](designs/tiny_ai_core/NOTES.md), [soc_image_text_match](designs/soc_image_text_match/NOTES.md), [soc_kv_attn_n8](designs/soc_kv_attn_n8/NOTES.md) |
| Caravel wrappers | [user_project_wrapper](designs/user_project_wrapper/NOTES.md), [user_project_wrapper_soc_itm](designs/user_project_wrapper_soc_itm/NOTES.md), [user_project_wrapper_soc_kv](designs/user_project_wrapper_soc_kv/NOTES.md) |

## More

- Results and lessons: [measured results](docs/RESULTS.md) (layout gallery, tables, agent results; locally `make results`), [what it taught us](docs/LESSONS.md) (chip design, EDA, AI, business, open source), [validation of every run](docs/VALIDATION.md), [tests](tests/TEST_MATRIX.md).
- Background: [why this is AI](docs/WHY_AI.md), [architecture](docs/ARCHITECTURE.md), [precision study](docs/PRECISION_STUDY.md), [LLM inference and the KV cache](docs/LLM_INFERENCE.md), [OpenROAD engines](docs/OPENROAD_ENGINES.md).
- SoC and tapeout: [SoC plan](docs/SOC_PLAN.md), [firmware](firmware/README.md), [Caravel simulation](docs/CARAVEL_SIM.md), [precheck](docs/PRECHECK.md).
- Agents: [Hermes agent](docs/HERMES_AGENT.md), [tools](tools/README.md), [KLayout demo](examples/hermes_klayout_demo/README.md), [harness](examples/hermes_harness/README.md), [RAG](examples/hermes_rag/README.md), [KLayout GUI](examples/hermes_klayout_gui/README.md), [OpenROAD GUI](examples/openroad_gui/README.md), [desktop and tool server](examples/hermes_desktop/README.md), [skills](docs/SKILLS.md), [local Grafana dashboards](docs/GRAFANA.md).
- Extending with a coding agent (Claude Code, Codex, Gemini CLI / Antigravity): [AGENTS.md](AGENTS.md).
- Project: [plan and rules](SPEC.md), [sources and licences](provenance/SOURCES.md), [slides](docs/slides/README.md) (from the sibling repository).

Not covered: no tapeout or `cf` account step is automated (owner only); the full-Caravel sims and the precheck ran for
`user_project_wrapper` only; full-chip gate level with SDF needs an x86 host.
