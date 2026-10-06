# Test matrix: every example, what tests it, what is left

Scope: the chip and SoC side. The agent/tools side (KLayout/OpenROAD GUI, RAG, MCP, Hermes) has its own matrix:
[tools/TEST_MATRIX_TOOLS.md](tools/TEST_MATRIX_TOOLS.md). All numbers below are from `bash tests/run_tests.sh` and
`bash tests/test_full.sh` on the development machine (other sessions were running at the same time, so seconds are indicative).

## The two gates

| Gate | Command | Needs | Contents |
|---|---|---|---|
| fast | `make test` (`tests/run_tests.sh`) | iverilog, python3; optional riscv64-elf-gcc and `build/agent/venv` | sections: structure, upstream, config, rtl, wrapper, model, sim, negative, negative-all, docs, notes, adapter, soc, tools, tables |
| full | `make test-full` (`tests/test_full.sh`, flags via `FLAGS="--precheck --synth-gl --fullgl --sdf --quick --only 'a b' --with-test"`) | the fast gate's tools, committed runs under `designs/*/runs/`, `build/caravel` for the Caravel items | per design: run-state (current/STALE), `make simulate`, `make check`, `make gl-final`; once: adapter-test, soc-sim, soc-kv, caravel-rtl, caravel-gl; opt-in: synth-netlist `make gl` (Docker), precheck (Docker), caravel-fullgl (~14 min), caravel-sdf-wrapper (CVC image) |

`test_full.sh` never calls `make gds`. A design whose newest run is not current (`scripts/flow/find_reusable_run.py`) is printed as
STALE, and its check and gate-level items are skipped (not failures). `make gl-final` and `make check` read the routed netlist and
metrics of the existing run and need no Docker. `make gl` (synthesis-only netlist) runs LibreLane in Docker, so it is opt-in
(`--synth-gl`) and reported SKIP when the daemon is down.

## Per design

Legend: V = RTL simulation of the self-checking testbench on the generated vectors (== sim or == wrapper); NV = negative test (corrupted
expected value in a copy of the vectors, testbench must FAIL); NR = negative test with a broken RTL copy; L = iverilog -Wall lint;
C = config.json guard checks; N = NOTES.md required headings; D = inventory and evidence checks of `tests/check_docs.py`
(files present, in tables.py ORDER and README, README Status numbers equal `output/metrics.json`); A = through the Wishbone adapter
(`tests/adapter/run.sh`, also with corrupted vectors for three engines); full = items of `make test-full` (simulate, check, gl-final).

| Example | Fast gate | Full gate | Remaining gap |
|---|---|---|---|
| user_proj_example | V (counter tb), NR (counter +2), L, C, N, D, upstream sha256 | simulate, check, gl-final | none known |
| vision_all_lit, vision_block, text_sentiment | V, NV, NR, L, C, N, D, A, model golden `--check` (tiny_ai), regeneration | same | none known |
| tiny_ai_core | V (Wishbone tb), NV, L, C, N, D, golden `--check` | same | no RTL-mutation negative test |
| user_project_wrapper (tiny_ai_core) | wrapper structure (one `tiny_ai_core mprj`, no glue, header equals template when `build/template` exists), V, NV, L, C, N, D | same, plus Caravel RTL (caravel-rtl) and hybrid gate level (caravel-gl) | caravel-fullgl and SDF are opt-in only |
| audio_pitch, audio_onset | V, NV, L, C, N, D, A, regeneration | same | no `golden.py --check` (the testbench is the check); no RTL-mutation negative test |
| image_text_match | V, NV, L, C, N, D, A, golden `--check`, regeneration | same | no RTL-mutation negative test |
| prec_bin, prec_tern, prec_int4, prec_int8, prec_fp8, prec_fp16, prec_bf16 | V, NV (all seven: prec_int8 in `== negative`, the others in `== negative-all`), L, C, N, D, A, precision_hw golden `--check`, regeneration | same | no RTL-mutation negative test (only the vectors are corrupted) |
| soc_image_text_match | V (Wishbone tb), NV (negative-all), L, C, N, D | same | the macro is not inside a Caravel sim (only `user_project_wrapper` with tiny_ai_core is) |
| soc_kv_attn_n8 | V, NV (negative-all), L, C, N, D, SoC firmware `make soc-kv` (PicoRV32 + KV firmware) | same | not in a Caravel sim |
| user_project_wrapper_soc_itm, user_project_wrapper_soc_kv | wrapper structure, V, NV (negative-all), L, C, N, D | same | not in a Caravel sim; no SDF run |
| kv_attn_n4, kv_attn_n8, kv_attn_n16 | V (1245 / 1521 / 2073 records), NV (n8 in `== negative`, n4 and n16 in `== negative-all`), L, C, N, D, golden `--check`, regeneration; n8 also A | same | the other four variants are not behind the adapter (by design, docs) |
| kv_attn_n8_int4, kv_attn_n8_ring | V, NV (negative-all), L, C, N, D, golden `--check` (recall figures), regeneration | same | not behind the adapter |

## Cross-cutting

| Area | Test | Gap |
|---|---|---|
| Structure | required files, no symlinks, no `$HOME` path or reference-repo names in sources (`== structure`) | markdown files are not scanned for `/Users` (sources only) |
| Config | `== config`: required keys, `ERROR_ON_SYNTH_CHECKS` true, no `DISABLE_LVS`, no `RUN_LINT_CHECK`, no SYNTH_STRATEGY DELAY, `dir::` paths exist, wrapper MACROS | clock period and slew limit are only checked by their absence of override, not by value |
| Models | golden `--check` for tiny_ai, image_text_match, precision_hw, kv_attention; `scripts/check_generated.sh` regenerates all six model dirs in a scratch tree and diffs; a mutated tiny_ai threshold makes golden fail | audio_pitch and audio_onset have no `--check` |
| Adapter | `tests/adapter/run.sh`: all 14 stream engines behind `wb_stream_adapter.v`; negative: corrupted vectors for vision_block, kv_attn_n8, audio_pitch must FAIL (env `VEC_DIR`) | the four other KV variants are not wired |
| Firmware | `make soc-sim` (PicoRV32, 784 cases, cycle table) and `make soc-kv` in `== soc` when riscv64-elf-gcc exists, NOTE otherwise | no negative test of the firmware flow |
| Caravel | caravel-rtl, caravel-gl in `test-full`; caravel-fullgl, SDF opt-in; not in `make test` (downloads) | only the tiny_ai_core wrapper is simulated inside Caravel |
| Precheck | `make precheck` opt-in in `test-full` (`--precheck`, Docker) | not run when Docker is down |
| Signoff evidence | `make check` in `test-full` (`scripts/flow/check_signoff.py`); `make test` compares README Status numbers with `output/metrics.json` (`tests/check_docs.py evidence`) | only numbers written in a recognisable form (N std cells, N flip-flops, W x H um, setup/hold slack ns, max_ss/nom_ss/min_ss WNS) are compared |
| README tables | `scripts/docs/tables.py --check` (`== tables`) | none |
| Docs | `check_docs.py links` (relative links of every tracked `*.md`, anchors not checked), `targets` (every `make <t>` in a code span or code block exists; SPEC.md, LOCAL_RUN_PLAN.md, docs/SOC_PLAN.md and docs/slides/README.md are skipped: historic plans and other repositories' targets), `inventory`, self-test `tests/lib/check_docs_selftest.sh` | prose mentions of make targets outside code spans are not checked |
| Tools (agents, MCP, KLayout/OpenROAD) | `== tools`: `build/agent/venv/bin/python -m pytest -q tests/tools`; details in tools/TEST_MATRIX_TOOLS.md | needs the venv; NOTE when it is missing |

## How each new negative test was proven

Each case in `== negative-all` flips one expected value (`tests/lib/mutate_vectors.py`) and requires exit status non-zero, no `PASS` line
and a `FAIL` message; the same testbench and unmodified vectors are shown passing in `== sim` or `== wrapper` of the same run. The
documentation checks are proven by `tests/lib/check_docs_selftest.sh` (10 cases: a good scratch tree passes, each injected fault is
rejected). Running `bash tests/lib/check_docs_selftest.sh` alone prints one `ok` line per case.

## Findings while writing the tests

- The evidence check first stumbled over `designs/soc_kv_attn_n8/README.md`, whose Status paragraph continues with the sibling
  `soc_image_text_match` figures (3201 cells, 393 flip-flops); those are labelled as the sibling's, so the checker cuts the paragraph at
  "Compared with"/"sibling". All other quoted numbers equal `metrics.json`.
- `.claude/skills/soc-run/SKILL.md` writes `make caravel-{rtl,gl}`-style globs; they are ignored by the targets check.
