---
name: tune-synthesis
description: Change and judge the synthesis settings (SYNTH_* variables of LibreLane 3.0.2: SYNTH_STRATEGY AREA 0..3, flatten or keep hierarchy, ABC buffering and sizing, MAX_FANOUT_CONSTRAINT, adder and multiplier mapping, synthesis checks) of a design in this repo, read synth_stat.rpt and synth_checks.rpt, and handle "logic lost" and signoff_allowances.json. Use when asked to shrink or speed up the synthesised netlist, compare synthesis strategies, see which block uses the area, fix a synthesis check or unmapped-cell error, or understand a flip-flop count that is lower than the RTL.
---

# Tune synthesis (Yosys + ABC)

Synthesis is step `06-yosys-synthesis` of the LibreLane flow (Yosys elaborates, ABC maps to sky130_fd_sc_hd). Its checks are steps 07
(unmapped cells), 08 (synthesis checks) and 09 (assign statements). Tables of every key are in `reference.md` (same folder). The
live list, the current values and the rules are the `param_info` tool; changes are tried with `whatif_run` (skill `whatif-experiment`).

## HARD RULES (CLAUDE.md, absolute; the what-if tools enforce them in code)
- Never `DISABLE_LVS` and never weaken DRC, LVS, timing or precheck settings.
- Never `SYNTH_STRATEGY` DELAY (`DELAY 0` .. `DELAY 4` are blocked; only `AREA 0` .. `AREA 3`).
- Keep `ERROR_ON_SYNTH_CHECKS` true (only the elaborate-only Caravel wrappers set it false) and never `SYNTH_ELABORATE_ONLY` true on a macro or engine.
- Never accept deleted logic: a `scripts/flow/signoff_allowances.json` entry needs a reason a reviewer can verify in the RTL.
- Never loosen `CLOCK_PERIOD` (25 ns) or `MAX_TRANSITION_CONSTRAINT` (0.75 ns) or `MAX_FANOUT_CONSTRAINT` to make a gate pass; tightening for an experiment is fine.
- Never edit committed `designs/<d>/` files from an agent (a `config.json` edit makes the committed run stale, `scripts/flow/find_reusable_run.py`).
  Experiments run on a copy under `build/whatif/`; an owner applies a patch by hand.
- One physical flow at a time (`docker ps` first); each flow is capped at 600 s (`FLOW_TIMEOUT`).

## Workflow
1. Read what the design already has: `designs/<d>/config.json` and its `"//KEY"` comments, `NOTES.md`, then `param_info {design, key}` for each key you
   want to touch (current value, default, meaning, rule). Committed synthesis settings are minimal: every design runs `AREA 0` (the default),
   `flatten`, `SYNTH_SIZING` false, `MAX_FANOUT_CONSTRAINT` 8 (user_proj_example 16, the template value); only `user_proj_example` sets `SYNTH_ABC_BUFFERING` false.
2. Read the committed result before changing anything: `designs/<d>/output/reports/synth_stat.rpt` (cell mix, area, per-module sections when hierarchy
   is kept), `synth_checks.rpt` (must say "Found and reported 0 problems"), `cell_usage.rpt`, `metrics.json` `design__instance__count__stdcell`,
   `design__instance__count__class:sequential_cell`, `design__instance__area`.
3. Pick ONE change with a stated reason (table "symptom -> key" below). `propose_change` first (rules, patch text), then `whatif_run` (confirm gate, copy
   under `build/whatif/`), then `whatif_result` (cells, flip-flops, area, setup/hold per corner, all signoff counts, verdict).
4. A synthesis change moves everything downstream (placement, routing, timing). Judge the final metrics, not synth_stat alone. Setup slack on the 25 ns clock is large
   (vision_block 13.5 ns), so area is the metric that usually matters; hold is the thin one (0.11 ns at min_ff).
5. A winning setting is reported with the measured table; the owner decides whether to apply the patch. Never claim an improvement from a run that failed a check.

## Symptom -> key
| symptom | try | note |
|---|---|---|
| cell count or area too high | `SYNTH_STRATEGY` `AREA 1`, `AREA 2`, `AREA 3` | one change at a time; AREA 3 (OpenROAD-flow-scripts area script, buffer/upsize/dnsize) can be larger |
| a long path through an adder (float prec_* designs) | `SYNTH_ADDER_TYPE` `RCA`/`CSA`/`FA`, `SYNTH_MUL_BOOTH` | read `timing_paths_max_ss.rpt` first |
| fanout or slew violations already at synthesis | `SYNTH_ABC_BUFFERING` true, or lower `MAX_FANOUT_CONSTRAINT` (tightening) | adds buffers |
| weak drivers on a slow path | `SYNTH_SIZING` true | costs area |
| which module dominates the area | `SYNTH_HIERARCHY_MODE` keep, or `SYNTH_KEEP_HIERARCHY_MODULES` | report only; the committed style is flatten |
| netlist `assign` statements | keep `SYNTH_DIRECT_WIRE_BUFFERING` true | step 09 |
| undriven or multi-driver warnings | fix the RTL (owner), never silence the check | step 08 |

## "Logic lost" and signoff_allowances.json
`check_signoff.py` elaborates the RTL with Yosys (`synth -flatten -noabc`), counts the flip-flop and latch output bits, and fails when the surviving
sequential cells are fewer (minus the allowance of the design). A strategy or ABC change must therefore keep the register count: `SYNTH_ABC_DFF` true
can merge identical flip-flops and trip it. Allowances are `"<design>": {"removed_registers": N, "reason": "..."}`; the reason must be verifiable in the RTL
(a constant or never-read register). Do not add one to make a what-if pass: a what-if that loses logic is a failed what-if. Wrappers carry `undriven_outputs` allowances
(owner decision, documented in the file). Read `scripts/flow/check_signoff.py` header for what each sub-check catches.

## Verify
- `whatif_result` verdict "CLEAN" plus the `signoff_check` tail ("=> PASS"), flip-flops equal to the committed count, and `committed_run_still_current` true.
- Cells and area from `metrics.json` of the copy (`build/whatif/<d>__<tag>/runs/<tag>/final/metrics.json`), never from memory.

## Tools and commands by name (self-contained; works outside Claude Code)
- `param_info {design, key?}`: current value, default, engine, safe range, rule. `propose_change {design, changes:{KEY:value}}`: allowed or blocked per key plus a patch text (never applied).
- `whatif_run {design, changes, tag}`: first call returns `confirm_id` and the plan and starts nothing; call again with `confirm_id` only after the user says `yes, run <confirm_id>`. Then `job_status {job_id}` until done and `whatif_result {tag}`.
  `whatif_sweep {design, key, values}` for 2 to 6 values, then `whatif_result {sweep_id}`. `whatif_list`, `whatif_clean {tag}`.
- Without the tool server: `python3 examples/hermes_desktop/tool_server/whatif_tools.py prepare <design> <tag> KEY=VALUE ...` (exit 3 and the rule when blocked), then
  `bash scripts/flow/whatif_flow.sh --dir build/whatif/<design>__<tag> --design <design> --tag <tag>` (one flow at a time, 600 s cap), then compare
  `build/whatif/<design>__<tag>/runs/<tag>/final/metrics.json` with `designs/<design>/output/metrics.json` and run `python3 scripts/flow/check_signoff.py <design> --metrics <that file>`.
- Never edit files under `designs/`; never apply a patch; never run `cf` commands. Rules above are absolute.
