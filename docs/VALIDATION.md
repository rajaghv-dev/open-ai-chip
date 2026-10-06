# Run validation (2026-10-07: clean validation and freeze)

Clean re-validation of all 25 designs of Makefile `ALL_DESIGNS`, then freeze (`designs/FROZEN.json`, `designs/FROZEN.md`).
No physical flow was run (`make gds` / `flow-all` never called); every design had a current run, so nothing was STALE.
Docker via `DOCKER_HOST=unix://$HOME/.colima/osl/docker.sock`, one container at a time, no other flow containers running.
Raw logs: `build/validation/test_full_2026-10-07.log` and `build/test_full/` (git-ignored).
Command: `bash tests/test_full.sh --synth-gl` (per design: run-state via `find_reusable_run.py`, `make simulate`, `make check`
= `check_signoff.py`, `make gl-final`, `make gl`; then adapter-test, soc-sim, soc-kv, caravel-rtl, caravel-gl), result line
"test-full: 130 PASS, 0 FAIL, 3 SKIP, 0 STALE in 430 s" (the 3 SKIP are the opt-in caravel-fullgl, caravel-sdf-wrapper, precheck).
Evidence column: `output/metrics.json` equals the run's `final/metrics.json` (parsed JSON equal), `error.log` empty,
`resources.json` `run_dir` = this run, `layout.png` present (checked with a script over all 25, 25 of 25).

| design | run dir | current | evidence | signoff | sim | gl | gl-final |
|---|---|---|---|---|---|---|---|
| user_proj_example | RUN_2026-10-05_18-17-43 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| vision_all_lit | RUN_2026-10-05_18-14-33 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| vision_block | RUN_2026-10-05_18-15-29 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| text_sentiment | RUN_2026-10-05_18-16-33 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| tiny_ai_core | RUN_2026-10-05_20-33-28 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| user_project_wrapper | RUN_2026-10-06_03-29-55 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| audio_pitch | RUN_2026-10-05_19-59-29 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| audio_onset | RUN_2026-10-05_20-00-31 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| image_text_match | RUN_2026-10-05_20-23-15 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| prec_bin | RUN_2026-10-05_20-16-32 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| prec_tern | RUN_2026-10-05_20-17-29 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| prec_int4 | RUN_2026-10-05_20-18-29 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| prec_int8 | RUN_2026-10-05_20-13-05 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| prec_fp8 | RUN_2026-10-05_20-28-04 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| prec_fp16 | RUN_2026-10-05_20-25-56 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| prec_bf16 | RUN_2026-10-05_20-30-14 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| soc_image_text_match | RUN_2026-10-06_08-18-20 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| user_project_wrapper_soc_itm | RUN_2026-10-06_08-21-27 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| kv_attn_n4 | RUN_2026-10-06_06-57-12 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| kv_attn_n8 | RUN_2026-10-06_07-00-35 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| kv_attn_n16 | RUN_2026-10-06_07-04-34 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| kv_attn_n8_int4 | RUN_2026-10-06_06-58-48 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| kv_attn_n8_ring | RUN_2026-10-06_07-02-36 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| soc_kv_attn_n8 | RUN_2026-10-06_08-09-32 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |
| user_project_wrapper_soc_kv | RUN_2026-10-06_08-13-27 | PASS | YES (metrics.json identical, layout.png, resources.json run_dir) | PASS | PASS | PASS | PASS |

Signoff: `python3 scripts/flow/check_signoff.py --all` also run separately: "check-all: 25 of 25 pass" (allowances unchanged:
24 flops in `kv_attn_n8_int4` [246 RTL vs 222 surviving], 204 undriven wrapper output bits x3 wrappers).

## System results (2026-10-07)

| target | result |
|---|---|
| `make test` | all sections PASS (incl. new `== frozen`) except 2 tests in `tests/tools` (RAG: `test_recall_at_k_v2_main_and_heldout`, `test_eval_rag_committed_retrieval_numbers_reproduce`, evidence recall 0.4 < 0.5). Not caused by this work: they fail identically with the 2026-10-07 doc edits (FROZEN.md, VALIDATION.md, CLAUDE.md) removed; another session is editing the RAG tests/corpus (uncommitted changes under `tests/tools/`, `tools/prompts/`, `examples/hermes_desktop/`) |
| `make test-full` system part (via `tests/test_full.sh --synth-gl`) | adapter-test (14 engines), soc-sim, soc-kv, caravel-rtl, caravel-gl all PASS |
| `make check-generated` | PASS (regeneration reproduces 41 files) |
| `make model-check` | PASS golden: 69 checks |
| `make freeze` / `make check-frozen` | wrote 25 designs, 523 input hashes, 29 model hashes; check PASS (602 hashes verified) |
| caravel-fullgl, caravel-sdf-wrapper, precheck | not re-run (opt-in, long); the 2026-10-06 evidence below still applies because no design file changed |

Freeze: see `designs/FROZEN.md` (unfreeze procedure, per-design numbers and hashes). Guard API for tools: `scripts/flow/frozen.py`.

---

# Run validation (2026-10-06)

Independent re-validation of every hardened design and every system run, done by parallel agents from the
evidence on disk, without re-running any physical flow (`make gds` / `flow-all` were never called).
Raw logs stay in `build/validation/` (git-ignored).

## Summary

| Check | Result |
|---|---|
| Run current (`scripts/flow/find_reusable_run.py`), run complete, `flow.log` ends "Flow complete.", `error.log` empty | 25 of 25 |
| Committed `output/metrics.json` byte/key-identical to the run's `final/metrics.json`; reports and `layout.png` from that run | 25 of 25 |
| Signoff (`scripts/flow/check_signoff.py`: DRC Magic + KLayout, LVS, XOR, antenna, setup/hold all 9 corners, no logic lost) | 25 of 25 PASS (allowances: 204 undriven wrapper outputs x3 wrappers, 24 duplicate flops in `kv_attn_n8_int4`) |
| RTL simulation (`make simulate`) | 25 of 25 PASS |
| Gate-level, synthesised netlist (`make gl`) | 25 of 25 PASS |
| Gate-level, routed netlist (`make gl-final`) | 25 of 25 PASS |
| README results tables and design README status lines vs `metrics.json` | match (`scripts/docs/tables.py --check`; `tests/check_docs.py` evidence check) |
| System: `make model-check`, `check-generated` (41 files), `adapter-test` (14 engines), `soc-sim`, `soc-kv`, `caravel-rtl`, `caravel-gl` | all PASS (re-run) |
| System: `caravel-fullgl`, `caravel-sdf-wrapper`, `precheck` (14/14) | evidence checked, not re-run (14 min / long runs; logs and `precheck/results/summary.tsv`) |

Notes from the validation:
- The full-Caravel sims and the precheck exist for `user_project_wrapper` (`tiny_ai_core`) only, not for the
  `soc_itm` / `soc_kv` wrappers.
- `build/` is shared: one `check_signoff.py` run failed transiently because another session cleaned `build/check` at the
  same moment; the immediate re-run passed. Run validations sequentially when several sessions work in the tree.
- The default Docker socket of a bare `docker ps` (`~/.colima/default`) is not this repo's; use
  `DOCKER_HOST=unix://$HOME/.colima/osl/docker.sock` (the Makefile and scripts already do).
- Ten `make gl` runs first failed with "Makefile: missing separator" because another agent was editing the Makefile at that
  moment; re-run afterwards, all PASS (table below).

## Gate-level, synthesised netlist, designs 14-25 (re-run after the Makefile edit)

| design | `make gl` |
|---|---|
| prec_fp8 | gl_sim: prec_fp8 PASS (2 s) |
| prec_fp16 | gl_sim: prec_fp16 PASS (2 s) |
| prec_bf16 | gl_sim: prec_bf16 PASS (1 s) |
| soc_image_text_match | gl_sim: soc_image_text_match PASS (3 s) |
| user_project_wrapper_soc_itm | gl_sim: user_project_wrapper_soc_itm PASS (6 s) |
| kv_attn_n4 | gl_sim: kv_attn_n4 PASS (0 s) |
| kv_attn_n8 | gl_sim: kv_attn_n8 PASS (1 s) |
| kv_attn_n16 | gl_sim: kv_attn_n16 PASS (1 s) |
| kv_attn_n8_int4 | gl_sim: kv_attn_n8_int4 PASS (1 s) |
| kv_attn_n8_ring | gl_sim: kv_attn_n8_ring PASS (1 s) |
| soc_kv_attn_n8 | gl_sim: soc_kv_attn_n8 PASS (2 s) |
| user_project_wrapper_soc_kv | gl_sim: user_project_wrapper_soc_kv PASS (4 s) |

## Part 1 (first 13 designs)

Date 2026-10-06. Docker via ~/.colima/osl/docker.sock (the default daemon socket was down; the Makefile/check_signoff use the osl socket). No make gds/flow-all/precheck run. Raw logs: build/validation/logs/<d>.{evidence,signoff,sim,gl,glfinal}.txt, reports_cmp.txt.

Columns: current = find_reusable_run.py exit 0 + final/gds + final/metrics.json present + flow.log ends 'Flow complete.' + error.log empty; evidence = output/metrics.json == run final/metrics.json (all keys), resources.json run_dir = that run, 17 (wrapper 15) output/reports files byte-identical to the run's step reports (collect.sh mapping, home->~, head -300), layout.png present; tables = README tables (tables.py --check: up to date) and the design README/NOTES status numbers agree with metrics.json.

| design | run dir | current | evidence | signoff | sim (time) | gl_synth (time) | gl_final (time) | tables |
|---|---|---|---|---|---|---|---|---|
| user_proj_example | RUN_2026-10-05_18-17-43 | YES | YES (equal, 314 keys) | PASS (exit 0, 1s) | PASS user_proj_example_tb: 28 checks (reset, count, Wishbone read/write, LA load/clock/reset) (exit 0, 0s) | gl_sim: user_proj_example PASS (1 s); synth_check_errors=0; total 4s | gl_sim: user_proj_example PASS (1 s); total 2s | YES |
| vision_all_lit | RUN_2026-10-05_18-14-33 | YES | YES (equal, 307 keys) | PASS (exit 0, 0s) | PASS vision_all_lit_tb: 29 cases, 158 checks (results, latency, back-pressure, protocol errors, reset) (exit 0, 1s) | gl_sim: vision_all_lit PASS (1 s); synth_check_errors=0; total 4s | gl_sim: vision_all_lit PASS (0 s); total 1s | YES |
| vision_block | RUN_2026-10-05_18-15-29 | YES | YES (equal, 309 keys) | PASS (exit 0, 0s) | PASS vision_block_tb: 540 cases, 2713 checks (results, latency, back-pressure, protocol errors, reset) (exit 0, 1s) | gl_sim: vision_block PASS (0 s); synth_check_errors=0; total 3s | gl_sim: vision_block PASS (1 s); total 2s | YES |
| text_sentiment | RUN_2026-10-05_18-16-33 | YES | YES (equal, 311 keys) | PASS (exit 0, 1s) | PASS text_sentiment_tb: 269 cases, 1358 checks (results, latency, back-pressure, protocol errors, reset) (exit 0, 0s) | gl_sim: text_sentiment PASS (1 s); synth_check_errors=0; total 4s | gl_sim: text_sentiment PASS (1 s); total 2s | YES |
| tiny_ai_core | RUN_2026-10-05_20-33-28 | YES | YES (equal, 310 keys) | PASS (exit 0, 1s) | PASS tiny_ai_core_tb: 784 cases, 39956 checks (24060 wishbone transactions; results, CYCLES<=16, irq, registers, byte lanes, window decode, protocol errors, back-to-back, reset) (exit 0, 1s) | gl_sim: tiny_ai_core PASS (1 s); synth_check_errors=0; total 7s | gl_sim: tiny_ai_core PASS (2 s); total 3s | YES |
| user_project_wrapper | RUN_2026-10-06_03-29-55 | YES | YES (equal, 280 keys) | PASS (exit 0, 0s) | PASS user_project_wrapper_tb: 784 cases, 39956 checks (24060 wishbone transactions; results, CYCLES<=16, irq, registers, byte lanes, window decode, protocol errors, back-to-back, reset) (exit 0, 1s) | gl_sim: user_project_wrapper PASS (2 s); synth_check_errors=0; total 7s | gl_sim: user_project_wrapper PASS (1 s); total 3s | YES |
| audio_pitch | RUN_2026-10-05_19-59-29 | YES | YES (equal, 311 keys) | PASS (exit 0, 0s) | PASS audio_pitch_tb: 370 recordings x3 passes, 6078 result beats, 12173 checks (values, order, stalls, full rate, garbage when idle, error flag, s_last, reset) (exit 0, 0s) | gl_sim: audio_pitch PASS (0 s); synth_check_errors=0; total 4s | gl_sim: audio_pitch PASS (0 s); total 1s | YES |
| audio_onset | RUN_2026-10-05_20-00-31 | YES | YES (equal, 310 keys) | PASS (exit 0, 0s) | PASS audio_onset_tb: 68829 beats x 3 passes (all 65536 windows, short streams, errors, resets; full-rate, gaps, stalls), 203193 output beats compared, 856018 checks (exit 0, 4s) | gl_sim: audio_onset PASS (7 s); synth_check_errors=0; total 11s | gl_sim: audio_onset PASS (8 s); total 9s | YES |
| image_text_match | RUN_2026-10-05_20-23-15 | YES | YES (equal, 311 keys) | PASS (exit 0, 1s) | PASS image_text_match_tb: 2079 cases, 10408 checks (results, latency, back-pressure, protocol errors, reset) (exit 0, 0s) | gl_sim: image_text_match PASS (1 s); synth_check_errors=0; total 5s | gl_sim: image_text_match PASS (1 s); total 2s | YES |
| prec_bin | RUN_2026-10-05_20-16-32 | YES | YES (equal, 309 keys) | PASS (exit 0, 0s) | PASS prec_bin_tb: 931 cases, 4668 checks (results, latency, back-pressure, protocol errors, reset) (exit 0, 0s) | gl_sim: prec_bin PASS (0 s); synth_check_errors=0; total 4s | gl_sim: prec_bin PASS (0 s); total 2s | YES |
| prec_tern | RUN_2026-10-05_20-17-29 | YES | YES (equal, 309 keys) | PASS (exit 0, 0s) | PASS prec_tern_tb: 931 cases, 4668 checks (results, latency, back-pressure, protocol errors, reset) (exit 0, 0s) | gl_sim: prec_tern PASS (0 s); synth_check_errors=0; total 4s | gl_sim: prec_tern PASS (0 s); total 1s | YES |
| prec_int4 | RUN_2026-10-05_20-18-29 | YES | YES (equal, 315 keys) | PASS (exit 0, 0s) | PASS prec_int4_tb: 931 cases, 4668 checks (results, latency, back-pressure, protocol errors, reset) (exit 0, 0s) | gl_sim: prec_int4 PASS (1 s); synth_check_errors=0; total 4s | gl_sim: prec_int4 PASS (0 s); total 1s | YES |
| prec_int8 | RUN_2026-10-05_20-13-05 | YES | YES (equal, 310 keys) | PASS (exit 0, 0s) | PASS prec_int8_tb: 931 cases, 4668 checks (results, latency, back-pressure, protocol errors, reset) (exit 0, 0s) | gl_sim: prec_int8 PASS (0 s); synth_check_errors=0; total 4s | gl_sim: prec_int8 PASS (1 s); total 2s | YES |

### Part 1 details

- Signoff (check_signoff.py): all 13 PASS. Register allowance 0 everywhere (RTL flops == surviving sequential cells: user_proj_example 33, vision_all_lit 10, vision_block 24, text_sentiment 12, tiny_ai_core 109, audio_pitch 21, audio_onset 25, image_text_match 39, prec_bin 19, prec_tern 28, prec_int4 30, prec_int8 35).
  - user_project_wrapper: elaborate-only (sequential cells counted 0; registers live in the macro); 204 undriven top-level output bits accepted (io_oeb, io_out, la_data_out) via signoff_allowances.json (owner decision 2026-10-06: tiny_ai_core keeps only Wishbone); check_signoff notes MAX_TRANSITION_CONSTRAINT = 1.5 (PDK default 0.75) - existing design setting, only noted here.
  - Notes (not failures): max-slew violations user_proj_example 482 (+1 max-cap), tiny_ai_core 195, audio_pitch 18, audio_onset 14, image_text_match 61, prec_tern 9, prec_int4 9, prec_int8 27; 0 for the rest.
- Reports: 17/17 identical to the run for 12 designs, 15/15 for user_project_wrapper (reports_cmp.txt). layout.png present for all 13.
- gl synth: `make gl` runs a real synthesis-only LibreLane (Yosys.Synthesis) on a copy of the config in build/gl/<d>/ (one Docker container at a time), then simulates the synthesised netlist; it does not trigger make gds and does not touch designs/. user_project_wrapper also runs macro-views --if-needed (writes build/macros only). gl-final reuses the routed netlist of the current run.
- Tables: `python3 scripts/docs/tables.py --check` -> README results tables up to date (generated from output/metrics.json/resources.json). README rows checked: cells/flops/die/setup/hold/slew/flow s/peak GB agree. Design README status lines vs metrics.json (cells/flops/setup/hold): audio_pitch 234/21/13.354, audio_onset 316/25/13.206, image_text_match 551/39/13.42/0.107, prec_bin 199/16.40/0.114, prec_tern 293/16.35/0.112, prec_int4 377/13.46/0.115, prec_int8 642/11.39/0.111, text_sentiment 200/12/14.75/0.108, vision_all_lit 169/10/16.07/0.108, vision_block 297/24/13.53/0.110, user_project_wrapper setup +1.46/hold +0.105: all match metrics.json. user_proj_example and tiny_ai_core have no design README; NOTES.md of user_proj_example (setup 5.9947) matches the table.

### Part 1 problems found

- None blocking. All 13 runs are current and complete, evidence matches, signoff passes, all RTL sims and both gate-level sims PASS.
- Observations: (1) the default Docker socket (~/.colima/default/docker.sock) is not running; tools use ~/.colima/osl/docker.sock, so bare `docker ps` fails without DOCKER_HOST. (2) Run-dir names (RUN_2026-10-05_..) are earlier than file mtimes (6 Oct) - UTC vs local clock, harmless. (3) Margins are positive but thin: setup +1.46 ns (tiny_ai_core, user_project_wrapper), hold slack 0.10 to 0.12 ns across designs.

## Part 2 validation (last 12 designs + system runs)
Date 2026-10-06. Docker daemon was DOWN (colima) the whole time; no flow run, no Docker used.

### Per-design

Item1 run current (find_reusable_run exit 0, final/gds + metrics.json present, flow.log ends 'Flow complete.', error.log empty): all 12 OK.
Item2 output/metrics.json byte-identical to <run>/final/metrics.json (cmp), resources.json run_dir = that run, output/reports/* and layout.png present: all 12 OK.

| design | run | signoff (check_signoff) | simulate | gl-final |
|---|---|---|---|---|
| prec_fp8 | RUN_2026-10-05_20-28-04 | PASS, 82 slew (report-only) | rc=0 0s: PASS prec_fp8_tb | rc=0 3s:  prec_fp8 PASS (2 s) |
| prec_fp16 | RUN_2026-10-05_20-25-56 | PASS, 36 slew | rc=0 1s: PASS prec_fp16_tb | rc=0 5s:  prec_fp16 PASS (3 s) |
| prec_bf16 | RUN_2026-10-05_20-30-14 | PASS, 45 slew | rc=0 0s: PASS prec_bf16_tb | rc=0 4s:  prec_bf16 PASS (2 s) |
| soc_image_text_match | RUN_2026-10-06_08-18-20 | PASS (1st call FAIL, see problem 1), 421 slew | rc=0 1s: PASS soc_image_text_match_tb | rc=0 9s:  soc_image_text_match PASS (7 s) |
| user_project_wrapper_soc_itm | RUN_2026-10-06_08-21-27 | PASS, allowance: 204 undriven top-level output bits (owner decision 2026-10-06; macro drives them) | rc=0 2s: PASS user_project_wrapper_soc_itm_tb | rc=0 8s:  user_project_wrapper_soc_itm PASS (7 s) |
| kv_attn_n4 | RUN_2026-10-06_06-57-12 | PASS, 415 slew | rc=0 0s: PASS kv_attn_n4_tb | rc=0 3s:  kv_attn_n4 PASS (1 s) |
| kv_attn_n8 | RUN_2026-10-06_07-00-35 | PASS, 551 slew, 1 max-cap | rc=0 0s: PASS kv_attn_n8_tb | rc=0 2s:  kv_attn_n8 PASS (1 s) |
| kv_attn_n16 | RUN_2026-10-06_07-04-34 | PASS, 1190 slew, 3 max-cap | rc=0 1s: PASS kv_attn_n16_tb | rc=0 3s:  kv_attn_n16 PASS (2 s) |
| kv_attn_n8_int4 | RUN_2026-10-06_06-58-48 | PASS, flop allowance 24 (246 RTL vs 222 surviving) | rc=0 1s: PASS kv_attn_n8_int4_tb | rc=0 2s:  kv_attn_n8_int4 PASS (1 s) |
| kv_attn_n8_ring | RUN_2026-10-06_07-02-36 | PASS, 666 slew | rc=0 0s: PASS kv_attn_n8_ring_tb | rc=0 2s:  kv_attn_n8_ring PASS (1 s) |
| soc_kv_attn_n8 | RUN_2026-10-06_08-09-32 | PASS, 836 slew | rc=0 1s: PASS soc_kv_attn_n8_tb | rc=0 5s:  soc_kv_attn_n8 PASS (4 s) |
| user_project_wrapper_soc_kv | RUN_2026-10-06_08-13-27 | PASS, same 204-bit undriven allowance | rc=0 1s: PASS user_project_wrapper_soc_kv_tb | rc=0 6s:  user_project_wrapper_soc_kv PASS (5 s) |

Test counts (simulate): prec_* 931 cases/4668 checks; soc_itm and wrapper_soc_itm 2079 cases/50947 checks; kv_attn_n4 1245 rec/12882; n8 1521/15834; n16 2073/22170; n8_int4 1521/16074; n8_ring 1767/22344; soc_kv_attn_n8 and wrapper_soc_kv 1521 rec/31647. Full logs: build/validation/_p2/<d>_{simulate,gl-final}.log.
`make gl` (synthesis-only netlist) was NOT run: it calls LibreLane in Docker (daemon down) and rm -rf's build/gl/<d>. gl-final (routed netlist, host iverilog) reuses the current run's final/nl; it never calls make gds.

Wrapper views (item 2): build/macros/soc_image_text_match/SOURCE.txt run = RUN_2026-10-06_08-18-20 = current macro run; build/macros/soc_kv_attn_n8/SOURCE.txt run = RUN_2026-10-06_08-09-32 = current. All 16 listed sha256 per macro re-hashed (shasum) and match; macro gds byte-identical to run final/gds. (Did not call --export-views, to avoid any overwrite; wrapper gl-final's macro-views prerequisite ran --if-needed as a no-op.)

README (item 5): `python3 scripts/docs/tables.py --check` -> results tables up to date. designs/<d>/README.md status lines vs metrics.json: std cell counts (design__instance__count__stdcell) match for all 10 engine designs (1404, 1932, 1754, 3201, 1679, 2566, 4169, 2394, 2621, 4514); setup WNS prec_fp8 0.259, fp16 0.111, bf16 0.044, kv_n4 9.35, n8 10.74, n16 8.77, soc_kv 1.448 (wrapper) all match; die sizes match; flop counts (soc_itm 393, soc_kv 570, int4 222, ring 198) match check_signoff. Wrapper READMEs: soc_kv setup +1.448 / hold +0.105 match.

### System runs

| target | rc | time | PASS line |
|---|---|---|---|
| make model-check | 0 | 6 s | PASS golden: 69 checks (last model) |
| make check-generated | 0 | 7 s | check-generated: PASS (regeneration reproduces 41 files) |
| make adapter-test | 0 | 9 s | adapter tests: 14 engines PASS |
| make soc-sim | 0 | 27 s | SOC_SIM: firmware exit PASS after 1899308 cycles |
| make soc-kv | 0 | 15 s | SOC_SIM: firmware exit PASS after 1020782 cycles |
| make caravel-rtl | 0 | 65 s | Monitor: tiny_ai_core Caravel firmware (RTL) PASS |
| make caravel-gl | 0 | 75 s | Monitor: tiny_ai_core Caravel firmware (GL) PASS |
| caravel-fullgl | not re-run | - | evidence checked: build/caravel/work/run_fullgl.log 'tiny_ai_core Caravel firmware (GL) PASS' (checkbits ab60 -> ab61, 4.10 ms sim) |
| caravel-sdf-wrapper | not re-run | - | evidence checked: run_sdf_wrapper_{nom_tt,nom_ss,max_ss}*.log; nom_tt: 'wrapper GL+SDF PASS at 1189000' |
| precheck | not re-run | - | evidence checked: precheck/results/summary.tsv 14/14 PASS (written 09:45, after user_project_wrapper metrics 09:01) |

Caravel rungs exist for user_project_wrapper (tiny_ai_core) only, as documented; none for soc_itm/soc_kv wrappers.

### Part 2 problems found

1. Transient: first `check_signoff.py soc_image_text_match` FAILED ('Yosys: Can't open output file build/check/rtl_ff/soc_image_text_match/ff.json'). Cause: another session was running at the same time and (re)creating/cleaning build/check (a vvp for audio_onset was running); os.makedirs happens before the write, so likely a concurrent rm of the dir. Re-run immediately after: PASS (393 flops = 393 surviving). Not a design problem; shared build/ tree is racy with parallel agents.
2. `make gl` (synth netlist) and Docker-dependent flows could not be exercised: Docker daemon not running (cannot connect to colima.sock). find_reusable_run's container check swallows this (treats as no container), so currency results are valid.
3. Minor: caravel-gl/rtl take the newest designs/user_project_wrapper/runs/*/final/pnl by mtime (not the run find_reusable_run calls current); the three wrapper runs present (03-23-43, 03-29-55 + older) are same RTL per the script comment. Not checked further.
4. No stale runs, no metrics/evidence mismatches, no error.log content found in the 12 designs.
