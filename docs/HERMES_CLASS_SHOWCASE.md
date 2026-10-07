# Class showcase: agentic chip design with Hermes (about 20 minutes)

A presenter's script for showing open-ai-chip in the Hermes Agent desktop app. Each step lists:
- **type**: exactly what to type in Hermes.app;
- **you see**: what appears;
- **say**: the chip-design and agent idea to explain.

Every step was run on this Mac on 2026-10-07. The times come from
`examples/hermes_desktop/eval_tools/speed_results.json`; the numbers on screen come from the committed
`designs/<d>/output/metrics.json` and `reports/`.

Background for the presenter: [hermes-agents.md](../hermes-agents.md) (the whole integration, top down).
More demos: [HERMES_DEMOS.md](HERMES_DEMOS.md).

## Before class (5 minutes)

From a terminal in the repo:

```bash
bash scripts/hermes_start.sh --demo-sessions    # first time before a class: also creates the nine pinned demo sessions
bash scripts/hermes_start.sh                    # any later time: checks and starts everything, opens Hermes.app
```

- It checks and starts Ollama and loads both models into memory **before** Hermes.app opens. They stay loaded until you
  quit the app (`scripts/hermes/keep_models_warm.sh --status` shows them), so no answer waits for a model load, Docker (for step 7), the tool
  server, KLayout and XQuartz.
- It smoke-tests the commands and opens Hermes.app.
- Expect a final table with **0 failed**. Warnings are optional parts (for example XQuartz, which only Magic needs).
- In Hermes.app, the sidebar's **Pinned** section lists the sessions "Demo 1: Repo tour" to "Demo 9: The model itself".
  Each one already shows its commands to type and what to say. Open the session for the step you are presenting; the
  steps below follow the same order.
- **Make the chat readable on a projector** (once; these are app preferences, kept across restarts). Press **Cmd+,**,
  open **Appearance**, and set:
  - **Color Mode: Light**: dark text on a light background (the dark mode's grey text is hard to read);
  - **Chat Text Size: 150 %** (presets 90 to 175 %; it scales chat text only);
  - **UI Scale** up a step if the sidebar is also too small.
- Warm the KLayout window once: type `klayout kv8`, then `chip close`.
- If `chip` is unknown in a chat, it is on another profile: start a new chat (Cmd+N), or run `hermes profile use chip`
  and restart the app. The start script reports which profile is the default.

| Session (sidebar, Pinned) | Steps below |
|---|---|
| Demo 1: Repo tour | 1, 5 |
| Demo 2: Search and explain | 2 |
| Demo 3: Open, operate and close a layout | 3 |
| Demo 4: Partial names | 4 |
| Demo 5: Synthesis, timing, DRC, LVS | 5 |
| Demo 6: Kick off an experiment | 6 |
| Demo 7: What-if and guardrails | 7 |
| Demo 8: Loop and harness engineering | 8 |
| Demo 9: The model itself | 9 |

## The one-minute story (say this first)

- **The repo:** 25 small AI accelerators taken from Verilog to a manufacturable layout (GDSII) on the open sky130
  process, on a laptop. Every result is committed and frozen, so the numbers can be checked.
- **The agent:** a local 9B model in Hermes, connected to the repo by about 50 safe tools. It reads, shows, explains and
  runs experiments. It never edits a file, never starts a run without your words, and never weakens a signoff rule.
- **The design trick:** anything that is really a lookup or a launch is done **in code**, in about a second. The
  model is used for language. That is the difference between a demo that waits a minute and one that answers at once.

## How to type the commands

- Type them **without** the `/` (`timing kv8`). The answer then appears as a normal chat reply: full size, with tables,
  🟢🟡🟠🔴 and ✅/❌ colours, bars and KLayout pictures. It takes a few seconds.
- With `/` the answer is instant, but Hermes.app shows plugin output as a small grey line without markdown. Use that only
  for quick checks, not on the projector.
- `run`, `rebuild`, `whatif` and `experiment` need `chip ` in front (or `/`): `chip experiment soc-kv`, `chip whatif ...`, `chip rebuild kv8`. `sim` and `loopdemo sim` start the cheap RTL simulation at once.
- The nine pinned sessions and `/demo n` use card numbers (1 tour ... 9 model); "run demo n" in the `chip-demos` skill uses the 15-demo numbering of `docs/HERMES_DEMOS.md`.
- If text is still small on the projector, zoom the app (**Cmd +**).

## The script

### 1. What is in the repo (1 minute)
- **type:** `designs`
- **you see:** 25 designs with cells, flip-flops, setup slack, and "clean" for each.
- **say:**
  - These went through simulation, synthesis, place and route, timing, DRC and LVS, and are frozen by sha256.
  - That was instant: no AI model ran. Code read 25 `metrics.json` files.

### 2. Search and explain, without hallucination (2 minutes)
- **type:** `search hold violation wrapper`, then the sentence `how was the wrapper hold violation fixed?`
- **you see:**
  - first, five passages with clickable `file:line` sources;
  - then the answer, quoted verbatim: the macro was re-hardened with the Caravel macro SDC, and hold went from
    -0.894 ns to +0.105 ns.
- **say:**
  - This is retrieval (RAG): BM25 plus local embeddings over the repo's own notes. First question about 0.6 s, then
    0.13 s (`examples/hermes_desktop/eval_tools/speed_results.json`, `rag`).
  - Before we added the exact-reply step, the 9B model paraphrased these passages and **invented** a fix (a changed
    `MAX_TRANSITION_CONSTRAINT`) that is not in the repo.
  - Now the quotes are shown exactly. Lesson: small models must quote, not summarise.

### 3. Open and operate the layout (4 minutes)
- **type, one per message:**
  - `klayout llm show only met1`
  - `layout show only met4 and met5`
  - `layout zoom to the lower-left 50 um`
  - `layout show all`
  - `drc kv8 live`
  - `chip close`
- **you see:** a KLayout window that follows each command (0.5 to 3.3 s each), and a picture in the chat after every
  step.
- **say:**
  - "llm" maps to `kv_attn_n8`, the KV-cache attention engine, the building block of LLM inference.
  - met1 holds the horizontal power rails and short wires.
  - met4 and met5 form the power grid.
  - The lower-left corner shows the rows of standard cells.
  - DRC shows zero markers: the layout obeys every manufacturing rule.
  - Nothing can be saved or changed: the GUI parser has no write operation.

### 4. Ask, don't guess (1 minute)
- **type:** `timing audio`, then just `2`
- **you see:** "Which design? 1) audio_onset 2) audio_pitch"; after `2`, the timing table of audio_pitch.
- **say:**
  - A good agent asks when a name is ambiguous instead of guessing.
  - The answer is completed in code. Partial names work everywhere: `kv8`, `vision lit`, `caravel kv`.

### 5. Signoff in numbers (2 minutes)
- **type:** `signoff caravel kv`, then `timing kv_attn`, then `compare kv4 kv8 kv16`
- **you see:**
  - the full Caravel wrapper is CLEAN;
  - nine timing corners with positive slack (10.74 ns worst setup at a 25 ns clock);
  - the KV family side by side: 1679, 2566 and 4169 cells.
- **say:**
  - Slack is how much earlier than needed the signal arrives, checked at slow, typical and fast corners.
  - Doubling the cache from 8 to 16 entries costs about 1600 cells.
  - Every answer names its source file.

### 6. Kick off an experiment (2 minutes)
- **type:** `experiments`, then `chip experiment soc-kv`, then `jobs` (the id it printed), then `result soc-kv`
- **you see:**
  - the catalogue;
  - a job started and done in about 15 s;
  - two tables: prefill cost per token falls from 328 to 90.6 clock cycles, while decode stays at about 670 cycles,
    74 % of it reading results over the bus.
- **say:**
  - This is the LLM prefill-versus-decode effect on a real SoC simulation (PicoRV32 plus the KV engine).
  - Decode is bus-bound, exactly as on big GPUs: memory bandwidth, not arithmetic.

### 7. What-if on a copy, and a guardrail (3 minutes)
- **type:**
  - `params vision lit PL_TARGET_DENSITY_PCT`
  - `chip whatif vision lit PL_TARGET_DENSITY_PCT=60`
  - `jobs` until done (about 51 s)
  - `result <tag>` (the tag it printed)
  - then `chip whatif vision lit CLOCK_PERIOD=5`, `jobs` and `result <tag>`: still met, but the slack is 🟠 +0.069 ns
  - then `chip whatif vision lit CLOCK_PERIOD=4`, `jobs` and `result <tag>`: 🔴 the flow stops; LibreLane reports hold
    violations at the three fast corners
  - then `chip whatif vision lit CLOCK_PERIOD=40`
- **you see:**
  - the setting explained, with its engine, safe range and documentation;
  - a full LibreLane flow on a **copy** (one more cell, setup slack +0.006 ns, still clean);
  - for 5 ns: orange slack; for 4 ns: a red banner with the reason taken from the flow log;
  - for `CLOCK_PERIOD=40`: "Blocked by the HARD RULES, nothing started".
- **say:**
  - The agent can experiment, but only on copies. The frozen design is untouched.
  - Tightening the clock shows the trade-off live: the margin shrinks (🟠), then the flow itself refuses (🔴).
  - The rule "never loosen the clock to pass timing" is enforced in code, not by asking the model nicely.

### 8. Agent engineering made visible (2 minutes)
- **type:** `loopdemo signoff kv`, then `harness facts kv`
- **you see:**
  - a loop trace (PLAN, then ACT, OBSERVE and CHECK per design, then STOP: 5 of 5 clean, tightest slack kv_attn_n16);
  - a harness score of 15/15 with a PASS gate.
- **say:**
  - An agent is a loop with a goal, a stop condition and a budget.
  - A harness turns "seems to work" into a score with a gate.
  - The same harnesses run in `make test` on every change.

### 9. The model itself, for contrast (2 minutes)
- **type:**
  - the sentence `which kv design has the most flip-flops?`
  - then `run the soc-kv experiment`, then `yes, run <id>` (the id it printed)
- **you see:**
  - the first answer after about 70 s: kv_attn_n16 with 277 flip-flops, a table, and the source;
  - "Nothing has started. Reply 'yes, run …'" after about 6 s;
  - the job starts after your reply.
- **say:**
  - Open questions still need the model and its tools, and on a 9B model that takes about a minute.
  - That is why everything routine is done in code.
  - A run never starts on the model's own word: the hook checks that **you** wrote "yes, run".

## If something goes wrong

| Symptom | Fix |
|---|---|
| `chip` unknown | the chat is on another profile: Cmd+N (new chat), or `hermes profile use chip` and restart the app |
| A window does not open | `chip close`, then try again; KLayout is in `/Applications/KLayout` |
| `magic ...` says XQuartz is not listening | run `bash scripts/hermes_start.sh` again (it starts XQuartz), or follow the header of `scripts/gui/open_gui.sh`; or skip Magic and use KLayout |
| A job seems stuck | `jobs`, then `job <id>`; only one physical flow runs at a time |
| The first answer is slow | the model was loading (cold start); the slash commands do not need it |

## What to claim, and what not to

- **Claim:**
  - every number on screen comes from a committed file in this repo;
  - the agent runs locally;
  - runs need your words;
  - the frozen designs cannot be changed by the agent.
- **Do not claim:**
  - a tapeout: it is a learning build, and no `cf` step is automated;
  - LLM-scale performance: the KV engine is 8 entries of 8 bits;
  - that the 9B model is reliable without the code around it: it is not, and that is the lesson.
