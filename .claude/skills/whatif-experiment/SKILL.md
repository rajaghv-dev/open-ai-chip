---
name: whatif-experiment
description: Safely try a changed synthesis, timing-constraint or OpenROAD-engine setting on a COPY of a design (build/whatif/<design>__<tag>/) and compare the result with the committed metrics, without touching designs/<d>/ or its committed run. Covers the HARD-RULE check, the patch text, the confirm gate, one physical flow at a time, the comparison table and verdict, sweeps over several values, cleanup, and the terminal route. Use when asked "what if we set X", "try a tighter clock", "does density 70 change anything", "sweep this parameter", or whenever a skill says to try a setting.
---

# What-if experiments: change constraints safely

The rule of this repo: an agent never edits a committed `designs/<d>/` file (a `config.json` or `*.sdc` edit makes the committed run stale; `scripts/flow/find_reusable_run.py` would rerun the flow).
So an experiment is a copy. The result is a table next to the committed numbers; the owner decides whether to apply a patch.

Companion skills: `tune-synthesis`, `tune-timing-sdc`, `tune-openroad-engines` (what to change and why), `harden-design` (how the flow runs and fails). Tools: Hermes desktop `examples/hermes_desktop/tool_server/whatif_tools.py`
(`docs/HERMES_DESKTOP.md` section "What-if experiments: change constraints safely"). `reference.md` has the exact tool arguments, the rule table, the files and the failure table.

## HARD RULES (CLAUDE.md, absolute; the tools enforce them, you must too)
- Never loosen `MAX_TRANSITION_CONSTRAINT` or `CLOCK_PERIOD` (25 ns) to make a gate pass; tightening (a shorter period, a lower slew limit) is fine for an experiment.
- Never `DISABLE_LVS`; never `SYNTH_STRATEGY` DELAY; keep `ERROR_ON_SYNTH_CHECKS` true; never weaken DRC, LVS, timing or precheck settings; never accept deleted logic.
- Do not touch fixed wrapper geometry or pins (`designs/user_project_wrapper*/fixed_dont_change`).
- One physical flow at a time: `docker ps` first, other sessions may be running; each flow is capped at 600 s (`FLOW_TIMEOUT`). After the first decisive failure stop and keep the log and run directory.
- Never edit committed `designs/<d>/` files from an agent; never commit `build/`; never run `cf` commands or publish anything.

## Steps (agent tools)
1. `param_info {design, key}`: current value, default, engine, meaning, safe range, the rule that applies, the design's own `//KEY` reasoning. No key: the tunable keys grouped by engine.
2. `propose_change {design, changes: {KEY: value}}`: each key `allowed` or `blocked` with the rule and reason, plus a unified diff of `config.json` as PATCH TEXT saved under `build/whatif/patches/`. Nothing is applied.
3. `whatif_run {design, changes, tag}`: FIRST call starts nothing and returns `confirm_id` and the exact plan; show it to the user and stop. Only after the user writes `yes, run <confirm_id>` call again with the `confirm_id`.
   It copies the design to `build/whatif/<d>__<tag>/` (rewriting every `dir::` path so it still resolves), applies the changes, runs LibreLane in the same container setup as `make gds` (tight profile) under the shared one-flow lock, then `check_signoff.py`.
4. `job_status {job_id}` until done, then `whatif_result {tag}`: table (std cells, flip-flops, die, utilisation, worst setup/hold and per corner, DRC/LVS/XOR/antenna, slew/cap/fanout violations, power, wall time, peak memory), a verdict built by code,
   and `committed_run_still_current` (must be true: the design was not touched).
5. Several values: `whatif_sweep {design, key, values}` (2 to 6, gated, sequential, one job), then `whatif_result {sweep_id}`.
6. Housekeeping: `whatif_list`, `whatif_clean {tag}` or `{all: true}` (only `build/whatif/`).
7. Report: the table, the verdict, the patch text, and what you did NOT do (design files unchanged). Say "no change recommended" when nothing improved. Do not apply the patch.

## Terminal route (Claude Code, no tool server)
`python3 examples/hermes_desktop/tool_server/whatif_tools.py prepare <design> <tag> KEY=VALUE ...` makes the rule-checked copy (exit 3 and the reason when blocked); then
`bash scripts/flow/whatif_flow.sh --dir build/whatif/<design>__<tag> --design <design> --tag <tag>` runs it (same container setup, 600 s cap, writes `whatif_resources.json`, `signoff.txt`).
To try an SDC change, edit the SDC inside the copy (never under `designs/`), then run the flow; never relax I/O delays, latencies, uncertainty or add false/multicycle paths to get a pass.
Read `build/whatif/<design>__<tag>/runs/<tag>/final/metrics.json` and compare with `designs/<design>/output/metrics.json`; `python3 scripts/flow/check_signoff.py <design> --metrics <file>` gives the sign-off verdict.

## Limits
- Not for the Caravel wrappers or macro-bearing designs (`MACROS`, `user_project_wrapper*`): they need exported macro views and fixed files. Use the macro (for example `tiny_ai_core`).
- RTL does not change, so simulation is not rerun; gate-level and precheck are not part of a what-if (they would need the committed flow).
- A what-if that fails a check is still a result; report it, do not tune the rules.
