---
name: tune-openroad-engines
description: Change and judge the parameters of the OpenROAD engines that LibreLane 3.0.2 runs in this repo - floorplan (FP_SIZING, DIE_AREA, FP_CORE_UTIL), IO pin placement (IO_PIN_*), PDN, global placement (PL_TARGET_DENSITY_PCT, GPL_CELL_PADDING, routability and timing driven), the resizer (DESIGN_REPAIR_* slew and cap margins, PL_RESIZER_* and GRT_RESIZER_* slack margins), clock tree synthesis (CTS_*), global routing (GRT_ADJUSTMENT, RT_MAX_LAYER, overflow), detailed routing (DRT_*), antenna and diode settings, fill. Use when asked to fix congestion, slew or cap violations, thin hold or setup slack after routing, an out-of-memory repair step, high or low utilisation, clock skew, antenna violations or DRC after routing by changing a flow setting.
---

# Tune the OpenROAD engines

Each LibreLane step calls one OpenROAD engine; each engine has config variables. This skill is the map from symptom to variable, with the repo's chosen values and the reasons
(from the `"//KEY"` comments of the configs). The per-key tables (default, repo value, safe range, symptom, verification) are in `reference.md`; the engines themselves are described in
`docs/OPENROAD_ENGINES.md` (section links in the tables). `param_info {design, key}` gives the live value and rule of any key; experiments go through `whatif-experiment`.

## HARD RULES (CLAUDE.md, absolute; enforced in code by the what-if tools)
- Never loosen `MAX_TRANSITION_CONSTRAINT` or `CLOCK_PERIOD` (25 ns) to make a gate pass; never `DISABLE_LVS`; never `SYNTH_STRATEGY` DELAY; keep `ERROR_ON_SYNTH_CHECKS` true.
- Never weaken DRC, LVS, timing or precheck settings. In engine terms: no negative slack margin, no `*_ALLOW_SETUP_VIOS` true, no `RUN_*` signoff or repair step off
  (`RUN_ANTENNA_REPAIR`, `RUN_DRT`, `RUN_CTS`, `RUN_MCSTA`, ...), no `ERROR_ON_*` false, no change to corner lists or to DRC/LVS/extraction tool settings (`MAGIC_*`, `KLAYOUT_*`, `LVS_*`, `RCX_*`).
- Never accept deleted logic (`signoff_allowances.json` needs a reason verifiable in the RTL).
- Do not modify fixed wrapper geometry or pin locations (`designs/user_project_wrapper*/fixed_dont_change`): on the Caravel wrappers every `DIE_AREA`, `FP_*`, `IO_*`, `PDN_*`, `RT_*` change is blocked.
- Never edit committed `designs/<d>/` files from an agent (staleness: any non-markdown edit makes the committed run stale). Experiments run on copies under `build/whatif/`.
- One physical flow at a time (`docker ps` first); capped at 600 s. A flow that ran out of memory is a result: keep the log and run directory, change one thing, stop after the first decisive failure.
- Congestion (GRT-0116) is a placement and pin problem; `GRT_ALLOW_CONGESTION` stays false.

## Workflow
1. Name the symptom and find the evidence: `designs/<d>/output/reports/*` (floorplan.txt, placement_global.txt, placement_detailed.txt, cts.rpt, routing_global.txt, routing_detailed.txt, timing_summary.rpt),
   `metrics.json`, the run's step logs. The harden-design failure table (`.claude/skills/harden-design/reference.md`) lists what already happened in this repo.
2. Look the engine up below, read the key's `//KEY` comment in the design config and `param_info` (safe range, rule).
3. Change ONE key (or one family) with `propose_change`, then `whatif_run`; for a range use `whatif_sweep` (2 to 6 values, sequential).
4. `whatif_result`: verdict, per-corner slack, violation counts, utilisation, wall time and peak memory. Decide with numbers; cite the files.
5. Report the table and the patch text. The owner applies a patch by hand if wanted (then `make gds` reruns the flow because the config changed).

## Symptom -> engine -> key (see reference.md for ranges)
| symptom | engine (step) | try |
|---|---|---|
| GPL-0301 or utilisation above 80 percent, repair out of memory | ifp (13) | bigger `DIE_AREA` (absolute sizing) toward 40 percent utilisation |
| area wasted (utilisation under 30 percent) | ifp | smaller `DIE_AREA`; but pins may limit the die (109 pins need about 2.3 um pitch on a 250 um edge) |
| congestion, long wires | gpl (28), ppl (25) | `PL_TARGET_DENSITY_PCT` lower or higher by 10 points, `GPL_CELL_PADDING`, pin order (owner), `GRT_ADJUSTMENT` |
| repair step out of memory (step 32 or 41) | rsz | lower `DESIGN_REPAIR_MAX_SLEW_PCT` / `GRT_DESIGN_REPAIR_MAX_SLEW_PCT`: 70 and 40 percent ran out of the 8 GB container; 20 percent is the safe value for big designs |
| max slew or cap violations after route | rsz (41) | raise the post-GRT margins in steps of 10; port-driven slews from the Caravel SDC are environment-limited, not fixable |
| setup or hold slack thin after route | rsz (37, post-GRT timing) | `PL_RESIZER_*_SLACK_MARGIN`, `RUN_POST_GRT_RESIZER_TIMING` true with `GRT_RESIZER_*_SLACK_MARGIN` (never negative) |
| clock skew, many clock buffers | cts (35) | `CTS_SINK_CLUSTERING_SIZE`, `CTS_SINK_CLUSTERING_MAX_DIAMETER`, `CTS_DISTANCE_BETWEEN_BUFFERS` (tiny_ai_core and soc_* use 8, 20, 30) |
| DRC after routing | drt (46) | `DRT_OPT_ITERS` up |
| antenna violations | ant (40-48) | `GRT_ANTENNA_REPAIR_ITERS/MARGIN`, `DRT_ANTENNA_REPAIR_*`, `DIODE_ON_PORTS` (in for the macros), `HEURISTIC_ANTENNA_THRESHOLD` |
| IR drop / PDN check | pdn (21), psm (58) | `PDN_MULTILAYER`, strap pitch (macros keep defaults; wrapper values are fixed) |

## Lessons already paid for (do not repeat)
- The 70 percent slew margin: with the Caravel SDC `wbs_dat_i` / `wbs_adr_i` arrive at 0.84 / 0.92 ns, above the 0.75 ns limit; repair chased unfixable nets until the 8 GB container died; 40 percent gave 247 violations
  against 195 at 20 percent (`designs/tiny_ai_core/config.json` `//SLEW`). The 12 tiny designs run 40 (they have no such inputs), the 9 larger ones run 20.
- 80 x 80 um at 82 percent utilisation: repair out of memory; 120 um (about 36 percent) fixed it (`image_text_match` `//DIE_AREA`).
- Wrapper congestion was cured by 109 ordered pins and a shorter macro placement, never by a looser setting.
- Antenna: macros use `DIODE_ON_PORTS` in with heuristic insertion off; wrappers set `RUN_ANTENNA_REPAIR` false (a router diode in the wrapper is unpowered: 70 LVS errors).

## Verify
`whatif_result` verdict CLEAN, `signoff_check` "=> PASS", `committed_run_still_current` true; the metric that motivated the change moved the right way (cite the table rows), memory and wall time did not blow up.

## Tools and commands by name (self-contained; works outside Claude Code)
- `param_info {design, key?}`: current value, default, engine, safe range, rule. `propose_change {design, changes:{KEY:value}}`: allowed or blocked per key plus a patch text (never applied).
- `whatif_run {design, changes, tag}`: first call returns `confirm_id` and the plan and starts nothing; call again with `confirm_id` only after the user says `yes, run <confirm_id>`. Then `job_status {job_id}` until done and `whatif_result {tag}`.
  `whatif_sweep {design, key, values}` for 2 to 6 values, then `whatif_result {sweep_id}`. `whatif_list`, `whatif_clean {tag}`.
- Without the tool server: `python3 examples/hermes_desktop/tool_server/whatif_tools.py prepare <design> <tag> KEY=VALUE ...` (exit 3 and the rule when blocked), then
  `bash scripts/flow/whatif_flow.sh --dir build/whatif/<design>__<tag> --design <design> --tag <tag>` (one flow at a time, 600 s cap), then compare
  `build/whatif/<design>__<tag>/runs/<tag>/final/metrics.json` with `designs/<design>/output/metrics.json` and run `python3 scripts/flow/check_signoff.py <design> --metrics <that file>`.
- Never edit files under `designs/`; never apply a patch; never run `cf` commands. Rules above are absolute.
