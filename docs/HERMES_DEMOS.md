# Hermes demos: fifteen narrated demos for the desktop app

Fifteen short demos you run inside the Hermes Agent desktop app (Hermes.app, profile `chip`) by typing "run demo 4" or "demo: layout".
The same content is the skill `.claude/skills/chip-demos/SKILL.md`, which Hermes loads from `skills.external_dirs`, so the agent can walk you through them.
Background: [HERMES_AGENT_INTEGRATION.md](HERMES_AGENT_INTEGRATION.md) (setup, safety, models). Numbers below come from `designs/<d>/output/metrics.json`
and from the transcripts at the end (isolated home `build/hermes_final_home`, model `qwen3.5-64k:9b`, one-shot `hermes -p chip -z`).

## Prerequisite (once)

```bash
bash scripts/hermes_agent_setup.sh                      # dry run: review the diff
bash scripts/hermes_agent_setup.sh --apply --demo-tools # profile chip; --demo-tools adds log_digest, param_info, propose_change, whatif_run, whatif_result (demos 3 and 6)
open -a Hermes                                          # pick profile chip, model qwen3.5-64k:9b
```

Ollama must be running with `qwen3.5-64k:9b` installed. Demo 4 needs the KLayout app (Magic: XQuartz and Docker), demos 5 and 6 (physical) need Docker (`make doctor`).
Safety during demos: every run needs your own message "yes, run <id>"; the hook blocks the model if it tries to confirm itself; physical flows also show an approval card.
Run the demos on `qwen3.5-64k:9b` (the evaluated model); the other local models are for show only.

Short talk: demos 12, 13, 2, 5, 14 (about 10 minutes; 12 to 15 need the plugin, installed by the setup script). Full tour: 1 to 15.

### Demo 1: Ask the chips (numbers)

- Say to Hermes (one prompt per message):
  - `How many standard cells does vision_block have?`
  - `Which of kv_attn_n4, kv_attn_n8 and kv_attn_n16 has the most standard cells, and how many?`
- Tools expected: read_metrics (design vision_block, key design__instance__count__stdcell); compare_designs (metric design__instance__count__stdcell)
- Look at: the tool-call cards in the chat. Answers: vision_block 297; kv_attn_n16 4169, kv_attn_n8 2566, kv_attn_n4 1679. They match designs/<d>/output/metrics.json.
- Explain: A standard cell is one placed gate from the sky130_fd_sc_hd library; the count includes fill, tap, diode and timing-repair buffer cells, so it is bigger than the synthesised logic. The numbers are read from the committed metrics.json, not remembered by the model. The KV engine grows about 200 to 220 std cells per cache slot (N = 4, 8, 16 gives 1679, 2566, 4169).
- Time: 15 to 20 s per question (measured 14 s and 19 s). Status: verified (isolated home, qwen3.5-64k:9b).

### Demo 2: Why, from the design notes

- Say to Hermes (one prompt per message):
  - `Why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8?`
  - `Show the Intuitions and insights section of kv_attn_n8.`
- Tools expected: rag_answer (question); notes_section (design kv_attn_n8, section Intuitions and insights)
- Look at: the quoted sentences with file:line, e.g. designs/kv_attn_n8_int4/NOTES.md:224. The answer must cite a file; if it does not, ask 'search the docs for it' (search_docs).
- Explain: Halving the cache bits did not halve the flip-flops: kv_attn_n8_int4 has 222 flops against 200 for kv_attn_n8 (96 cache flops against 72), because the int8 baseline was already pruned. The weights are fixed constants, so most stored fields carry little information and synthesis removes their flops. Lesson: nominal bits are not flip-flops; read metrics.json.
- Time: 45 to 80 s (measured 47 s and 76 s). Status: verified (isolated home).

### Demo 3: Logs: errors and key lines

- Say to Hermes (one prompt per message):
  - `Show the errors in the flow log of kv_attn_n8.`
  - `Use the log_digest tool for kv_attn_n8 and tell me the key lines.`
- Tools expected: read_log (design kv_attn_n8, which error); optional log_digest (design kv_attn_n8)
- Look at: read_log returns real lines with line numbers. For kv_attn_n8 it reports no errors for run RUN_2026-10-06_07-00-35: the error log is empty.
- Explain: A clean flow leaves an empty error log; warnings are normal (LibreLane prints many). Logs live in designs/<d>/output/flow.log (committed) and designs/<d>/runs/RUN_*/ (local, git-ignored). If a stage fails, diagnose (demo 5) matches the log to a cause.
- Time: 20 s (measured 18 s); log_digest 30 to 200 s and flaky on the 9B model. Status: verified for read_log; log_digest needs `--demo-tools` and the model twice produced an invalid tool call (recorded in docs/HERMES_DEMOS.md).

### Demo 4: Open the layout in KLayout and Magic

- Say to Hermes (one prompt per message):
  - `open kv_attn_n8 in klayout`
  - `show only met1 and met2`
  - `zoom to the lower-left 50 um`
  - `run drc`
  - `open kv_attn_n8 in magic`
  - `close all`
- Tools expected: gui_command (text = your sentence unchanged); gui_examples lists the sentences it understands; open_gds for 'open the GDS of X'; klayout_view for a picture in the chat instead of a window
- Look at: the window changes after each sentence: whole die (260 x 260 um), then rails and routing only, then standard-cell rows, then DRC markers (0 for these clean designs).
- Explain: li1 is local interconnect, met1 and met2 are the first routing layers (met1 horizontal, met2 vertical); standard cells sit in rows 2.72 um high with power rails on met1; the picture is the real GDSII that passed DRC and LVS. KLayout is the fast viewer, Magic is the checker with its own DRC. Say 'close all' at the end.
- Time: 1 to 2 minutes; KLayout needs the KLayout app, Magic needs XQuartz and Docker. Status: not run in the isolated home (opens real windows).

### Demo 5: Run a flow (gated, you confirm)

- Say to Hermes (one prompt per message):
  - `Run make simulate for vision_block`
  - `yes, run <id>   (type the id Hermes shows)`
  - `Summarize the last run of vision_block`
  - `What should I improve in vision_block?`
  - `Run the full flow for vision_block   (physical; approve the card, then yes, run <id>)`
- Tools expected: run_make (first call starts nothing and returns confirm_id plus the text to show) -> you type 'yes, run <id>' -> confirm_run -> job_status; then run_summary, suggest, and diagnose if a stage failed
- Look at: the confirm text, then an approval card for physical targets, then job_status with the log tail. If the model calls confirm_run before you answered, the hook blocks it: 'the user has not written yes, run <id>'.
- Explain: Nothing physical starts without you: first call returns an id, only your message 'yes, run <id>' releases it (scripts/hermes/hooks/confirm_guard.py reads the session and checks who wrote the id). One flow at a time (10 minute cap). A question like 'how do I run it' never starts a run.
- Time: simulate 1 to 3 s; flow-all about 1 to 2.5 minutes plus checks (needs Docker). Status: gate and guard verified live (isolated home): the model's own confirm was blocked 3 times; one-shot -z cannot show the second turn because each -z starts a new tool server.

### Demo 6: What-if: change a constraint on a copy

- Say to Hermes (one prompt per message):
  - `What does PL_TARGET_DENSITY_PCT mean for kv_attn_n8?`
  - `Is setting CLOCK_PERIOD to 40 allowed for kv_attn_n8?`
  - `Is setting CLOCK_PERIOD to 20 allowed for kv_attn_n8?`
  - `Run a what-if for kv_attn_n8 with PL_TARGET_DENSITY_PCT 60, tag d60   (then yes, run <id>)`
  - `Show the what-if result d60 for kv_attn_n8`
- Tools expected: param_info; propose_change (writes nothing); whatif_run (confirm gate plus approval card) -> job_status; whatif_result
- Look at: 40 ns is BLOCKED (rule R1-CLOCK: only a shorter period is allowed); 20 ns is allowed with a patch text; the what-if result is a table of committed against changed numbers.
- Explain: A what-if runs on a copy under build/whatif/, never on designs/<d>/, so the committed evidence stays valid. HARD RULE: never loosen CLOCK_PERIOD (25 ns) or MAX_TRANSITION_CONSTRAINT to make a gate pass; tightening is a legitimate experiment and shrinks setup slack.
- Time: questions 15 s; a what-if flow 2 to 3 minutes (Docker). Status: propose_change and param_info answers checked against the tool server; whatif_run not run; needs `--demo-tools`.

### Demo 7: Experiments: soc-kv and the precision table

- Say to Hermes (one prompt per message):
  - `What experiments can I run?`
  - `Run the experiment soc-kv   (approve, then yes, run <id>)`
  - `Show the soc-kv results`
  - `Show the precision table`
- Tools expected: list_experiments; run_experiment (id soc-kv) -> confirm_run -> job_status; experiment_result (id soc-kv or precision)
- Look at: the precision table: bin 199 std cells at 88.95 % accuracy, ternary 293 and int4 377 at about 94 %, fp16 1932 and bf16 1754; fp16 and bf16 have only 0.111 and 0.044 ns setup slack at 40 MHz. soc-kv shows prefill against decode cycles.
- Explain: Precision study: one neuron in seven number formats. Ternary and int4 reach about 94 % accuracy with 293 and 377 cells; fp16 costs 1932 cells for the same accuracy. SoC: the accelerator computes in a few clocks but the bus costs hundreds, so decode is bus-bound.
- Time: soc-kv about 15 s; precision table instant. Status: precision table checked against the tool server; run_experiment not run.

### Demo 8: Skills: the plan for a job

- Say to Hermes (one prompt per message):
  - `Give me the plan for hardening vision_block`
  - `Show me the tune-timing-sdc skill`
- Tools expected: skill_plan (skill harden-design, design vision_block); for the second prompt Hermes' own skills tools (skills_list / skill_view) read .claude/skills/tune-timing-sdc/SKILL.md
- Look at: skill_plan returns ordered steps with the tool for each (doctor, flow-all, run_summary, diagnose ...). skill_plan knows add-tiny-engine, harden-design, precision-variant, soc-run, wrapper-build, write-design-notes; the tune-* skills are read, not planned.
- Explain: The skills are the repo's operating manuals (.claude/skills/), shared with Claude Code. The agent cannot edit files, so a plan ends with 'needs edits: the owner applies this' and ask_claude (demo 11) can propose the patch.
- Time: 15 to 30 s. Status: skill_plan checked against the tool server.

### Demo 9: Memory and run history

- Say to Hermes (one prompt per message):
  - `Remember that I prefer tables`
  - `What did I tell you about tables?`
  - `What jobs have we run?`
- Tools expected: remember (note, topic); recall (query); job_list
- Look at: the note is saved in build/agent/memory (outside the repo tree's tracked files) and found again by recall, also in a new session.
- Explain: Local, inspectable memory: a plain file you can read and delete. The Hermes built-in memory toolset is off for the chip profile so nothing is stored in ~/.hermes behind your back.
- Time: 10 s per prompt. Status: not run (it writes build/agent/memory); tools exist in the core set.

### Demo 10: Proof: local, repo and context

- Say to Hermes (one prompt per message):
  - `Is this running locally? Does it send data anywhere?`
  - `What context did you get for the last turn?`
- Tools expected: proof_local; show_context
- Look at: proof_local lists the measured listeners and connections: Ollama on 127.0.0.1:11434, the tool server on 127.0.0.1:8770, no remote peer. show_context lists what was put in the prompt.
- Explain: The model is qwen3.5-64k:9b on your Mac through Ollama; the repo is read through the MCP bridge; the only way text leaves the machine is ask_claude, which asks first (demo 11).
- Time: 10 to 20 s. Status: not run (reads the live listeners).

### Demo 11: Claude steps in (ask_claude, with your confirmation)

- Say to Hermes (one prompt per message):
  - `Ask Claude to explain the worst setup path of kv_attn_n8   (then yes, run <id>)`
  - `Check the Claude job`
- Tools expected: ask_claude (first call returns confirm_id and says the text goes to Claude, a cloud model) -> you type 'yes, run <id>' -> confirm_run -> claude_status (job_status)
- Look at: the confirm text names exactly what will be sent. Without your 'yes, run <id>' nothing is sent; the hook blocks a self-confirmation here too.
- Explain: Local first, cloud on request: the small local model does the routine reading; for a hard question you choose to hand a self-contained question to Claude. Claude's answer comes back as text; it does not edit the repo either.
- Time: 1 to 3 minutes; needs the claude CLI. Status: not run (sends text to a cloud model); the gate is the same two-step confirm.

### Demo 12: Instant commands (no model)

- Type: `/chip`, `/designs`, `/signoff caravel kv`, `/synth vision lit`, `/timing kv_attn`, `/drc kv attention 16`, `/lvs prec bf16`, `/compare kv4 kv8 kv16`
- What runs: the open-ai-chip plugin's slash commands -> tool server POST /quick; no model turn, no tool-call cards.
- Look at: each answer ends with `Source: designs/<d>/output/...`; partial names resolve (kv_attn -> kv_attn_n8, vision lit -> vision_all_lit, caravel kv -> user_project_wrapper_soc_kv). The timing table has nine corners; setup slack is positive (met).
- Explain: a lookup does not need a language model. Code reads the committed evidence in milliseconds and cannot invent a number or flip a sign; the model is kept for explaining. This is the 'move work into deterministic tools' lesson of harness engineering.
- Time: about a second each (0.1 s through Hermes's command dispatch, examples/hermes_desktop/eval_tools/speed_results.json). Status: verified (isolated home, Hermes plugin dispatch).

### Demo 13: GUI tour of a layout (KLayout and Magic by text)

- Type: `/klayout kv_attn show only met1`, then `/layout show only met4 and met5`, `/layout zoom to the lower-left 50 um`, `/drc kv8 live`, `/layout show all`, `/loop layers vision lit`, `/layout close all`. With XQuartz set up: `/magic vision lit find clk`.
- What runs: gui_command through the plugin (instant) or, in words ("open kv_attn in klayout and show only met1"), the pre_llm_call router; a picture of the view comes back after each step.
- Look at: the KLayout window follows each command; met1 is the horizontal power rails and short local wiring, met4/met5 the power grid straps; the lower-left 50 um shows standard-cell rows; DRC shows 0 markers; the layer tour returns five pictures.
- Explain: a layout is a stack of masks; looking at one metal at a time shows how routing uses alternating directions and how the power grid is built. Nothing is ever saved: the parser has no write operation.
- Time: 1 to 3 s per step; `/loop layers` 7.2 s (speed_results.json). Status: verified live for `/klayout vision lit show only met1` and `/loop layers vision lit`; the other window steps go through the same gui_command parser (tests/tools/test_gui_tools.py) but were not timed here. Magic needs XQuartz on TCP (scripts/gui/open_gui.sh header).

### Demo 14: Loop engineering (plan, act, observe, check, stop)

- Type: `/loop signoff kv`, then `/loop sim vision lit`.
- What runs: two loops in code with their trace printed as a table: a read-only loop over the five KV designs, and a real job (make simulate) polled until its PASS line is verified.
- Look at: row 0 is the PLAN with the goal, the stop condition and the budget; each step is ACT, OBSERVE, CHECK; the last row is STOP with the result (5 of 5 clean, tightest setup slack kv_attn_n16 8.766 ns; the simulation's PASS line).
- Explain: an agent is a loop. A good loop has a goal, a stop condition and a budget, and it verifies (the PASS line), it does not assume ("the command returned"). examples/hermes_harness/ shows the same loop with a model choosing the actions (ReAct versus plan-then-execute).
- Time: `/loop signoff` instant; `/loop sim vision lit` 2 s (speed_results.json). Status: verified.

### Demo 15: Harness engineering (fixed cases, score, gate)

- Type: `/harness names`, then `/harness facts kv`.
- What runs: two small harnesses: 10 loose design names checked against the expected design (an ambiguous 'audio' must ask, not guess), and 3 questions per KV design checked against metrics.json and for a cited source file.
- Look at: the score and the gate line (10/10 PASS, 15/15 PASS); a row fails if the answer is right but names no source.
- Explain: a harness turns 'it seems to work' into a number that a test can hold: an eval set, ground truth, a score and a pass/fail gate. The same harnesses run in `make test` (tests/tools/test_quick_tools.py). examples/hermes_harness/ measures six harness configurations around one fixed model.
- Time: instant. Status: verified.

## Measured transcripts (demos 1 to 3)

Setup: `HERMES_HOME=build/hermes_final_home`, profile `chip` created by `scripts/hermes_agent_setup.sh --demo-tools --apply`, model `qwen3.5-64k:9b`, command
`hermes -p chip -z "<prompt>"`, 2026-10-07. Tools are read from `build/agent/hermes_hook.log` (one line per call). Excerpts, text shortened where marked.

| Demo | Prompt | Tool calls | Wall time |
|---|---|---|---|
| 1 | How many standard cells does vision_block have? | read_metrics | 14 s |
| 1 | Which of kv_attn_n4, kv_attn_n8 and kv_attn_n16 has the most standard cells, and how many? | compare_designs | 19 s |
| 2 | Why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8? | rag_answer | 47 s |
| 2 | Show the Intuitions and insights section of kv_attn_n8. | notes_section (twice) | 76 s |
| 3 | Show the errors in the flow log of kv_attn_n8. | read_log | 18 s |
| 3 | Give me a digest of the logs of kv_attn_n8: errors, warnings and key lines. | log_digest, read_log x3 | 207 s, ended with "Model generated invalid tool call: read_log" |
| 3 | Use the log_digest tool for kv_attn_n8 and tell me the key lines. | log_digest | 31 s, ended with "Model generated invalid tool call: read_file" |

Demo 1 (answers match `designs/vision_block/output/metrics.json`: 297):

```text
> How many standard cells does vision_block have?
`vision_block` has **297** standard cells (including fill, tap, and buffers).

> Which of kv_attn_n4, kv_attn_n8 and kv_attn_n16 has the most standard cells, and how many?
**kv_attn_n16** has the most standard cells: **4,169**.
- kv_attn_n16: 4,169 cells   - kv_attn_n8: 2,566 cells   - kv_attn_n4: 1,679 cells
```

Demo 2 (the quoted sentence is `designs/kv_attn_n8_int4/NOTES.md` line 224):

```text
> Why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8?
kv_attn_n8_int4 has more flip-flops (222) than kv_attn_n8 (200) because halving the cache bits did not halve (or even reduce)
the flip-flops, because the int8 baseline was already pruned.
From designs/kv_attn_n8_int4/NOTES.md:224:
> ... Nominal cache bits are 256 against 512 (golden.py --check), yet the flops are 222 here against 200 for kv_attn_n8
> (metrics.json): 96 cache flops against 72.

> Show the Intuitions and insights section of kv_attn_n8.
From designs/kv_attn_n8/NOTES.md, section Intuitions and insights (lines 285-307):
> 1. Prefill is streaming, decode is serial. ...   2. The cache is nominally 512 bits but only 72 flip-flops were built. ...   (text shortened)
```

Demo 3:

```text
> Show the errors in the flow log of kv_attn_n8.
The kv_attn_n8 design has no recorded errors in this run (RUN_2026-10-06_07-00-35). The error log is empty.
```

Honest notes: `log_digest` is the weak spot of the 9B model (two invalid tool calls in the two tries above); `read_log` is reliable, so demo 3 leads with it.
Demo 5 gate, measured live in the same home: `hermes -p chip -z "Run make simulate for vision_block"` answered "Reply 'yes, run 659ccc' to start make simulate for vision_block. Nothing has been started yet."; resuming the session with
`"yes, run 659ccc"` let the user's confirm through, and the model's own later `confirm_run` calls (ids bf9c00 and 5e7d1c, which you never typed) were blocked by
the hook ("the user has not written 'yes, run 5e7d1c' in their latest message", `build/agent/hermes_hook.log`).
Each `-z` starts its own bridge and tool server (the bridge starts one when none runs), so the pending id most likely did not survive between the two one-shot processes (inferred, not separately tested); in Hermes.app the session keeps one bridge and the id stays valid for 10 minutes.
