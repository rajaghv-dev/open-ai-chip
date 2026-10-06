# Other local models for the Hermes agents

The agents (`tools/hermes_agent.py`, the harness, RAG, KLayout view agent, Open WebUI preset) default to `hermes3:8b` through
Ollama. This page says how to point them at another local model, and what a same-day comparison on this repo's workflow showed.
Registry: [examples/models.json](../examples/models.json). Measured results: [examples/models_results.json](../examples/models_results.json)
(raw report `build/agent/models_20261006_205129.json`, git-ignored). Runner: `tools/eval/compare_models.py`.

## Switch the model

Nothing changes when nothing is set (default `hermes3:8b`, same options: temperature 0, seed 42, num_ctx 8192).

```
export HERMES_MODEL=granite4.1:3b                       # every entry point reads it (tools/hermes_agent.py)
build/agent/venv/bin/python tools/hermes_agent.py --model granite4.1:3b "How many std cells does vision_block have?"
build/agent/venv/bin/python tools/eval/run_eval.py --model granite4.1:3b
build/agent/venv/bin/python examples/hermes_harness/eval_harness.py --model granite4.1:3b --only baseline,all
build/agent/venv/bin/python examples/hermes_rag/eval_rag.py --model granite4.1:3b --only rag+router
build/agent/venv/bin/python examples/hermes_klayout_gui/demo.py --live --model granite4.1:3b   # transcript goes to build/agent/klayout_gui/, the committed one is hermes3:8b
```

`--model` exists on `hermes_agent.py`, `harness.py`, `eval_harness.py`, `rag_agent.py`, `eval_rag.py`, `run_eval.py`, and the KLayout
`agent.py` / `demo.py`. Models with a thinking mode (tags starting `qwen3`, `gemma4`) get `"think": false` on `/api/chat`
(`NO_THINK_PREFIXES` in `tools/hermes_agent.py`; `HERMES_THINK=on` turns that off). `run_eval.py --mode native` uses Ollama's `tools` field.

Open WebUI: `install_preset.py` creates the default preset (hermes3:8b) as before; with `--model <tag>` it creates one extra
preset "Hermes chip agent (<tag>)" next to it, same tools and system prompt:

```
build/agent/venv/bin/python examples/hermes_desktop/install_preset.py --model granite4.1:3b
```

Then pick that preset in the model menu (the desktop app shows the same list). Run `ollama pull <tag>` first.

## What "suitable for this flow" means (fixed before the runs)

A model is suitable if, in prompt mode (the mode all loops in this repo use), it gets all of:
chip eval (`tools/eval/run_eval.py`, 15 questions) >= 12/15; harness config `all` (`eval_harness.py`) >= 14/15;
KLayout demo (`demo.py --live`) >= 4/5 scenarios with the right tool sequence. RAG (12 + 5) is reported but not a gate.
Recommendation rule: the smallest model that meets all three, plus the most accurate one.

## Candidates

Newest-generation small instruction models from three different families that list tool support on their Ollama page
(checked 2026-10-06, Ollama 0.35.1), at most three, plus the baseline. Older generations (qwen2.5, llama3.2) were not evaluated.

## Results (one run each, 2026-10-06; source examples/models_results.json)

| model | disk | memory (ollama ps) | chip eval /15 (prompt) | tok/s | median s | harness baseline / all /15 | RAG router main /12, held-out /5 | KLayout /5 | suitable |
|---|---|---|---|---|---|---|---|---|---|
| hermes3:8b (baseline) | 4.7 GB | 6.0 GB | 13 | 45.0 | 3.7 | 13 / 15 | 8, 4 | 5 | yes |
| granite4.1:3b | 2.1 GB | 2.9 GB | 11 | 78.0 | 1.6 | 11 / 8 | 6, 5 | 1 | no |
| gemma4:e2b | 4.6 GB | 250 MB shown (see caveats) | 2 | 82.3 | 0.6 | 2 / 2 | 2, 0 | 5 | no |
| qwen3.5:4b | 3.3 GB | 3.4 GB | 2 (HTTP 500) | 51.2 | n/a | 2 / 2 (HTTP 500) | 7, 4 | not run (HTTP 500) | no |

Additional, native mode (Ollama `tools` field) chip eval only: qwen3.5:4b 14/15 (56.1 tok/s, median 4.5 s), gemma4:e2b 9/15
(76.2 tok/s, median 0.9 s). The harness, RAG and KLayout loops do not have a native mode, so these were not run for them.

Failures, from the reports:
- hermes3:8b: chip eval q02 (worst setup slack corner), q04; harness `all` 15/15; RAG fails r02, r05, r08, r09, h01.
- granite4.1:3b: works in prompt mode, tool calls are valid, but q03, q04, q12, q13 fail; the harness guardrails made it worse
  (`all` 8/15 vs baseline 11/15); KLayout 1/5 (wrong tool sequence in 4 scenarios).
- gemma4:e2b: emits valid tool calls, but with think=false the reply after the tool result is empty; with thinking on it
  invented a tool result (1500 std cells for vision_block; the file says 297). Passes 5/5 KLayout scenarios, which only check the tool sequence.
- qwen3.5:4b: Ollama parses `<tool_call>` itself with a Qwen parser and returns HTTP 500 ("qwen3.5 tool call parsing failed") for the
  Hermes JSON tool-call form, so prompt mode fails on every tool question. RAG 7/12 and 4/5 are the questions answered without a tool
  call (RAG router runs the search itself), so read that row with care.

## Recommendation

- Meeting all thresholds: only hermes3:8b. No smaller model tested met them in prompt mode, so the default stays.
- Smallest suitable: none today. granite4.1:3b (2.1 GB, 78 tok/s) is the best small fit for chip questions (11/15 at 1.7x the speed) but
  fails the harness and KLayout gates.
- Most accurate: hermes3:8b in prompt mode (chip 13/15, harness 15/15). qwen3.5:4b in native mode scored 14/15 on the chip eval, one
  more than hermes3:8b in prompt mode and 3.3 GB on disk, so it is the one worth a follow-up: it needs a native-mode loop in the
  harness (not built; today's loops use the Hermes prompt format), after which the full suite could be repeated.

## Caveats

One run per model and config; 15 + 15 + 12 + 5 questions and 5 scenarios, so one question is 7 percentage points. The prompts, the
harness guardrails and the score checks were written and tuned on hermes3:8b, which favours it. KLayout scenarios score the tool
sequence only, not the answer text. `ollama ps` memory is the size Ollama reports after the chip eval; for gemma4:e2b it printed
250 MB (probably the per-layer embeddings and vision parts are not counted), so treat that cell as unreliable. Latency and tok/s
depend on the machine. Model licences: see the registry (three Apache-2.0, hermes3 under the Llama 3 community licence).
