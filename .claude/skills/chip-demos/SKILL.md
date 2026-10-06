---
name: chip-demos
description: Eleven narrated demos of the open-ai-chip repo that run inside the Hermes Agent desktop app (profile chip) by name. Use when the user says "run demo 4", "demo: layout", "list the demos", "give me a demo of <ask the chips, why, logs, layout, flow, what-if, experiments, skills, memory, proof, claude>", or asks what to show someone. Each demo gives the exact prompts to type, the tool calls to expect, what to look at, what to say about the chip design, and the time.
---

# chip-demos: eleven narrated demos for Hermes.app

Self-contained. Demos run through the MCP tools of the `chip` profile (names `mcp__chip__<tool>`). The agent never edits repo files; every run starts only after the USER writes "yes, run <id>".
How to run one: the user says "run demo N" or "demo: <name>". Reply with the demo title, then walk the steps: say the next prompt to type (or call the tool when the step is a plain question), show the result, then give the "Explain" lines. Never run a physical step (flow, what-if flow, experiment, ask_claude) yourself: show the confirm text and wait for the user's own "yes, run <id>".
Menu (say "list the demos"): 1 chips, 2 why, 3 logs, 4 layout, 5 flow, 6 what-if, 7 experiments, 8 skills, 9 memory, 10 proof, 11 claude.
Setup prerequisite: `bash scripts/hermes_agent_setup.sh --apply` (add `--demo-tools` for demos 3 (log_digest) and 6). Human version with measured transcripts: docs/HERMES_DEMOS.md. Order for a short talk: 1, 2, 4, 5, 6.
Models: the default qwen3.5-64k:9b is the one evaluated (87.9 % on 58 tool-calling cases, examples/hermes_desktop/eval_tools/results_summary_hermes.json). hermes3:8b, gemma3:4b-it-qat, gemma4:12b and mistral-nemo:latest are selectable for show; do not run the demos on them (docs/HERMES_AGENT_INTEGRATION.md, "Models for demos").

## Demo 1: Ask the chips (numbers)

- Say to Hermes (one prompt per message):
  - `How many standard cells does vision_block have?`
  - `Which of kv_attn_n4, kv_attn_n8 and kv_attn_n16 has the most standard cells, and how many?`
- Tools expected: read_metrics (design vision_block, key design__instance__count__stdcell); compare_designs (metric design__instance__count__stdcell)
- Look at: the tool-call cards in the chat. Answers: vision_block 297; kv_attn_n16 4169, kv_attn_n8 2566, kv_attn_n4 1679. They match designs/<d>/output/metrics.json.
- Explain: A standard cell is one placed gate from the sky130_fd_sc_hd library; the count includes fill, tap, diode and timing-repair buffer cells, so it is bigger than the synthesised logic. The numbers are read from the committed metrics.json, not remembered by the model. The KV engine grows about 200 to 220 std cells per cache slot (N = 4, 8, 16 gives 1679, 2566, 4169).
- Time: 15 to 20 s per question (measured 14 s and 19 s). Status: verified (isolated home, qwen3.5-64k:9b).

## Demo 2: Why, from the design notes

- Say to Hermes (one prompt per message):
  - `Why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8?`
  - `Show the Intuitions and insights section of kv_attn_n8.`
- Tools expected: rag_answer (question); notes_section (design kv_attn_n8, section Intuitions and insights)
- Look at: the quoted sentences with file:line, e.g. designs/kv_attn_n8_int4/NOTES.md:224. The answer must cite a file; if it does not, ask 'search the docs for it' (search_docs).
- Explain: Halving the cache bits did not halve the flip-flops: kv_attn_n8_int4 has 222 flops against 200 for kv_attn_n8 (96 cache flops against 72), because the int8 baseline was already pruned. The weights are fixed constants, so most stored fields carry little information and synthesis removes their flops. Lesson: nominal bits are not flip-flops; read metrics.json.
- Time: 45 to 80 s (measured 47 s and 76 s). Status: verified (isolated home).

## Demo 3: Logs: errors and key lines

- Say to Hermes (one prompt per message):
  - `Show the errors in the flow log of kv_attn_n8.`
  - `Use the log_digest tool for kv_attn_n8 and tell me the key lines.`
- Tools expected: read_log (design kv_attn_n8, which error); optional log_digest (design kv_attn_n8)
- Look at: read_log returns real lines with line numbers. For kv_attn_n8 it reports no errors for run RUN_2026-10-06_07-00-35: the error log is empty.
- Explain: A clean flow leaves an empty error log; warnings are normal (LibreLane prints many). Logs live in designs/<d>/output/flow.log (committed) and designs/<d>/runs/RUN_*/ (local, git-ignored). If a stage fails, diagnose (demo 5) matches the log to a cause.
- Time: 20 s (measured 18 s); log_digest 30 to 200 s and flaky on the 9B model. Status: verified for read_log; log_digest needs `--demo-tools` and the model twice produced an invalid tool call (recorded in docs/HERMES_DEMOS.md).

## Demo 4: Open the layout in KLayout and Magic

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

## Demo 5: Run a flow (gated, you confirm)

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

## Demo 6: What-if: change a constraint on a copy

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

## Demo 7: Experiments: soc-kv and the precision table

- Say to Hermes (one prompt per message):
  - `What experiments can I run?`
  - `Run the experiment soc-kv   (approve, then yes, run <id>)`
  - `Show the soc-kv results`
  - `Show the precision table`
- Tools expected: list_experiments; run_experiment (id soc-kv) -> confirm_run -> job_status; experiment_result (id soc-kv or precision)
- Look at: the precision table: bin 199 std cells at 88.95 % accuracy, ternary 293 and int4 377 at about 94 %, fp16 1932 and bf16 1754; fp16 and bf16 have only 0.111 and 0.044 ns setup slack at 40 MHz. soc-kv shows prefill against decode cycles.
- Explain: Precision study: one neuron in seven number formats. Ternary and int4 reach about 94 % accuracy with 293 and 377 cells; fp16 costs 1932 cells for the same accuracy. SoC: the accelerator computes in a few clocks but the bus costs hundreds, so decode is bus-bound.
- Time: soc-kv about 15 s; precision table instant. Status: precision table checked against the tool server; run_experiment not run.

## Demo 8: Skills: the plan for a job

- Say to Hermes (one prompt per message):
  - `Give me the plan for hardening vision_block`
  - `Show me the tune-timing-sdc skill`
- Tools expected: skill_plan (skill harden-design, design vision_block); for the second prompt Hermes' own skills tools (skills_list / skill_view) read .claude/skills/tune-timing-sdc/SKILL.md
- Look at: skill_plan returns ordered steps with the tool for each (doctor, flow-all, run_summary, diagnose ...). skill_plan knows add-tiny-engine, harden-design, precision-variant, soc-run, wrapper-build, write-design-notes; the tune-* skills are read, not planned.
- Explain: The skills are the repo's operating manuals (.claude/skills/), shared with Claude Code. The agent cannot edit files, so a plan ends with 'needs edits: the owner applies this' and ask_claude (demo 11) can propose the patch.
- Time: 15 to 30 s. Status: skill_plan checked against the tool server.

## Demo 9: Memory and run history

- Say to Hermes (one prompt per message):
  - `Remember that I prefer tables`
  - `What did I tell you about tables?`
  - `What jobs have we run?`
- Tools expected: remember (note, topic); recall (query); job_list
- Look at: the note is saved in build/agent/memory (outside the repo tree's tracked files) and found again by recall, also in a new session.
- Explain: Local, inspectable memory: a plain file you can read and delete. The Hermes built-in memory toolset is off for the chip profile so nothing is stored in ~/.hermes behind your back.
- Time: 10 s per prompt. Status: not run (it writes build/agent/memory); tools exist in the core set.

## Demo 10: Proof: local, repo and context

- Say to Hermes (one prompt per message):
  - `Is this running locally? Does it send data anywhere?`
  - `What context did you get for the last turn?`
- Tools expected: proof_local; show_context
- Look at: proof_local lists the measured listeners and connections: Ollama on 127.0.0.1:11434, the tool server on 127.0.0.1:8770, no remote peer. show_context lists what was put in the prompt.
- Explain: The model is qwen3.5-64k:9b on your Mac through Ollama; the repo is read through the MCP bridge; the only way text leaves the machine is ask_claude, which asks first (demo 11).
- Time: 10 to 20 s. Status: not run (reads the live listeners).

## Demo 11: Claude steps in (ask_claude, with your confirmation)

- Say to Hermes (one prompt per message):
  - `Ask Claude to explain the worst setup path of kv_attn_n8   (then yes, run <id>)`
  - `Check the Claude job`
- Tools expected: ask_claude (first call returns confirm_id and says the text goes to Claude, a cloud model) -> you type 'yes, run <id>' -> confirm_run -> claude_status (job_status)
- Look at: the confirm text names exactly what will be sent. Without your 'yes, run <id>' nothing is sent; the hook blocks a self-confirmation here too.
- Explain: Local first, cloud on request: the small local model does the routine reading; for a hard question you choose to hand a self-contained question to Claude. Claude's answer comes back as text; it does not edit the repo either.
- Time: 1 to 3 minutes; needs the claude CLI. Status: not run (sends text to a cloud model); the gate is the same two-step confirm.

