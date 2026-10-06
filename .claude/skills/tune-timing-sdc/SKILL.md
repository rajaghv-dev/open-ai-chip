---
name: tune-timing-sdc
description: Change and judge timing constraints in this repo (CLOCK_PERIOD and its rule, the Caravel macro SDC base_*.sdc with its input and output delays, input transitions, clock latency and uncertainty, PNR_SDC_FILE versus SIGNOFF_SDC_FILE, setup and hold slack margins PL_RESIZER_* and GRT_RESIZER_*, the nine timing corners) and read timing_summary.rpt and the path reports. Use when asked to try a faster clock, explain a setup or hold violation or thin slack, compare corners, change I/O delays or margins, or understand why a macro uses the Caravel template SDC.
---

# Tune timing and the SDC

Timing is decided by three things: the constraints (the SDC and the `CLOCK_PERIOD`-style variables), the repair margins of the resizer, and the nine
corners that sign-off checks. Key tables, SDC statement map and report-reading guide are in `reference.md` (same folder). Use `param_info` for live values
and rules, `propose_change` / `whatif_run` / `whatif_result` for experiments (skill `whatif-experiment`).

## HARD RULES (CLAUDE.md, absolute; enforced in code by the what-if tools)
- Never loosen `CLOCK_PERIOD` (25 ns, 40 MHz on `wb_clk_i`) or `MAX_TRANSITION_CONSTRAINT` to make a gate pass. Tightening (a shorter period, a lower slew limit) is allowed for an experiment.
- Never `DISABLE_LVS`, never `SYNTH_STRATEGY` DELAY, keep `ERROR_ON_SYNTH_CHECKS` true.
- Constraints may only move in the tightening direction: lower `MAX_FANOUT_CONSTRAINT`, higher `CLOCK_UNCERTAINTY_CONSTRAINT`, `TIME_DERATING_CONSTRAINT`, `IO_DELAY_CONSTRAINT`, `OUTPUT_CAP_LOAD`.
- Never weaken DRC, LVS, timing or precheck settings: no negative `*_SLACK_MARGIN`, no `*_ALLOW_SETUP_VIOS` true, no change of `*_VIOLATION_CORNERS` / `STA_CORNERS` / `PNR_CORNERS`, no `RUN_MCSTA`/`RUN_POST_CTS_RESIZER_TIMING` off.
- Do not edit a committed `designs/<d>/*.sdc` or `config.json` from an agent: any non-markdown edit in a design directory makes its committed run stale. The Caravel macro SDC values
  are the environment, not a knob: never relax them (delete a `set_input_delay`, lower a latency, add a false or multicycle path) to turn a failure into a pass.
- Do not modify the fixed wrapper (`designs/user_project_wrapper*/fixed_dont_change`, `signoff.sdc`).
- One physical flow at a time (`docker ps`), each capped at 600 s.

## What the clock rule means
`CLOCK_PERIOD` creates the clock (`create_clock -period`), is passed to ABC (`abc -D`) and, in LibreLane's own `base.sdc`, scales `IO_DELAY_CONSTRAINT` (percent of the period).
All 25 designs use 25 ns. A what-if with 20 ns asks "would this design still close at 50 MHz?". It is allowed because it tightens. The answer is in the setup slack of the worst
corner; the committed design is never changed by it. A 30 ns what-if is refused (`R1-CLOCK`), also in `propose_change`.

## Two kinds of SDC in this repo
| design | SDC | where the I/O numbers come from |
|---|---|---|
| tiny engines, prec_*, kv_attn_*, vision_*, image_text_match, text_sentiment, audio_* | LibreLane `base.sdc` (image path: `librelane/scripts/base.sdc`) | variables: `IO_DELAY_CONSTRAINT` 20 percent, `CLOCK_UNCERTAINTY_CONSTRAINT` 0.25 ns, `CLOCK_TRANSITION_CONSTRAINT` 0.15 ns, `TIME_DERATING_CONSTRAINT` 5 percent, `OUTPUT_CAP_LOAD` about 33 fF, `MAX_FANOUT_CONSTRAINT`, `MAX_TRANSITION_CONSTRAINT` |
| `tiny_ai_core`, `soc_image_text_match`, `soc_kv_attn_n8` | `base_tiny_ai_core.sdc` / `base_soc.sdc` (`PNR_SDC_FILE` and `SIGNOFF_SDC_FILE` both) | the Caravel template macro SDC: clock latency 4.65..5.57 ns, clock transition 0.61 ns, per-port Wishbone delays and transitions, output load 0.19 pF, multicycle paths on `wbs_ack_o`/`wbs_cyc_i`/`wbs_stb_i` |
| `user_proj_example` | `base_user_proj_example.sdc` (`FALLBACK_SDC_FILE`) | the unmodified template |
| `user_project_wrapper*` | `signoff.sdc` (fixed) | the Caravel wrapper context |
Variables such as `IO_DELAY_CONSTRAINT` have no effect on a design whose SDC does not read them. `param_info` says which engine and constraint a key belongs to; check the SDC before expecting an effect.

## Workflow
1. Read the committed timing: `designs/<d>/output/reports/timing_summary.rpt` (9 corners, worst setup and hold per corner), `timing_paths_max_ss.rpt`, `timing_paths_min_ff.rpt`,
   `metrics.json` `timing__setup__ws__corner:*`, `timing__hold__ws__corner:*`. Committed numbers: vision_block setup +13.5 ns (max_ss), hold +0.110 ns (min_ff): hold is the thin one.
2. Decide the question (faster clock? more margin? which corner limits?). One change at a time.
3. `propose_change` (rules + patch text), `whatif_run` (copy under `build/whatif/`, confirm gate), `whatif_result` (per-corner table, verdict).
4. Read the limiting path in the copy's `runs/<tag>/57-openroad-stapostpnr/<corner>/max.rpt` or `min.rpt` (startpoint, endpoint, slew, delay, slack) before concluding.
5. Report the table. A tighter period that fails is information ("closes down to X ns"), not a defect of the committed 25 ns design.

## Margins and corners in one paragraph
`PL_RESIZER_SETUP_SLACK_MARGIN` (0.05 ns) / `PL_RESIZER_HOLD_SLACK_MARGIN` (0.1 ns) set how far past zero the post-CTS repair goes; `GRT_RESIZER_SETUP_SLACK_MARGIN` (0.025) /
`GRT_RESIZER_HOLD_SLACK_MARGIN` (0.05) do the same after global routing and only run when `RUN_POST_GRT_RESIZER_TIMING` is true (prec_* and user_proj_example). Raising them overfixes at the cost of buffers;
lowering below zero is blocked. Sign-off checks all nine corners (`nom/min/max` x `tt_025C_1v80`, `ss_100C_1v60`, `ff_n40C_1v95`): setup is worst at `ss`, hold at `ff`. The placement and repair steps use the default corner `nom_tt_025C_1v80`
unless `PNR_CORNERS`/`RSZ_CORNERS` say otherwise (blocked to change).

## Verify
`whatif_result` verdict, `violations` empty, `signoff_check` "=> PASS", `committed_run_still_current` true. Cite the table rows with their file (`runs/<tag>/final/metrics.json` of the copy).

## Tools and commands by name (self-contained; works outside Claude Code)
- `param_info {design, key?}`: current value, default, engine, safe range, rule. `propose_change {design, changes:{KEY:value}}`: allowed or blocked per key plus a patch text (never applied).
- `whatif_run {design, changes, tag}`: first call returns `confirm_id` and the plan and starts nothing; call again with `confirm_id` only after the user says `yes, run <confirm_id>`. Then `job_status {job_id}` until done and `whatif_result {tag}`.
  `whatif_sweep {design, key, values}` for 2 to 6 values, then `whatif_result {sweep_id}`. `whatif_list`, `whatif_clean {tag}`.
- Without the tool server: `python3 examples/hermes_desktop/tool_server/whatif_tools.py prepare <design> <tag> KEY=VALUE ...` (exit 3 and the rule when blocked), then
  `bash scripts/flow/whatif_flow.sh --dir build/whatif/<design>__<tag> --design <design> --tag <tag>` (one flow at a time, 600 s cap), then compare
  `build/whatif/<design>__<tag>/runs/<tag>/final/metrics.json` with `designs/<design>/output/metrics.json` and run `python3 scripts/flow/check_signoff.py <design> --metrics <that file>`.
- Never edit files under `designs/`; never apply a patch; never run `cf` commands. Rules above are absolute.
