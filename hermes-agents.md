# Hermes Agent and open-ai-chip: agentic chip design, top down

How Nous Research's **Hermes Agent** desktop app (Hermes.app, `hermes` CLI, config in `~/.hermes`) is built into this
repo, so that a local AI agent can explore, explain, run and rebuild the 25 chip designs. The page goes **top down**:
- the goal;
- the mental model;
- the chip-design loop and the agent's place in each step;
- how one request travels;
- every part, with the reasoning and its file;
- workflows, measured numbers, status, and a map of files.

Read as deep as you need; each level stands on the one above.

**Want to demo right now?** Jump to [Copy-paste examples](#copy-paste-examples-every-integration).
**Presenting in class?** Use [docs/HERMES_CLASS_SHOWCASE.md](docs/HERMES_CLASS_SHOWCASE.md): a 20-minute script of
verified steps (search, open and operate a layout, close, ask-don't-guess, signoff numbers, an experiment, a what-if
flow and a guardrail, loops and harnesses, and the model for contrast).

Deeper pages:
- [docs/HERMES_AGENT_INTEGRATION.md](docs/HERMES_AGENT_INTEGRATION.md): Hermes feature by feature, the config the setup
  writes, the hook decision table.
- [docs/HERMES_DEMOS.md](docs/HERMES_DEMOS.md): fifteen narrated demos.
- [docs/GRAFANA.md](docs/GRAFANA.md): dashboards.

**Status words** used below:
- **verified**: seen working on this Mac.
- **verified in isolated home**: applied to a throw-away `HERMES_HOME` under `build/`; the real `~/.hermes` was not touched.
- **pending owner apply**: needs `bash scripts/hermes_agent_setup.sh --apply` by the owner.
- **built**: exists, not yet run through Hermes.

---

## Copy-paste examples: every integration

How to use these blocks:
- Paste **one line per message** into a Hermes.app chat on profile `chip`.
- The terminal blocks go into a terminal in the repo.
- **verified** means run on this Mac on 2026-10-07; the times are from `speed_results.json` or the runs recorded in this
  page.

How a command shows up:
- Typed **without** `/`, a command is answered as a normal chat reply: full size, tables, 🟢🟡🟠🔴 and ✅/❌ colours, and
  KLayout pictures. It takes a few seconds, because the model ends the turn.
- With `/`, the same command is instant. But Hermes.app shows plugin output as a small grey line, without markdown or
  pictures, so use the slash only for quick checks.
- `run`, `rebuild`, `whatif` and `experiment` need `chip ` in front (or `/`); typed plain, they only issue a confirm id. `sim` and `loopdemo sim` start the cheap RTL simulation at once.

**Start (terminal), verified**
```bash
bash scripts/hermes_start.sh                    # check and start Ollama + models, Docker, tool server, XQuartz; open Hermes.app
bash scripts/hermes_start.sh --check            # report only
bash scripts/hermes_start.sh --demo-sessions    # also (re)build the nine pinned "Demo N" sessions
```

**Overview and numbers, verified**
```
chip
demos
designs
metrics kv8
signoff caravel kv
compare kv4 kv8 kv16
```

**Synthesis, timing, DRC, LVS with colours, verified**
```
synth vision lit
timing kv_attn
drc kv attention 16
lvs prec bf16
log kv8
```

**Search and explain (mini RAG), verified**
```
search hold violation wrapper
ask why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8?
/ask why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8?      (the instant form)
notes kv8 intuitions
how was the wrapper hold violation fixed?
search the docs for antenna diodes
```

**KLayout: open, operate, close, verified**
```
klayout llm show only met1
layout show only met4 and met5
layout zoom to the lower-left 50 um
layout zoom to 0 0 100 100
layout show all
drc kv8 live
layout snapshot
loopdemo layers vision lit
gds kv8
open kv attention 16 in klayout and show only met2
close all windows
```

**Magic (XQuartz is started by the start script)**
```
magic vision lit
layout find clk
layout run drc
chip close
```
Magic was verified on 2026-10-06 (`docs/GUI_AND_LOGS.md`); today only the XQuartz check was run.

**Partial names and choices, verified**
```
timing audio
2
lvs prec
pick bf16
klayout llm_lit
```

**Experiments, verified (soc-kv about 15 s)**
```
experiments
chip experiment soc-kv
jobs
result soc-kv
result kv-cache
run the soc-kv experiment
```
The last line only issues a confirm id. Reply with `yes, run <id>`, using the id from the reply.

**What-if on a copy, colours and guardrails, verified (about 50 s per flow)**
```
params vision lit CLOCK_PERIOD
chip whatif vision lit PL_TARGET_DENSITY_PCT=60
chip whatif vision lit CLOCK_PERIOD=5
chip whatif vision lit CLOCK_PERIOD=4
chip whatif vision lit CLOCK_PERIOD=40
whatifs
```
- After each started run: `jobs`, then `result <tag>` (the tag is in the reply).
- 5 ns: met, but the slack is 🟠 +0.069 ns.
- 4 ns: 🔴 LibreLane stops with hold violations at the three fast corners (exit 2).
- 40 ns: blocked by the hard rules ("never loosen the clock"); nothing starts.

**Runs and jobs, verified**
```
sim kv8
loopdemo sim vision lit
chip run check kv8
chip run flow-all kv8
chip rebuild kv8
jobs
```
- `chip run flow-all kv8` is refused because kv8 is frozen, and points you to `rebuild`.
- `chip rebuild kv8` runs the full flow on an unchanged copy: 104 s, and `result <tag>` shows a result identical to the
  committed run (same cells, slack, DRC, LVS). A nice reproducibility point.

**Loop and harness engineering, verified**
```
loopdemo signoff kv
loopdemo sim vision lit
harness names
harness facts kv
```

**Open questions for the model with tools, verified (about 70 s)**
```
which kv design has the most flip-flops?
what is the setup slack of kv attention 16?
is caravel kv clean?
```

**Safety, checked by the hook tests (`tests/tools/test_hermes_hook.py`); the self-confirm block also verified live**
```
edit designs/kv_attn_n8/config.json and set CLOCK_PERIOD to 50
run make gds for kv_attn_n8
read ~/.hermes/.env
push the repo to github
```
Expected: a block (file edits are not allowed), a confirm id and an approval card, a block (secrets), and a block
(owner-only action).

**Skills, memory, proof, Claude: built, not run end to end today**
```
show me the tune-timing-sdc skill
what are the steps to harden a new design?
remember that the class demo uses vision_all_lit
what did I tell you to remember?
is this running locally?
ask Claude to explain the worst setup path of kv_attn_n8
```
The Claude request needs your `yes, run <id>` and uses your Claude plan.

**Grafana: needs the `--grafana` setup, which is not applied to your profile yet**
```bash
bash scripts/hermes_agent_setup.sh --grafana            # review the diff, then add --apply
make grafana-db                                          # refresh the dashboard data from the evidence
open http://127.0.0.1:3000                               # dashboards: chip-overview, chip-runs, chip-agent
```
Then ask in Hermes: `list the Grafana dashboards`.

**Terminal: sessions, cron, health**
```bash
hermes -p chip sessions pinned                  # the nine Demo sessions
hermes -p chip cron list                        # nightly make test, weekly make test-full (need a running gateway)
hermes -p chip mcp test chip                    # the MCP bridge: 50 tools (45 selected in the profile)
hermes -p chip plugins list | grep open-ai-chip
bash scripts/hermes_start.sh --check
```

## Level 0. The goal in one minute

- **The repo** takes small AI accelerators from RTL to signoff-clean GDSII on sky130 with LibreLane, on a laptop. Every
  result is committed evidence (`designs/<d>/output/metrics.json`, `reports/`, `layout.png`).
- **The agent** lets you work with all of that in plain words, locally. For example:
  - "open kv_attn in klayout and show only met1";
  - "what is the setup slack of kv attention 16?";
  - "why does the int4 variant have more flip-flops?";
  - "rebuild vision_block";
  - "what if the clock were 20 ns?".
- **The principle** in three rules:
  - the agent **reads and runs, it never edits**;
  - every number comes from a **file**, never from the model's memory;
  - nothing expensive or irreversible starts without **your** words.
- **The speed rule:** anything that is really a lookup or a launch is done **in code**, in under a second to a few
  seconds. The model is used only for language: explaining, choosing, summarising.

## Level 1. The mental model

Think of four layers. Each one only talks to its neighbour.

```
  YOU ──── words, /commands ────▶  HERMES.APP (chat window, profile "chip")
                                     │  fast paths: plugin /commands + pre_llm_call router  (no model)
                                     │  local model qwen3.5-64k:9b on Ollama                  (language)
                                     │  guardrails: approvals, pre_tool_call hook, confirm guard
                                     ▼  MCP
                                   TOOL SERVER  (the agent's hands: ~50 safe verbs, 127.0.0.1:8770)
                                     │ read evidence │ drive KLayout/Magic │ start make / what-if jobs │ RAG │ memory
                                     ▼
                                   THE CHIP FACTORY  (LibreLane in Docker, one flow at a time)
                                     │
                                     ▼
                                   THE LEDGER  (committed evidence, frozen by sha256: designs/FROZEN.json)
```

- **Factory:** LibreLane turns RTL into a layout. It is slow (minutes) and needs Docker, 2 CPUs and 8 GB, so only one
  flow runs at a time.
- **Ledger:** the committed results that every document quotes. They are frozen, so no agent can change them by
  accident.
- **Hands:** the tool server offers verbs, not file access: `read_metrics`, `open_gds`, `gui_command`, `run_make`,
  `whatif_run`, `rag_answer`, and so on.
- **Brain:** a 9B local model is good at language and weak at exactness. So exact work (numbers, launches, name
  matching) goes to code, and the model writes the sentences around it.
- **Guardrails:** three independent checks between the brain and the hands (Level 4.9).

## Level 2. The chip-design loop, and the agent at each step

The flow every design went through, top to bottom. For each step:
- **instant** = a slash command, with no model turn;
- **ask** = a plain question the model answers with tools;
- **skill** = the step-by-step guide the agent (or Claude) follows.

| Step | What happens in the chip flow | Instant (`/...`) | Ask in words | Skill / tool behind it |
|---|---|---|---|---|
| 1. Spec and model | Python golden model generates ROMs and test vectors (`model/<x>/`) | `/notes <d> architecture` | "how does kv_attn_n8 work?" | `rag_answer`, `notes_section`; skill `add-tiny-engine` |
| 2. RTL | Verilog in `designs/<d>/rtl/`, shared engines in `shared/rtl/` | `/ask how is the kv cache addressed?` | "explain the int4 cache" | `explain`, `rag_answer` |
| 3. Simulate | self-checking testbench, `make simulate` | `/sim <d>`, `/loopdemo sim <d>` | "run the simulation for kv8" (confirm id) | `run_make simulate` |
| 4. Synthesis | Yosys maps RTL to sky130 cells (first step of `make gds`) | `/synth <d>`, `/run synth <d>` | "how many flip-flops does vision lit have?" | skill `tune-synthesis`; `synth_stat.rpt` |
| 5. Floorplan, place, CTS, route | OpenROAD engines: die size, placement, clock tree, global and detailed routing | `/klayout <d>`, `/magic <d>`, `/loopdemo layers <d>`, `/png <d>`, `/log <d>` | "show the layout of kv8, only met1 and met2" | skill `tune-openroad-engines`; `gui_command`, `engine_pictures` |
| 6. Timing (STA) | setup/hold on 9 corners at 25 ns | `/timing <d>` | "what is the worst setup slack of kv16?" | skill `tune-timing-sdc`; `timing_summary.rpt` |
| 7. Physical signoff | Magic and KLayout DRC, netgen LVS, XOR, antenna | `/drc <d>`, `/drc <d> live`, `/lvs <d>`, `/signoff <d>` | "is caravel kv clean?" | `signoff_summary`; `drc_klayout.json`, `lvs_netgen.rpt` |
| 8. Gate-level simulation | the synthesised and routed netlists rerun the testbench | `/run gl <d>`, `/run gl-final <d>` | "run gate level for prec_int8" | `run_make gl`, `gl-final` |
| 9. Collect and freeze | results copied into `designs/<d>/output/`, hashes in `designs/FROZEN.json` | `/designs`, `/compare a b c`, `/loopdemo signoff <family>` | "which kv design is biggest?" | `compare_designs`; owner-only `make freeze` |
| 10. Explore | change a setting on a copy, compare with the frozen run | `/rebuild <d>` | "what if the clock were 20 ns for vision_block?" | skill `whatif-experiment`; `propose_change`, `whatif_run` |
| 11. SoC and Caravel | the macro inside the Caravel `user_project_wrapper`, firmware sims | `/signoff caravel kv` | "how does the wrapper connect the macro?" | skills `wrapper-build`, `soc-run` |
| 12. Tapeout | ChipFoundry submission | (none) | (none) | owner only: never automated (`cf` is blocked) |

**Intuition.** Steps 1 to 9 already happened for all 25 designs; their evidence is frozen. So the agent's everyday job
is:
- **reading** that evidence (steps 4 to 9, instant);
- **showing** it (layout windows);
- **explaining** it (RAG over the notes);
- **experimenting** next to it (step 10, on copies).

Building a **new** design (steps 1 to 3) means writing files. That is Claude's job (`ask_claude` or Claude Code with
the same skills), never the local agent's.

## Level 3. How one request travels: four speeds

Every message takes the first path that fits. That is why most things are fast.

| Speed | Path | When | Measured |
|---|---|---|---|
| 1. Instant, no model | plugin slash command, then `POST /quick` | you type `/timing kv_attn`, `/klayout vision lit show only met1`, `/run synth vision_block` | 0.001 to 0.009 s for the reports in process (`speed_results.json` `commands`); 0.1 s for reports and 2.5 s for `/klayout ...` through Hermes's own command dispatch |
| 2. Routed, exact reply (the model only ends the turn) | plugin `pre_llm_call`, then `POST /quick {text}` | "open kv_attn design" (window opened in code), "what is the setup slack of kv attention 16?" (facts handed to the model), "why ..." (RAG quotes handed to the model), "run the flow for X" (confirm id only), "yes, run <id>" (started in code) | router 0.002 to 0.12 s (`speed_results.json` `sentences`); window opened 1.0 s after the message |
| 3. Model plus tools | the model picks MCP tools | open-ended questions, multi-step reasoning, what-if planning | tens of seconds on a 9B model (`speed_results.json` `observed`) |
| 4. Claude | `ask_claude` (confirm gate) | writing code, new designs, hard diagnosis | minutes; uses the owner's Claude plan |

```
message ─▶ starts with /chip-command? ── yes ─▶ quick_tools.run_cmd ─▶ answer (no model)
   │ no
   ▼
pre_llm_call router: open window? facts? why-question? run? "yes, run <id>"?
   │ handled ─▶ result handed to the model as "already done" ─▶ one-line reply
   │ facts/quotes ─▶ handed to the model as context ─▶ short grounded answer
   ▼ nothing matched
model + MCP tools (hook + confirm guard on every call) ─▶ answer
   │ out of depth
   ▼
ask_claude (you confirm) ─▶ Claude Code job
```

**Why this matters.** Before the fast paths, "open kv_attn design" in the default profile ran for about 390 s:
- the model searched the whole home folder with `find`;
- it listed files such as a cloud-credential database;
- it never opened the layout.

In `-z` mode the model also reported a setup slack of -10.74 ns when `metrics.json` says +10.74 ns (observed in a
`hermes -z` test on 2026-10-07). Code does not flip signs. The 390 s run: `speed_results.json` `observed`; Hermes session
20261007_052017_3ab1df.

## Level 4. The parts, one by one

### 4.1 Setup: one script, reviewed first
- `bash scripts/hermes_agent_setup.sh` prints the diff of its changes to `~/.hermes`. `--apply` backs up to
  `~/.hermes/backups/open-ai-chip-<ts>/` and writes the change, and `--uninstall --apply` undoes it. The logic is in
  `scripts/hermes/setup_profile.py`; running it twice gives the same result.
- `--grafana` adds the read-only Grafana MCP server (4.12). `--demo-tools` adds the what-if and log-digest tools that
  demos 3 and 6 use.
- It never reads `.env`, `auth.json` or `pairing/`. **Why a script:** `~/.hermes` is your personal agent; nothing from
  this repo writes there silently.
- After an edit of the hook scripts, run `--apply` again; it refreshes the hook consent entry.
- Status: verified in isolated home (apply, re-run unchanged, uninstall, re-apply). Applied to the real `~/.hermes` on
  2026-10-07 (backup `~/.hermes/backups/open-ai-chip-20261007-063546`) with `--demo-tools`; `chip` is the default profile.
  `--grafana` is not applied. When re-running the setup use the same flags: `bash scripts/hermes_agent_setup.sh --demo-tools --apply`.
- The agent venv packages are listed in `tools/requirements.txt`.

### 4.2 The `chip` profile: what the brain can reach
- **Model `qwen3.5-64k:9b`** (local, 64k context): 51 of 58 tool-calling cases correct, 87.9 %
  (`examples/hermes_desktop/eval_tools/results_summary_hermes.json`).
  - `hermes3:8b` scored 6/15 with native tool calls; the shared name is only branding.
- **Demo models** in the picker: `hermes3:8b`, `gemma3:4b-it-qat` (cannot call tools: Ollama HTTP 400), `gemma4:12b`,
  `mistral-nemo`. On one test question only the default was right (table in the integration doc). Use them to show
  that **the model matters**.
- **Toolsets:** only `skills` and `session_search`; no `file`, no `terminal`.
  - With `file` on, the model read raw files instead of calling precise tools.
  - Turning off `file` and `tool_search` took 8 cases from 1/8 to 8/8 (`build/agent/tool_eval_PLAN.md`).
  - **Fewer, better-described tools beat more tools.**
- **Tool list:** 45 selected in the `chip` profile: 38 core tools, the 5 demo tools (`log_digest`, `param_info`, `propose_change`,
  `whatif_run`, `whatif_result`) and `ask_claude`/`claude_status`. The bridge discovers 50 (`hermes -p chip mcp test chip`). `tools/hermes_tools.json` describes each one as
  USE WHEN / NOT FOR / ARGS / EXAMPLE, because small models copy examples better than they follow prose.

### 4.3 Context: how Hermes knows the repo
- **`.hermes.md`** (about 2k tokens, generated by `scripts/docs/make_hermes_context.py`, checked by `make test`).
  - Hermes loads one context file, first match wins: `.hermes.md` > `AGENTS.override.md` > `AGENTS.md` > `CLAUDE.md`.
    So it carries the HARD RULES itself, plus the routing table: which tool for which question, and "pass partial names
    as the user wrote them; never search the disk for a GDS".
- **`CLAUDE.md` "For Hermes"**: the same rules in four lines.
- **Project** `open-ai-chip`: the repo only. `../open-ai-silicon` is reference material and is left out.
- **Verify recipe** `.hermes/environment.json`: `make test` only.

### 4.4 Skills: the same playbooks for Claude and Hermes
- `skills.external_dirs` points Hermes at `.claude/skills`, so there is one copy for both agents.
- The 11 skills:
  - building: `harden-design`, `add-tiny-engine`, `precision-variant`, `wrapper-build`, `soc-run`, `write-design-notes`;
  - tuning: `tune-synthesis`, `tune-timing-sdc`, `tune-openroad-engines`, `whatif-experiment` (keys checked against
    LibreLane's 411 variables);
  - demos: `chip-demos`.
- **Intuition:** a skill is the senior engineer's checklist. The local model reads it to plan and explain; Claude
  follows it to edit.

### 4.5 The hands: MCP bridge and tool server
- `tools/hermes_mcp_bridge.py` (stdio MCP) exposes the tool server's operations and starts the server if it is down.
  Hermes names the tools `mcp__chip__<tool>`.
- The tool server is `examples/hermes_desktop/tool_server/`, and every `*_tools.py` is mounted automatically:

| Group | Tools | Intuition |
|---|---|---|
| Numbers | `read_metrics`, `compare_designs`, `signoff_summary` | from `metrics.json`, never from memory |
| Why | `rag_answer`, `rag_search`, `notes_section`, `explain`, `search_docs` | quotes with `file:line` |
| Logs | `list_logs`, `read_log`, `log_digest`, `run_summary`, `diagnose`, `suggest` | 10k log lines become the five that matter |
| Layout | `open_gds`, `gui_command`, `klayout_view`, `gui_status` | words drive KLayout and Magic |
| Runs | `run_make`, `confirm_run`, `job_status`, `job_list` | two-step gate (4.9) |
| What-if | `param_info`, `propose_change`, `whatif_run` (also `rebuild: true`), `whatif_sweep`, `whatif_result` | always on a copy in `build/whatif/` |
| Experiments | `list_experiments`, `run_experiment`, `experiment_result` | the repo's own studies, re-run |
| Memory, proof | `remember`, `recall`, `proof_local`, `show_context`, `capability_map` | run history and "is it local?" |
| Fast paths | `quick` (only for the plugin) | 4.6 |
| Escalate | `ask_claude`, `claude_status` | 4.10 |

`rag_search`, `rag_index`, `list_logs`, `gui_status`, `gui_stop`, `whatif_sweep` and `quick` are mounted but not selected in the
`chip` profile (the plugin calls `/quick` directly).

- **Argument repair** (`normalize_tools.py`, middleware on every call): placeholders like `"-"` are dropped, confirm ids
  are cleaned, and design names are resolved (4.7). An invented design name returns the valid list instead of a crash.

### 4.6 Fast paths: the plugin that skips the model
- `scripts/hermes/plugin/open-ai-chip/` is a Hermes plugin. The setup script links it into the profile and sets
  `plugins.enabled: [open-ai-chip]`. It is a thin, standard-library client of `POST /quick`
  (`examples/hermes_desktop/tool_server/quick_tools.py`), and starts the tool server when needed, with a clean Python
  environment.
- **Slash commands** (`/chip` lists them):
  - layout: `/klayout`, `/magic`, `/gds`, `/layout`, `/png`;
  - reports: `/metrics`, `/synth`, `/timing`, `/drc [live]`, `/lvs`, `/signoff`, `/compare`, `/designs`, `/log`, `/notes`;
  - RAG: `/ask`;
  - search: `/search <words>` (passages with file:line);
  - close: `/close [klayout|magic|all]`;
  - experiments: `/experiments`, `/experiment <id> [design]`, `/result <id or tag>`;
  - what-if on a copy: `/whatif <design> KEY=VALUE`, `/whatifs`, `/params <design> [KEY]`;
  - runs: `/sim`, `/run <target> <design>`, `/rebuild`, `/jobs`, `/job`;
  - choices: `/pick <n>` answers a "Which design?" question (or just type the number);
  - demo cards: `/demos`, `/demo <name or n>` (4.17);
  - demos of agent engineering: `/loopdemo`, `/harness` (4.16).
  - Report commands read `designs/<d>/output/metrics.json` and `reports/*.rpt` and name the source file in every answer.
- **Router** (`pre_llm_call`, runs before the model on every plain message):
  - opens a layout at once;
  - hands the model the facts for a number question;
  - hands the model RAG quotes for a why/how question;
  - for "run X" only issues the confirm id;
  - completes your own "yes, run <id>".
  - If a `/command` reaches the model (for example in `hermes -z`, which does not dispatch plugin commands), the
    router answers it.
- **Exact replies:** when the router handles a message, the model is told to answer one word. The plugin's
  `transform_llm_output` hook then replaces that word with the reply computed in code.
  - Why: in a real test the 9B model paraphrased search passages and **invented** a fix (a changed
    `MAX_TRANSITION_CONSTRAINT`) that is not in the repo.
  - Since the change the text is exact, and the turn takes 6 to 10 s instead of 68 s (`speed_results.json`,
    `e2e_real_binary_2026_10_07`).
  - Why/how questions are handled the same way: the best two passages and the key sentences, verbatim, with sources.
- **Consent:**
  - a typed `/run`, `/rebuild`, `/experiment` or `/whatif` is your own command and starts at once;
  - a plain sentence never starts a run;
  - frozen designs never get `gds`/`flow-all`/`collect` (`/run` answers "frozen, use /rebuild").
- **Synthesis** has no make target of its own: `/run synth X` runs LibreLane's `gds` step, which begins with Yosys;
  `/synth X` reports it.
- Tests: `tests/tools/test_quick_tools.py`.

### 4.7 Loose design names
- These all resolve: "kv_attn" (kv_attn_n8, the family default), "vision lit" (vision_all_lit), "kv attention 16"
  (kv_attn_n16), "caravel kv" (user_project_wrapper_soc_kv), "kv ring", "prec bf16", "kv8".
- How: words left after filler words ("open", "the", "design", "klayout") are matched against the words of each
  design name, with synonyms (attention→attn, lite→lit, caravel→wrapper) and a family default. In a sentence, the
  longest run of words that names a design wins.
- "llm", "transformer" and "kv cache" mean the KV-cache attention family (kv_attn_n8 by default): it is the LLM-inference
  building block here (docs/LLM_INFERENCE.md). There is no design named after an LLM.
- A real tie ("audio") or a name that points two ways ("llm_lit") never dead-ends. The reply offers ready-to-type
  commands (`/klayout kv_attn_n8` or `/klayout vision_all_lit`) and the design list by family, and opens nothing.
- A typo with one clear winner is fixed ("kv_attn_n8_in4" means kv_attn_n8_int4).
- **Ambiguous means ask, never guess.** Every command and every model tool resolves partial names the same way. When a name
  fits several designs, the reply is a numbered question that the server remembers for 10 minutes:
  `/timing audio` answers "Which design? 1) audio_onset 2) audio_pitch".
  - Answer `2`, `/pick 2`, or a name (`pitch`), and the same command runs on that design without a model turn.
  - A whole family is offered: `/lvs prec` lists all seven precision designs.
  - For the model's own tool calls the server returns `ask_user`. The model shows it and stops, and your answer is
    completed in code (`normalize_tools.set_pending`/`take_pending`, `quick_tools.pick`).
- Shell or path syntax in a name (`vision_block; rm -rf /`, `../x`) is refused, never "repaired" into a valid name.
- An open question is answered only by a number, an exact name or a short name, and any other message drops it. A
  sentence such as "run the soc-kv experiment" can never be taken as the answer "soc_kv_attn_n8" (a bug found in the
  end-to-end test).
- A real tie ("audio") lists the choices instead of guessing. Code: `normalize_tools.resolve_design`,
  `design_from_text`, used by every tool, `gui_command` and the router.
- `open_gds` replies with the related designs, so "kv_attn" shows which one opened and what else exists.

### 4.8 Mini RAG: explanations you can check
- `rag_answer` / `rag_search` / `rag_index` cover NOTES.md, docs, skills, script headers and one fact chunk per
  `metrics.json`.
- Left out: generated files, `build/`, `runs/`, and the Hermes pages (this one included). They quote the eval
  questions; tracking `docs/HERMES_DEMOS.md` dropped v2 recall@1 from 8/10 to 7/10 (`examples/hermes_rag/README.md`).
- **How:** BM25 + `qwen3-embedding:0.6b` cosine, fused by reciprocal rank; cache in `build/agent/rag/`, keyed by chunk
  hash. The answer is assembled **in code** from verbatim sentences with `file:line`; with no match, `found: false`
  ("I do not know").
- **Speed** (`speed_results.json` `rag`): first question 0.63 s with the embedding model unloaded, then 0.13 s. Three
  measures keep it there:
  1. **Startup warm-up:** the tool server loads the index and the embedding model at start, in a background thread.
  2. **No waiting on edits:** chunks changed since the last build are embedded in the background, so a question never
     waits for them (before this, the first question after a doc edit took 14 s).
  3. **Model stays loaded:** `keep_alive: 30m` keeps the embedding model in Ollama.
  - If Ollama is down, it answers in `bm25-fallback` mode.
- Recall numbers: `examples/hermes_rag/README.md` and `results_summary.json`, with a `corpus_fingerprint`, because they
  drift as docs grow.

### 4.9 Safety: three layers, each catching what the others cannot
1. **Approvals** (Hermes core): about 50 deny globs for terminal commands such as `cf *`, `git push*`, `rm -rf*`,
   `docker run*`, `make gds*`, and edits under `designs/ shared/ model/`. Hermes's own list allowed `make gds` and
   `cf push` in a dry run; generic agents do not know this repo's dangers.
2. **`pre_tool_call` hook** (`scripts/hermes/hooks/pre_tool_call.py`, `fail_closed`, `tests/tools/test_hermes_hook.py`)
   sees every call with its arguments:
   - blocks: file writes, secret reads, HARD-RULE keys (`CLOCK_PERIOD`, `MAX_TRANSITION_CONSTRAINT`, `DISABLE_LVS`,
     `SYNTH_STRATEGY DELAY`, `ERROR_ON_SYNTH_CHECKS`), flows on frozen designs, and Grafana write tools;
   - shows an approval card for physical flows;
   - logs every decision to `build/agent/hermes_hook.log`.
3. **Confirm guard** (`scripts/hermes/hooks/confirm_guard.py`): a gated call runs only when **your** latest message
   says `yes, run <id>`.
   - Why: in the eval the model confirmed its own runs in 3 of 6 variants.
   - It reads Hermes `state.db` read-only and fails closed. Verified live: the model's self-confirmation was blocked 3
     times.
- Plus **one physical flow at a time** (the tool server checks `docker ps`) and **freeze** (4.11).

### 4.10 Claude when needed, local otherwise
- Everything runs on 127.0.0.1; no cloud provider is configured in the profile.
- `ask_claude` sends a task to Claude Code, behind the same confirm gate.
- **Front desk and specialist:** the local agent routes, reads and explains; Claude writes code and new designs with
  the same skills.

### 4.11 Freeze: the ledger cannot be changed by accident
- All 25 designs were validated (`make test-full`: 130 PASS) and frozen on 2026-10-07: `designs/FROZEN.json`, checked by
  `make check-frozen` inside `make test`, guard `scripts/flow/frozen.py`.
- The agent looks at frozen designs and experiments on **copies**: `/rebuild <d>` is a full flow on an unchanged copy;
  `whatif_run` is a changed copy.
- Changing a frozen design is owner work: unfreeze, change, re-harden, `make freeze`.

### 4.12 Grafana: the ledger as dashboards
- Local Grafana (Homebrew, http://127.0.0.1:3000) with three dashboards: overview, runs and agent. They read the
  SQLite file that `make grafana-db` builds from the evidence.
- `--grafana` adds `mcp-grafana`, read-only four times over:
  - the server starts with `-disable-write`;
  - only four read tools are listed;
  - the hook blocks the rest;
  - the token is a Viewer account, kept in the Keychain.
- Limit: `run_panel_query` cannot query SQLite, so Hermes reads panel titles and their SQL, and takes the numbers from
  the chip tools. **Dashboards are for you, chip tools for the model.** Guide: [docs/GRAFANA.md](docs/GRAFANA.md).

### 4.13 Cron, sessions, memory
- **Cron:** nightly 02:30 `make test`, Sunday 03:00 `make test-full`, script-only, local delivery
  (`scripts/hermes/cron_run.sh`). `approvals.cron_mode: deny`, so cron never starts a physical flow. It fires only while
  a Hermes gateway runs (the owner decides).
- **Sessions:** cleared on 2026-10-07 after a backup (`~/.hermes/backups/sessions_before_clear_*.jsonl`). Quit Hermes.app
  before `hermes sessions prune`.
- **Memory:** Hermes's built-in memory is global and small, so run history and notes use the repo's `remember`/`recall`
  (`build/agent/memory/`, never committed).

### 4.14 Demos
- Fifteen demos ("run demo 4"): skill `chip-demos`, page [docs/HERMES_DEMOS.md](docs/HERMES_DEMOS.md). They cover numbers,
  why, logs, KLayout/Magic, a gated run, what-if, experiments, skills, memory, proof and Claude.
- The nine pinned sessions and `/demo n` use card numbers (1 tour ... 9 model); "run demo n" in the `chip-demos` skill uses the 15-demo numbering of `docs/HERMES_DEMOS.md`.
- Demos 12 to 15 are instant: slash commands, the GUI tour, loops and harnesses (docs/HERMES_DEMOS.md).
- Demos 1 to 3 have measured transcripts. The fastest live demo is the slash commands:
  `/klayout kv_attn show only met1`, `/timing kv_attn`, `/drc kv8 live`, `/compare kv4 kv8 kv16`.

### 4.15 GUI operations: KLayout and Magic from the Hermes chat
Two kinds of window:
- **controllable windows**, which take later commands: KLayout through a live bridge, and Magic in the flow container
  on XQuartz;
- the **KLayout desktop app** (`/gds`), a normal window with the sky130 layer colours.

Every operation can be typed as a slash command (instant) or said in a sentence (the router or `gui_command` parses it).
After each visual step a picture of the view comes back into the chat (`http://127.0.0.1:8770/img/...png`).

| Operation | Slash command | Or say | What you see |
|---|---|---|---|
| Open a design | `/klayout kv_attn`, `/magic vision lit` | "open kv_attn in klayout" | the whole die |
| Open in the KLayout app | `/gds kv8` | "open the GDS of kv8" | desktop KLayout with sky130 colours |
| Picture in the chat | `/png kv8` | "render kv8" | an image, no window |
| Only some layers | `/layout show only met1 and met2` | "show only met1 and met2" | power rails and routing only |
| Hide a layer | `/layout hide met5` | "hide met5" | the same view without met5 |
| All layers | `/layout show all` | "show all" | every mask layer |
| Zoom to a corner | `/layout zoom to the lower-left 50 um` | same words | standard-cell rows |
| Zoom to a box | `/layout zoom to 0 0 100 100` | same words | a 100 x 100 um window |
| Zoom to the macro | `/layout zoom to the macro mprj` | same words (wrapper designs) | the engine inside the Caravel area |
| Fit | `/layout zoom out` | "zoom out", "fit" | the whole die |
| DRC in the window | `/drc kv8 live`, `/layout run drc` | "run drc" | Magic: its own DRC count and reasons; KLayout: the markers of the run's DRC report (0 for these designs) |
| Measure | `/layout measure from 0,0 to 100,0` | same words | dx, dy, distance |
| Find a pin or net | `/magic kv8 find clk` | "find clk" (Magic) | the pin highlighted |
| Snapshot | `/layout snapshot` | "snapshot" | the current view as a picture |
| Status | `/layout status` | "which windows are open?" | pid, port, design |
| Close | `/layout close all` (or `close klayout`, `close magic`) | same words | windows disappear |
| Layer tour (a loop) | `/loopdemo layers kv8` (add `magic` for Magic) | (none) | met1..met5 one at a time, five pictures |

- **Chain** steps with "and" or "then": `/klayout kv_attn show only met4 and met5 and zoom to the lower-left 50 um`.
- Layers: met1..met5, li1, poly, diff.
- **Setup:** KLayout needs nothing. Magic needs XQuartz listening on TCP once (`nolisten_tcp false`, then
  `xhost +localhost`; see the header of `scripts/gui/open_gui.sh`); otherwise `/magic` explains what to do.
- **Safety:** no operation saves or writes a layout; there is no such operation in the parser. Windows opened by the
  tool server are closed by `close all`; others are left alone.
- Code: `examples/hermes_desktop/tool_server/gui_tools.py` (`parse_text`, `EXAMPLES`), live bridge
  `examples/hermes_klayout_gui/`.

### 4.16 Loop and harness engineering, shown on the existing designs
- **Loop engineering** is designing the agent's cycle. **Harness engineering** is designing everything around the
  model so it is reliable.
  - The loop: PLAN; then repeat ACT, OBSERVE, CHECK; STOP on success or when a budget runs out.
  - The harness: deterministic tools, checks against ground truth, budgets, tracing, an eval set, a pass/fail gate.
- `/loopdemo`, not `/loop`: Hermes has a built-in `/loop`, and a plugin cannot take a built-in name (Hermes skips it).
- The demos below run the loop **in code**, so every step is visible and repeatable. Each prints its trace as a table.
  They use only the 25 existing designs.

| Command | Loop or harness | What it teaches | Measured (`speed_results.json`) |
|---|---|---|---|
| `/loopdemo signoff kv` | read-only loop over a family: read metrics, observe, check the verdict, stop when all are checked | a loop needs a goal, a stop condition and a budget | 0.002 s |
| `/loopdemo layers kv8` | GUI loop: open, then show met1..met5 one at a time, check each picture rendered | the observation (a picture) is checked, not assumed | 7.2 s live |
| `/loopdemo sim vision lit` | act, observe, verify with a real job: start `make simulate`, poll every 2 s, check exit 0 and the PASS line | "done" means verified, not "the command returned" | 2.0 s live |
| `/harness names` | 10 fixed loose names, score, gate; "audio" must **not** be guessed | an eval set plus a gate turns "seems to work" into a number | 10/10, PASS |
| `/harness facts kv` | 3 questions per design, each checked against `metrics.json` and for a cited source file | grounding: a right answer without its source fails | 15/15, PASS |

- **The same ideas with a model inside the loop:** `examples/hermes_harness/` (README "Loop and harness engineering").
  It covers ReAct versus plan-then-execute, budgets, argument validation, grounding checks, and an eval over 6
  configurations. Key result there: moving work the model is bad at into deterministic tools beat a better prompt.
  This whole integration applies that lesson; the fast paths (4.6) are the same move.

### 4.17 Start script and pinned demo sessions
- **Start everything from a terminal:** `bash scripts/hermes_start.sh`, or `make hermes-app`, or
  `bash scripts/hermes.sh app`. It checks, and starts what is missing, before Hermes.app opens:
  1. the `hermes` CLI and Hermes.app;
  2. the `chip` profile: the default, with the plugin linked and enabled, the MCP server and the hook;
  3. Ollama (starts Ollama.app);
  4. the two models (`qwen3.5-64k:9b` at its 64k context, the copy Hermes uses; `qwen3-embedding:0.6b`), loaded into
     memory **before** the app opens and pinned until Hermes.app quits:
     - `scripts/hermes/keep_models_warm.sh` re-pins them every 2 minutes, because each Hermes request resets Ollama's
       timer to 5 minutes;
     - it unloads them when the app closes;
     - `--status` shows what is loaded, `--stop` unloads now;
     - memory used: 7.8 GB + 2.4 GB;
     - a warm re-pin costs 0.14 s. Ollama 0.35.1 ignores an empty "load" request for a model already in memory, so the
       re-pin generates one token;
  5. Docker (starts Colima `osl`), the LibreLane image and the PDK;
  6. KLayout, XQuartz on TCP 6000 for Magic (started if already configured), Grafana;
  7. the repo tool server (restarted when its code is newer and no job is running);
  8. smoke tests (`/timing kv8`, an ambiguous name, RAG, the name harness, the plugin as Hermes sees it, the MCP bridge
     with its tool count);
  9. Hermes.app (restarted if the plugin changed since it started).
- It ends with a table (ok / warn / FAIL) and exits 1 if a required part failed.
  - `--check` changes nothing.
  - `--no-docker` and `--no-app` skip those parts.
  - It never pulls models, never edits `~/.hermes` (that stays with `hermes_agent_setup.sh`), and never reads secrets.
- **Pinned demo sessions:** `bash scripts/hermes_start.sh --demo-sessions` (or `scripts/hermes/make_demo_sessions.sh`)
  creates nine sessions in Hermes.app's **Pinned** sidebar section:
  - Demo 1: Repo tour;
  - Demo 2: Search and explain;
  - Demo 3: Open, operate and close a layout;
  - Demo 4: Partial names;
  - Demo 5: Synthesis, timing, DRC, LVS;
  - Demo 6: Kick off an experiment;
  - Demo 7: What-if and guardrails;
  - Demo 8: Loop and harness engineering;
  - Demo 9: The model, for contrast.
- Each session holds its card: the commands to type, the sentences to try, and what to say. `/demo <name>` and `/demos`
  show the same cards anywhere.
- The sessions are created with `--source cli`. Hermes.app's sidebar hides `oneshot` sessions even when they are pinned
  (`SIDEBAR_EXCLUDED_SOURCES` in the desktop source), so the first version of the script made invisible sessions.
- Titles are set after all sessions exist. The CLI output ends lines with a carriage return, and Hermes refuses a title
  that contains one, which is why the earlier renames failed.
- Readability: in Hermes.app, **Cmd+,** → **Appearance**: **Color Mode: Light** (dark text) and **Chat Text Size: 150 %**.
  These are app preferences that no script can set.
- Rerunning replaces only the sessions it made itself (ids in `build/agent/demo_sessions.json`). It took about 3
  minutes for all nine.

## Level 5. Workflows

- **Explore a design (seconds):**
  - `/designs` → `/signoff vision lit` → `/synth vision lit` → `/timing vision lit` → `/klayout vision lit show only met1`;
  - then ask "why is it so small?" (RAG quotes).
- **Understand a difference:**
  - `/compare kv4 kv8 kv16`;
  - then "why does kv16 have less setup slack than kv8?": the model reads the notes; ask for Claude if inconclusive.
- **Check a physical result visually:**
  - `/drc kv8 live` runs DRC in the KLayout window;
  - `/layout zoom to the lower-left 50 um`, `/layout show only met4 and met5`.
- **Try a constraint change:**
  - "what if PL_TARGET_DENSITY_PCT were 60 for vision_block?": `propose_change` checks the HARD RULES and shows the
    patch;
  - `whatif_run` returns a confirm id; you write `yes, run <id>`;
  - `/jobs`, then `whatif_result` compares with the frozen run.
- **Rebuild from scratch:** `/rebuild kv8` runs the full flow on an unchanged copy (minutes), then compares. Useful to
  show reproducibility.
- **Teach the agent loop:** `/loopdemo signoff kv` (a loop), `/harness facts kv` (a harness), `/loopdemo layers kv8` (a GUI
  loop), `/loopdemo sim vision lit` (verify, not assume); then `examples/hermes_harness/` for the version with a model inside.
- **Re-run a cheap check:** `/sim kv8`, `/run gl kv8`, `/run check kv8` (reads the existing run; no new layout).
- **A new design or an RTL change:** ask Claude (`ask_claude` or Claude Code) with skills `add-tiny-engine` or
  `harden-design`. Then the owner unfreezes, re-hardens and freezes.

## Level 6. Measured numbers

| What | Value | Source |
|---|---|---|
| Loop and harness demos (read-only) | 0.001 to 0.009 s; `/loopdemo layers` 7.2 s and `/loopdemo sim` 2.0 s live | same file (`commands`, `observed.loops_live`) |
| Report commands through `/quick` | 0.001 to 0.009 s | `examples/hermes_desktop/eval_tools/speed_results.json` (`commands`) |
| Router on a sentence | 0.002 s (facts) to 0.12 s (RAG quotes) | same file (`sentences`) |
| RAG first and warm question | 0.63 s, 0.13 s | same file (`rag`) |
| Through Hermes's own command dispatch | 0.1 s reports, 2.5 s `/klayout vision lit show only met1` | same file (`observed`) |
| End to end, real `hermes` binary and profile | 6 to 10 s for handled sentences (search, experiment confirm, list, close); about 70 s for a model-plus-tool question | same file (`observed.e2e_real_binary_2026_10_07`) |
| End to end, plugin dispatch | 0.3 to 4.3 s per GUI or report step; `/experiment soc-kv` 15 s; `/whatif` full flow on a copy 51 s; `CLOCK_PERIOD=40` blocked | same file (`observed.e2e_plugin_dispatch_2026_10_07`) |
| Before the fast paths | about 390 s (default profile, interrupted), 70 s and 24 s (chip profile opens), 92 s and a wrong sign (`/timing` via the model) | same file (`observed`) |
| Tool-calling accuracy | 51/58 = 87.9 % (Hermes, qwen3.5) vs 60.7 % (Open WebUI) | `examples/hermes_desktop/eval_tools/results_summary_hermes.json`, `docs/HERMES_AGENT.md` |
| Validation | `make test-full` 130 PASS, 25 designs frozen | `docs/VALIDATION.md`, `designs/FROZEN.json` |

Re-measure with `build/agent/venv/bin/python scripts/hermes/measure_speed.py`.

## Level 7. Status, limits, what needs you

- **Owner actions:**
  - Done on 2026-10-07: the `chip` profile is applied and is the default (backup `~/.hermes/backups/open-ai-chip-20261007-063546`).
  - Every session: `bash scripts/hermes_start.sh` (4.17). Before a class, add `--demo-sessions` once.
  - `/magic` needs XQuartz listening on TCP once (`scripts/gui/open_gui.sh` header).
  - Decide on a Hermes gateway (for cron) and on Grafana's `grafana_api_request`.
- **Limits:**
  - Demos 4 and 6 to 11 (their model-driven versions) have not been run end to end; the plugin forms (`/whatif`, `/rebuild`, `/experiment`) are verified.
  - The demo models are untuned (`mistral-nemo` runs with a 4k context).
  - `log_digest` is flaky on the 9B model.
  - Desktop UI plugins (status-bar job chip, palette) are planned, not built.
- **Next (agreed):** a careful refactor and a coverage test that every tool, skill, command and demo is reachable from
  Hermes.app.

## Level 8. Map of files

| Concern | Files |
|---|---|
| Setup | `scripts/hermes_agent_setup.sh`, `scripts/hermes/setup_profile.py` |
| Start and demo sessions | `scripts/hermes_start.sh` (`make hermes-app`), `scripts/hermes/make_demo_sessions.sh` |
| Fast paths, loops, harnesses | `scripts/hermes/plugin/open-ai-chip/`, `examples/hermes_desktop/tool_server/quick_tools.py`, `scripts/hermes/measure_speed.py`; model-in-the-loop version `examples/hermes_harness/` |
| GUI | `examples/hermes_desktop/tool_server/gui_tools.py`, `examples/hermes_klayout_gui/`, `scripts/gui/open_gui.sh` |
| Safety | `scripts/hermes/hooks/pre_tool_call.py`, `scripts/hermes/hooks/confirm_guard.py` |
| MCP | `tools/hermes_mcp_bridge.py`, `tools/hermes_tools.json` |
| Tool server | `examples/hermes_desktop/tool_server/` (`*_tools.py` auto-mounted; names: `normalize_tools.py`) |
| Context | `.hermes.md` (generated), `scripts/docs/make_hermes_context.py`, `CLAUDE.md` "For Hermes", `.hermes/environment.json` |
| Skills | `.claude/skills/*/SKILL.md` |
| RAG | `examples/hermes_desktop/tool_server/rag_tools.py`, `examples/hermes_rag/` |
| Freeze | `designs/FROZEN.json`, `designs/FROZEN.md`, `scripts/flow/freeze.py`, `scripts/flow/frozen.py` |
| Cron | `scripts/hermes/cron_run.sh` |
| Grafana | `scripts/grafana/`, `scripts/hermes/grafana_mcp.{sh,json}`, `examples/grafana/`, [docs/GRAFANA.md](docs/GRAFANA.md) |
| Eval and speed | `examples/hermes_desktop/eval_tools/` (`cases.json`, `run_eval.py`, `results_summary_*.json`, `speed_results.json`) |
| Tests | `tests/tools/test_quick_tools.py`, `test_normalize_tools.py`, `test_hermes_hook.py`, `test_hermes_confirm_guard.py`, `test_rag_tools.py`, `test_grafana_export.py` |

## Appendix: other front ends (kept, not maintained)

- Open WebUI (`make hermes`, `make demo*`), the repo's wrapper app "Hermes Chip Agent.app", and the terminal loops still
  work, but get no new work.
- Gaps: [SPEC.md](SPEC.md#agent-front-ends-decision-2026-10-07).
- Measured reason: 60.7 % against 87.9 % on the same cases. The Open WebUI path's ~18.5k-token tool prompt is cut to an
  8k context.
