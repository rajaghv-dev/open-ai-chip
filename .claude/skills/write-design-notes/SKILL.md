---
name: write-design-notes
description: Write or refresh designs/<d>/NOTES.md the way this repo does (required headings, per-section sources, every number cited to a file, valid mermaid, flip-flop and slew reconciliation, "Intuitions and insights"). Use when a design has just been hardened and output/metrics.json exists, when the "== notes" check in tests/run_tests.sh fails, or when a re-run changed the numbers in an existing NOTES.md.
---

# Write design notes

Reference examples to copy the shape from (read one before writing):
[designs/prec_int8/NOTES.md](../../../designs/prec_int8/NOTES.md) (stream engine, 250 lines) and
[designs/tiny_ai_core/NOTES.md](../../../designs/tiny_ai_core/NOTES.md) (Wishbone core, 303 lines).
Longer tables and checklists: [reference.md](reference.md).

## Rules that never bend
1. Every number names its source file in the same sentence (`synth_stat.rpt`, `metrics.json` key, `config.json`, `spec.md` section). A number you computed yourself says "my sum" / "my division".
2. Never invent. If a value is not in a report or log, write "not reported" (or "not verified") instead of estimating. Missing negative tests, missing runs, unexplained behaviour are stated as such (prec_int8 "Negative tests: none ...", "I did not find which script treats it as non-fatal").
3. Re-read the file you cite. After any re-run of the flow, refresh every number from `designs/<d>/output/`; stale numbers are the usual defect.
4. Do not edit anything but `designs/<d>/NOTES.md`. `rtl/*_rom.v` is generated; never edit it.
5. No emojis. Plain ASCII punctuation as in the existing notes.

## Required headings (tests/run_tests.sh "== notes")
Checked only for designs that have `output/metrics.json`. `##` or `###`, case-insensitive prefix match, any of the alternatives:
architecture; data flow; verification; layout; synthesis; floorplan; placement; clock tree | cts; routing; timing; drc; lvs; power | ir; antenna; run time; reproduce; intuitions and insights.
Layout used by the examples: `## What it is`, `## Architecture`, `## Data flow`, `## Verification`, `## Layout (GDSII)`, `## From RTL to GDSII: what each step did` with `###` Synthesis, Floorplan, Placement, Clock tree, Routing, Timing, DRC, LVS, Power / IR drop, Antenna slew capacitance; then `## Run time and memory`, `## Reproduce`, `## Intuitions and insights`.

Check with: `python3 .claude/skills/write-design-notes/check_notes.py designs/<d>/NOTES.md` (same heading regex as the test, plus the mermaid lint below). Authoritative: `bash tests/run_tests.sh` section "== notes".

## Where each section's facts come from
| Section | Source |
|---|---|
| What it is | `designs/<d>/README.md`, `model/*/spec.*`, `make simulate DESIGN=<d>` PASS line, `make flow-all` log (`build/flow_<d>.log`), `output/resources.json` wall time |
| Architecture | `rtl/*.v` (ports, registers, widths); one mermaid flowchart + a register table (name, bits, role) |
| Data flow | the model's golden run on one concrete input, e.g. `python3 model/precision_hw/golden.py --trace <fmt> <9 pixels>` (only precision_hw has `--trace`; other models: `python3 model/<m>/golden.py <args>`, see image_text_match NOTES); per-edge table; say which columns are your replay |
| Verification | `tb/*_tb.v`, `tb/vectors.hex` provenance, PASS line (cases, checks), `golden.py --check`, gate-level stage logs `build/flow/<d>/stage_gl_*.log`, `stage_check.log`; list negative tests or say none |
| Layout | `output/layout.png`, `design__die__bbox`, `design__core__bbox`, `design__instance__utilization`, `design__instance__area__stdcell`, fill/tap/diode counts (`design__instance__count__class:*`) |
| Synthesis | `reports/synth_stat.rpt`, `synth_checks.rpt`, `synthesis__check_error__count`, `design__lint_warning__count` |
| Floorplan | `reports/floorplan.txt`, `config.json` DIE_AREA |
| Placement | `reports/placement_global.txt`, `placement_detailed.txt`, `design__instance__count__class:timing_repair_buffer` |
| Clock tree | `reports/cts.rpt`, `clock__skew__worst_setup/hold`, `design__instance__count__hold_buffer` |
| Routing | `reports/routing_global.txt`, `routing_detailed.txt`, `route__drc_errors*`, `route__wirelength*`, `global_route__*` |
| Timing | `reports/timing_summary.rpt` (per corner table), `timing_paths_max_ss.rpt`, `timing_paths_min_ff.rpt` (worst setup/hold path), `timing__setup__wns`, `timing__hold__wns` |
| DRC / LVS | `drc_magic.rpt` (COUNT), `drc_klayout.json` (sum entries, say so), `lvs_netgen.rpt` ("Circuits match uniquely" + device/net counts), `manufacturability.rpt` |
| Power / IR drop | `power__total` etc. in `metrics.json`, `irdrop.rpt` (its own total differs; say both) |
| Antenna, slew, cap | `antenna__violating__nets`, `design__max_slew/cap/fanout_violation__count`, `cell_usage.rpt` |
| Run time | `output/resources.json` (`wall_s_total`, `container_peak_mem_gb`, per-step `wall_s`; profile and cpus/memory under `limits`) |
| Reproduce | the real commands: `make simulate DESIGN=<d>`, `make flow-all DESIGN=<d>`, model scripts |

Always link the report files at the end of each step subsection, with a path relative to the design's NOTES.md (for example the link target `output/reports/synth_stat.rpt`, written as a normal markdown link with the file name as its text).

## Mermaid validity
- Open with ```` ```mermaid ```` and a type (`flowchart LR|TB`). Quote every label that has any of `( ) [ ] { } / , : ; < > & # % ' | + = *`: `A["s_data 8 bit, pixel in bits 3..0"]`, subgraphs `subgraph IO["IO: stream handshake"]`, edge labels `-->|"wbs_cyc, stb"|`.
- Ids are unique within a diagram and alphanumeric/underscore; do not reuse an id with a second label (use a new id). Ids must not be keywords (`end`, `graph`, `subgraph`, `style`, `class`).
- One idea per diagram; keep labels short; no trailing `;` inside quotes; balanced quotes.
- Lint: `check_notes.py` flags unquoted special characters, duplicate ids with different labels, odd quote counts (flowcharts only; sequence/state diagrams are skipped).

## Flip-flop count reconciliation (always do it when the numbers differ)
1. Declared RTL bits: sum register widths from `rtl/*.v` ("3+4+9+... = 41").
2. Synthesised: `design__instance__count__class:sequential_cell` in `metrics.json`, `synth_stat.rpt` (`dfxtp_*`), `build/flow/<d>/stage_check.log` ("registers: RTL N (allowance 0), surviving sequential cells M").
3. Explain the difference: one-hot FSM recoding adds bits (`06-yosys-synthesis/yosys-synthesis.log`: "mapping auto encoding to `one-hot`", n states instead of ceil(log2 n) bits); bits pruned because never read, e.g. zero-weight channels (image_text_match: 41 + 2 - 4 = 39, [designs/image_text_match/NOTES.md](../../../designs/image_text_match/NOTES.md), the register-count paragraph before its "Data flow" heading).
4. Per-register evidence: `python3 scripts/flow/check_signoff.py <d> --breakdown`. The check compares against the elaborated count, not the source count; a deliberate removal needs `scripts/flow/signoff_allowances.json` `removed_registers` with a reason.

## Slew violations: classify honestly
`check_signoff.py` prints slew/cap counts as notes and never fails on them. Report `design__max_slew_violation__count` per corner (`timing_summary.rpt` shows ss corners). Then trace violating pins to their driver in the routed netlist: nets driven directly by input ports whose SDC transition (e.g. 0.84 / 0.92 ns on `wbs_dat_i` / `wbs_adr_i`) exceeds the limit (0.75 ns) are environment-limited and cannot be fixed by resizing; nets driven by internal cells are fixable. State the counts of each class, the repair margins tried (`config.json` key `//SLEW`: 70 % OOM, 40 % worse, 20 % chosen), and that a lower wrapper count is not a better result (tiny_ai_core NOTES "The slew story"). If you did not classify, say so.

## Intuitions and insights checklist (final section; numbered or bold-led paragraphs, each with numbers + sources)
- Why this is a neural network and not a rule: what was learned (weights/thresholds from `model/*/weights.json`), what a hand rule would miss, accuracy and agreement with fp32.
- Architecture lesson: neural part versus glue, by flip-flops and cells; where ROM constants went (folded into logic, no storage).
- Where the area goes: synth cells vs final cells = repair buffers + clock buffers + diodes + taps + fill; die set by pins or PDN, not logic.
- Timing headroom: worst setup/hold with the corner and path; why the period is generous; what limits (hold, slew).
- Physical failures and what they teach: every failed attempt (congestion, OOM, hold, unpowered diodes) with the fix and its cost, from `config.json` comments and READMEs.
- Verification lessons: what the testbench catches, what it does not (negative tests), gate-level vs RTL, signoff register check.
- Takeaway comparing with sibling designs (cite their metrics).

## Workflow
1. Confirm `designs/<d>/output/metrics.json` and `output/reports/` exist; if not, the flow is not done (do not run flows from this skill).
2. Read the examples, `README.md`, `rtl`, `tb`, model golden run.
3. Fill sections from the table above, dumping numbers with `python3 -c` on `metrics.json` rather than from memory.
4. Run `check_notes.py`, then `bash tests/run_tests.sh` ("== notes" section) if time allows.
5. Final skim: every sentence with a digit has a file name or "my sum".
