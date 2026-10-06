# Hermes local agent

How to run every Hermes example from the Mac terminal, with diagrams: [HERMES_FROM_TERMINAL.md](HERMES_FROM_TERMINAL.md).

A local, offline question-answering agent for this repo's chip results. A Nous Research Hermes 3 model (8B, 4-bit)
runs in Ollama on the Mac; it can only call the read-only tool layer `tools/eda_tools.py` (metrics.json, GDS/LEF
via KLayout, signoff reports, precheck summary). Nothing leaves the machine; no flow, Docker or network is used.

## Setup

```
ollama serve &                      # if curl -s localhost:11434/api/tags fails
ollama pull hermes3:8b              # 4.7 GB download, one time
build/agent/venv/bin/python tools/hermes_agent.py "How many standard cells does vision_block have?"
build/agent/venv/bin/python tools/hermes_agent.py            # interactive (empty line quits)
python3 tools/eval/ground_truth.py                           # regenerate tools/eval/questions.json from repo files
build/agent/venv/bin/python tools/eval/run_eval.py --mode prompt   # eval -> build/agent/eval_<timestamp>.json
```

Each tool call (name, args, truncated result) is logged to stderr; the answer goes to stdout.
Limits per question: 6 tool calls, 300 s HTTP timeout, tool results truncated to 3500 characters for the model.

## Model, memory, speed

- Tag `hermes3:8b` (digest 4f6b83f30b62): Llama 3.1 architecture, 8.0B parameters, quantisation Q4_0, 4.7 GB on disk,
  context 131072 (we use 8192), capabilities completion + tools.
- Loaded size per `ollama ps`: 5.8 GB, 100% GPU (Apple M4 Pro, 24 GB). It unloads itself 5 minutes after last use.
  Docker/Colima was not running during the eval; with a 16 GB VM running, 5.8 GB plus 16 GB still fits in 24 GB but leaves little headroom.
- Generation about 47.6 tokens/s in the latest scored run (`build/agent/eval_20261006_143402.json`: median 2.9 s per question, range 0.85 s
  to 10.62 s, the slowest being q01; 0 or 1 tool calls per question). The previous run (`eval_20261006_121338.json`, stale q03) measured 47.0
  tokens/s, median 2.9 s; the earlier 12/15 run (`eval_20261006_105552.json`) 49.2 tokens/s and 2.4 s; latency varies from run to run.
- Settings: temperature 0, seed 42, num_ctx 8192, num_predict 700.

## Which tool-calling method worked

Both were tried on the same 15 questions.

| Method | Flag | Score | Why |
|---|---|---|---|
| Ollama `/api/chat` `tools` field | `--mode native` | 6/15 | Works mechanically (the model emits structured tool calls), but the hermes3 Ollama template drops the `system` message whenever `tools` is set, so none of our rules (answer only from tools, say unknown, argument recipes) reach the model. It then guesses, apologises, or invents an answer. |
| Hermes function-calling prompt (system prompt with `<tools>` JSON, `<tool_call>` tags parsed from the text, results returned as `<tool_response>`) | `--mode prompt` (use this) | 13/15 | Our system prompt is honoured. |

## Eval (15 questions, ground truth computed from repo files by `tools/eval/ground_truth.py`)

Scores, honestly: native mode 6/15; prompt mode 13/15 (fails q02 and q04), re-scored after a scorer fix, in
`build/agent/eval_20261006_121338.json`, and again 13/15 after the q03 ground-truth refresh (`build/agent/eval_20261006_143402.json`). Before the fix the same behaviour was recorded as 12/15 (first system prompt V2, rules plus tool recipes;
and still 12/15 after V3, the common-mistake list and worked examples plus a shim that retries
`read_metrics(keys=[partial name])` as a substring `pattern`). The model is not perfectly stable between prompt changes.
A larger Hermes (70B) does not fit in 24 GB.
Scoring is automatic (numeric tolerance, name match after removing the "Source:" tail, yes/no, "unknown" keywords).
Two scorer defects were found and fixed: an early one wrongly passed q05 because the tool call was echoed in the citation, and
`strip_source` in `tools/eval/run_eval.py` cut the whole answer when the answer itself STARTED with a "Source:" line. The
q05 "fail" in the old 12/15 was therefore a scorer artefact, not a model change. Caveat: the baseline's q05 answer names `prec_fp16`
(so the keyword check passes) but its cited counts (12345, 54321) are invented after a tool error; the pass is by the keyword, not
by grounded numbers. One run per configuration on 15 questions is an anecdote; see `examples/hermes_harness/README.md` for the
harness variants. Harness results (`examples/hermes_harness/results_summary.json`): baseline 13/15, + guardrails 13, + grounding 13,
+ `pick_extreme` 15, all features 15, all + plan 14; `eval_harness.py --gate 15` exits 0 and `--gate 16` exits 1 (the gate compares the
"all" score).

| Question | Expected | Answer (first line) | Tools | Result |
|---|---|---|---|---|
| std cells of vision_block | 297 | 297 standard cells | read_metrics | PASS |
| worst setup slack of prec_fp16 and corner | 0.1106 ns at max_ss_100C_1v60 | -16.93 ns at max_ff_n40C_1v95 (picked the wrong value and invented the sign) | read_metrics | FAIL |
| design with most flip-flops | soc_kv_attn_n8 (570) | soc_kv_attn_n8, 570 | compare_designs | PASS |
| smallest std-cell area (designs with cells) | vision_all_lit (1084.79 um2) | user_project_wrapper, 0 um2 (ignored the "has cells" condition) | compare_designs | FAIL |
| prec format with most std cells | prec_fp16 (1932) | prec_fp16 named, but the listed counts are invented after a tool error (keyword check passes) | compare_designs | PASS (by keyword) |
| die size of tiny_ai_core | 250 x 250 um | 250.0 x 250.0 um | layout_summary | PASS |
| macro instances in user_project_wrapper | 1: tiny_ai_core | 1, tiny_ai_core | layout_summary | PASS |
| wbs_dat_i pins on tiny_ai_core | 32 | 32 | find_pins | PASS |
| image_text_match DRC and LVS clean | yes | yes | signoff_summary | PASS |
| max-slew violations, soc_image_text_match | 421 | 421 | read_metrics | PASS |
| precheck checks passing | 14 of 14 | 14 | precheck_summary | PASS |
| std cells prec_fp16 minus prec_int8 | 1290 | 1290 | compare_designs | PASS |
| ratio prec_fp16 / prec_int8 | 3.009 | 1932 / 642 = 3.01 | compare_designs | PASS |
| selling price per chip (unanswerable) | unknown | unknown | none | PASS |
| measured silicon yield (unanswerable) | unknown | unknown | none | PASS |

Ground truth refreshed (q03): `tools/eval/questions.json` was written before `soc_kv_attn_n8` (570 flip-flops,
`designs/soc_kv_attn_n8/output/metrics.json`) was hardened. It was regenerated with `tools/eval/ground_truth.py` (only q03 changed:
`soc_image_text_match (393)` -> `soc_kv_attn_n8 (570)`) and the eval re-run: still 13/15, q03 passes with the new answer, q02 and q04
fail as before (`build/agent/eval_20261006_143402.json`). The previous 13/15 (`eval_20261006_121338.json`) had been scored against the
stale q03.

## Good at / bad at

- Good: one-call lookups of layout, pins, signoff and precheck facts; simple differences and ratios (it shows the two numbers);
  refusing questions outside the repo data with "unknown".
- Bad: choosing the right element from a long list (min versus max over corners, "has cells" filters, restricting a comparison
  to the prec_* family); it sometimes calls the right tool with a too-narrow argument. Always check the cited numbers; every
  answer names its source tool. It is a convenience layer, not a signoff authority: for decisions use `make` targets and the reports.

## Safety design

Read-only by construction: `hermes_agent.py` imports only `eda_tools`, accepts only tool names present in `eda_tools.TOOLS`,
and executes them only via `eda_tools.call`. The tools read files under the repo (and render PNGs into `build/agent/renders/`);
there is no shell, file-write, Docker or network tool, and the model has no way to name one. The only network use is HTTP to localhost:11434.
Tool errors are returned to the model as data. Tool output is untrusted text but is never executed.

## Using the same tools from other clients

`tools/mcp_server.py` (agent A) exposes the same `eda_tools` over MCP stdio:
`build/agent/venv/bin/python tools/mcp_server.py`. Register that command in Claude Code, Claude Desktop or any MCP client
(see tools/README.md, "MCP server"). Larger hosted models call the identical tools and avoid the 8B argument mistakes above.

## Limits

- 8B at 4 bits: roughly 80% on this set, not stable across prompt edits; no memory between questions.
- Only data that exists in committed outputs/reports (and `build/precheck`) can be answered; nothing about price, yield or schedule.
- "Latest precheck run" means the newest `build/precheck/results_*` directory; `classify_slew` needs a local flow run.
- Questions are about repo data as of the eval; rerun `ground_truth.py` after re-hardening designs.

RAG (documentation search with citations, `search_docs`, BM25; a deterministic router retrieves first for why/how questions; recall@1 5/10 -> 8/10, answerable 2/10 tools-only -> 4/10 model-chosen retrieval -> 6/10 retrieve-first, 5/10 hand-checked, held-out 4/5): `examples/hermes_rag/README.md`.

KLayout view control (offscreen viewer and live-window option; 7 read-only commands, text-only model, 5/5 scripted scenarios with the right tool sequence): `examples/hermes_klayout_gui/README.md`.

Other local models (switch with `HERMES_MODEL` or `--model`, comparison, thresholds): [AGENT_MODELS.md](AGENT_MODELS.md).
