# open-ai-chip

Twenty-five small digital designs (tiny AI engines, a precision study, KV-cache attention, SoC macros and Caravel
wrappers), each taken from RTL to signoff-clean GDSII on sky130A with LibreLane 3.0.2 in Docker, on a laptop. Local
Hermes agents read the results, drive KLayout and OpenROAD, and can hand flows to Claude.

## Quick start

Every step below was run on macOS (Apple Silicon) on 2026-10-07. Linux (Ubuntu/Debian) has its own setup script; see
step 1.

### 1. Install (once)

**macOS** (details and checks for each line: [docs/RUN_ON_MAC.md](docs/RUN_ON_MAC.md) section 2):

```bash
git clone https://github.com/rajaghv-dev/open-ai-chip.git && cd open-ai-chip
brew install icarus-verilog riscv64-elf-gcc colima docker                           # simulator, RISC-V compiler, Docker runtime
colima start -p osl --cpu 6 --memory 16 --disk 60 --vm-type vz                       # the Docker VM this repo uses
python3 -m venv build/agent/venv && build/agent/venv/bin/pip install -r tools/requirements.txt   # agent and test packages
make doctor                                   # checks everything; tells you how to get the LibreLane image and the sky130A PDK
brew install --cask klayout xquartz           # optional: layout windows (KLayout) and Magic/OpenROAD windows (XQuartz)
```

**Linux:** `bash scripts/setup_linux.sh` installs the same pieces (apt packages, Docker, the image, the PDK, the venv),
then run `make doctor`. Details: [docs/RUN_ON_LINUX.md](docs/RUN_ON_LINUX.md).

### 2. Verify

```bash
make doctor          # every check PASS (tools, Docker, LibreLane image, PDK, disk)
make test            # fast gate, no Docker: about 100-125 s, ends with "test: ALL PASSED"
make test-full       # heavier, never starts a physical flow: about 5 min (105 PASS, 282 s on 2026-10-07)
bash scripts/run_all_mac.sh        # or everything in order (tests, sims, signoff, gate level); Linux: bash scripts/run_all.sh
```

`run_all_mac.sh --all` adds flows, precheck, the terminal agents and the GUI windows. Every target is listed by `make help`.

### 3. Explore a design (safe on the frozen designs)

All 25 designs are frozen (`designs/FROZEN.json`): the Makefile refuses `gds`, `collect`, `flow-all` and `flow` on them.
These commands reuse the committed run and change nothing:

```bash
make simulate DESIGN=kv_attn_n8      # RTL simulation, self-checking testbench
make check    DESIGN=kv_attn_n8      # signoff check of the committed run (DRC, LVS, timing, no lost logic)
make gl       DESIGN=kv_attn_n8      # gate-level simulation (add NETLIST=final for the routed netlist)
make view     DESIGN=kv_attn_n8      # summary plus the layout picture
```

- To run the full flow on a frozen design, use a what-if copy under `build/whatif/` (in Hermes: `chip rebuild kv8`).
- For a new design, or one the owner has unfrozen: `make doctor`, `make test`, then `make flow-all DESIGN=<d>`
  (simulate, gds, check, gl, gl-final, collect).
- Unfreezing is owner-only: [designs/FROZEN.md](designs/FROZEN.md).

### 4. The local AI agent: Hermes Agent desktop app (macOS)

The front end is Nous Research's **Hermes Agent** desktop app, connected to this repo over MCP with a `chip` profile:
- local Ollama model `qwen3.5-64k:9b`;
- no file edits, gated runs;
- Claude only via `ask_claude` when you ask.

**Once:**

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash            # Hermes Agent (or the Hermes Desktop download)
brew install ollama && ollama pull qwen3.5:9b && ollama pull qwen3-embedding:0.6b
ollama create qwen3.5-64k:9b -f tools/ollama/qwen3.5-64k.Modelfile             # the 64k-context variant Hermes uses
bash scripts/hermes_agent_setup.sh --demo-tools            # dry run: shows the diff it would make in ~/.hermes; review it
bash scripts/hermes_agent_setup.sh --demo-tools --apply    # backup ~/.hermes, create the chip profile, MCP bridge, skills, hook, cron
hermes profile use chip                                    # make chip the profile Hermes.app opens on
```

**Every time:**

```bash
bash scripts/hermes_start.sh                   # checks and starts Ollama (models loaded and kept in memory), Docker, tool server, then Hermes.app
bash scripts/hermes_start.sh --demo-sessions   # before a class: also nine pinned "Demo N" sessions with the commands to type
```

In Hermes.app:
- Make the chat readable: Settings (**Cmd+,**) → Appearance → **Color Mode: Light**, **Chat Text Size: 150 %**.
- Open a pinned "Demo N" session, or start a new chat (Cmd+N) and type `chip`.

**How to type commands**
- **Without** the `/`, the answer is a full chat reply with tables, 🟢🟡🟠🔴 slack colours and KLayout pictures (a
  few seconds). With `/`, the answer is instant but shown as a small grey line.
- Partial names work; an ambiguous one asks with numbered choices (`timing audio`, then `2`).

| Do | Type |
|---|---|
| See all designs, signoff, numbers | `designs`, `signoff caravel kv`, `timing kv_attn`, `synth vision lit`, `drc kv8`, `lvs prec bf16`, `compare kv4 kv8 kv16` |
| Open and operate a layout | `klayout kv_attn show only met1`, `layout zoom to the lower-left 50 um`, `drc kv8 live`, `magic vision lit`, `chip close` |
| Search and explain | `search hold violation wrapper`, `ask why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8?` |
| Experiments and what-ifs (start at once) | `chip experiment soc-kv`, `chip whatif vision lit CLOCK_PERIOD=5`, `chip rebuild kv8`, then `jobs`, `result <tag>` |
| Agent loops and harnesses | `loopdemo signoff kv`, `harness facts kv` |
| All commands, demo cards | `chip`, `demos` |

Plain questions go to the local model: "Which kv design has the most flip-flops?", "What if the clock were 20 ns for
vision_block?". A run asked for in a sentence starts only after your `yes, run <id>`.

Every integration with copy-paste examples: [hermes-agents.md](hermes-agents.md). Class script:
[docs/HERMES_CLASS_SHOWCASE.md](docs/HERMES_CLASS_SHOWCASE.md). Fifteen narrated demos: [docs/HERMES_DEMOS.md](docs/HERMES_DEMOS.md).
Safety and status: [docs/HERMES_AGENT_INTEGRATION.md](docs/HERMES_AGENT_INTEGRATION.md). Undo the setup:
`bash scripts/hermes_agent_setup.sh --uninstall --apply`.

Older front ends (Open WebUI with `make hermes` / `make demo*`, the repo's wrapper app, terminal agent loops) remain in
the repo unmaintained. What is incomplete is listed in [SPEC.md](SPEC.md#agent-front-ends-decision-2026-10-07) and
[docs/HERMES_DESKTOP.md](docs/HERMES_DESKTOP.md).

### 5. Extend the repo with a coding agent

Claude Code, Codex, Gemini CLI or Antigravity: start from [AGENTS.md](AGENTS.md) (rules, where to extend, recipes,
lessons learned). Before any commit: `make test`.

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
- Licence: Apache-2.0 ([LICENSE](LICENSE), [NOTICE](NOTICE)); third-party parts and their licences: [provenance/SOURCES.md](provenance/SOURCES.md).
- Project: [plan and rules](SPEC.md), [sources and licences](provenance/SOURCES.md), [slides](docs/slides/README.md) (from the sibling repository).

Not covered: no tapeout or `cf` account step is automated (owner only); the full-Caravel sims and the precheck ran for
`user_project_wrapper` only; full-chip gate level with SDF needs an x86 host.
