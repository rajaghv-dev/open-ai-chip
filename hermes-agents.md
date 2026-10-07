# Hermes Agent in open-ai-chip: how it is wired, and why

This page covers how Nous Research's **Hermes Agent** desktop app (Hermes.app, `hermes` CLI, config in `~/.hermes`)
is connected to this repo. Every part is listed with what it does, the intuition behind it, and the file that holds it.

Related pages:
- Full feature map, diagrams and status tables: [docs/HERMES_AGENT_INTEGRATION.md](docs/HERMES_AGENT_INTEGRATION.md).
- Narrated demos: [docs/HERMES_DEMOS.md](docs/HERMES_DEMOS.md).

**Status words.**
- **verified**: tried on this Mac and seen to work.
- **verified in isolated home**: applied to a throw-away `HERMES_HOME` under `build/`. The real `~/.hermes` was not touched.
- **pending owner apply**: waits for the owner to run the setup script with `--apply`.
- **built**: the file exists but has not been run through Hermes yet.
- **in progress**: not finished yet.

## 1. The one-paragraph picture

Hermes is the **brain and the chat window**: a local model (Ollama `qwen3.5-64k:9b`) runs a tool-calling loop. The
repo is the **hands**: a tool server offers a fixed list of safe verbs (read metrics, open a GDS, digest a log,
start a gated flow). They meet over **MCP**.

The design aims for every capability the model needs and no capability it could misuse:
- It cannot edit files.
- It has no terminal.
- It cannot start a physical flow without your typed confirmation.
- It cannot weaken a signoff rule.

When the local model is out of its depth, it can hand the question to Claude (`ask_claude`), again only with your
confirmation.

```
Hermes.app / hermes CLI  ──(profile "chip")──>  local model on Ollama 127.0.0.1:11434
        │ reads .hermes.md (rules + routing), skills from .claude/skills
        │ every tool call passes through the pre_tool_call hook (block / ask / allow)
        ▼
tools/hermes_mcp_bridge.py (stdio MCP)  ──HTTP 127.0.0.1:8770──>  tool server (examples/hermes_desktop/tool_server)
                                                                     ├─ repo files: metrics.json, NOTES.md, logs
                                                                     ├─ flow jobs via make (one at a time) → LibreLane in Docker
                                                                     ├─ KLayout / Magic windows
                                                                     └─ build/agent/ (memory, RAG index, hook log)
```

## 2. Setup: one script, reviewed before it touches anything

- **What:** `bash scripts/hermes_agent_setup.sh` prints a diff of what it would change in `~/.hermes`.
  - `--apply` makes the change after a backup to `~/.hermes/backups/open-ai-chip-<timestamp>/`.
  - `--uninstall --apply` removes it.
  - The logic is in `scripts/hermes/setup_profile.py`.
- **Why a diff first:** `~/.hermes` is your personal agent with other uses. Nothing in this repo writes there silently.
  Running the script twice gives the same result. The script never reads `.env`, `auth.json` or other secrets.
- **Why a separate `chip` profile:** the repo's MCP server, deny rules, hook and local-only model stay away from your
  default profile. A mistake here cannot leak into your other Hermes use. Pick it in the app or use `hermes -p chip`.
- **Status:** verified in isolated home (apply, re-run unchanged, uninstall, re-apply); pending owner apply.
- **Gotcha:** after any edit of the hook scripts, run `--apply` again. It refreshes the hook consent entry.

## 3. The profile: what the model can and cannot reach

- **Model:** `qwen3.5-64k:9b`, local, with a 64k context.
  - Measured: 51 of 58 tool-calling cases correct (87.9 %), `examples/hermes_desktop/eval_tools/results_summary_hermes.json`.
  - `hermes3:8b`, the obvious-sounding choice, scored 6 of 15 with native tool calling. The name "Hermes" in the app
    and in the model is a coincidence of brand, not a fit.
- **Demo models:** `hermes3:8b`, `gemma3:4b-it-qat`, `gemma4:12b` and `mistral-nemo` also appear in the model picker
  (provider "Local Ollama (demo models)"), with the default model unchanged.
  - Measured on one question: only the default answered correctly (table in the integration doc, "Models for demos").
  - Use them to show that **the model matters more than the prompt**.
  - `gemma3:4b-it-qat` cannot call tools at all (Ollama returns HTTP 400).
- **Toolsets:** only `skills` and `session_search`. There is no `file` toolset and no `terminal`.
  - Insight: with `file` on, the model read raw files instead of calling the precise tools and got worse.
  - Turning off `file` and `tool_search` took one 8-case set from 1/8 to 8/8 (`build/agent/tool_eval_PLAN.md`).
  - **Fewer, better-described tools beat more tools** for a 9B model.
- **Tool list:** a curated "core" of 38 tools plus `ask_claude` and `claude_status`.
  - `tools/hermes_tools.json` gives each tool a description in one fixed shape: USE WHEN / NOT FOR / ARGS / EXAMPLE.
    Small models copy examples far better than they follow prose.
  - `--demo-tools` adds 5 more: log digest and the what-if tools.

## 4. Context: how Hermes knows this repo

- **`.hermes.md`** (repo root, about 1.9k tokens) is generated by `scripts/docs/make_hermes_context.py` from the master
  prompt. `make test` checks that it is current.
  - Hermes loads exactly **one** context file, first match wins: `.hermes.md` > `AGENTS.override.md` > `AGENTS.md` > `CLAUDE.md`.
  - So `.hermes.md` must itself carry the HARD RULES. It replaces `CLAUDE.md` for Hermes; it does not add to it.
  - It stays short because every token of context is a token the 9B model does not spend on the question.
- **`CLAUDE.md`, section "For Hermes"** gives the same rules in four lines, for any session that reads `CLAUDE.md`.
- **Skills:** `skills.external_dirs` points at `.claude/skills`, so Claude Code and Hermes share the **same 11 skills**
  with no copy.
  - Existing workflows: harden-design, add-tiny-engine, precision-variant, write-design-notes, wrapper-build, soc-run.
  - Tuning: tune-synthesis, tune-timing-sdc, tune-openroad-engines, whatif-experiment. Their keys are checked against
    LibreLane's variable list.
  - Demos: chip-demos.
- **Project:** `open-ai-chip`, with the repo as its only folder. `../open-ai-silicon` is reference material only, so it
  is left out on purpose.
- **Verify recipe:** `.hermes/environment.json` has `make test` only. `make check` needs Docker results, so it is not
  a safe "is the repo OK" test.

## 5. The bridge and the tool server: verbs, not file access

- **`tools/hermes_mcp_bridge.py`** is a stdio MCP server.
  - It reads the tool server's OpenAPI and exposes the curated operations, starting the tool server if it is down.
  - Hermes names its tools `mcp__chip__<tool>`.
- **Why a bridge and not native Hermes plugins:** the same tool server already serves the other front ends. MCP keeps
  the repo independent of Hermes internals.
- **Tool groups** (the `capability_map` tool lists them live):

| Group | Tools (examples) | Intuition |
|---|---|---|
| Ask about numbers | `read_metrics`, `compare_designs`, `signoff_summary` | Numbers come from `metrics.json`, never from the model's memory |
| Ask why | `rag_answer`, `rag_search`, `notes_section`, `search_docs` | Answers are quotes with `file:line`, not paraphrase |
| Logs | `list_logs`, `read_log`, `log_digest`, `run_summary`, `diagnose` | A 10k-line flow log becomes the five lines that matter |
| See the layout | `open_gds`, `gui_command`, `klayout_view`, `render_png` | Text such as "show met1, zoom to the pins" drives KLayout and Magic |
| Run | `run_make`, `confirm_run`, `job_status` | Gated: see section 6 |
| What-if | `param_info`, `propose_change`, `whatif_run`, `whatif_sweep` | Experiments run on **copies** under `build/whatif/`, never on the frozen design |
| Experiments | `run_experiment` (soc-kv, precision table) | The repo's own measurements, re-run on request |
| Memory | `remember`, `recall` | Run history in `build/agent/memory/`, not in Hermes's global memory |
| Proof | `proof_local` | Shows the model, URL and repo path, so you can see it is local and tied to this repo |
| Escalate | `ask_claude`, `claude_status` | Claude takes over a hard task when you confirm |

- **Argument repair** (`normalize_tools.py`): small models write `"<design>"` or `kvattn n8`. The server fixes a
  near-miss design name or rejects a placeholder with a clear message, so one bad argument does not derail the session.

## 6. Safety: three layers, each catching what the others cannot

1. **Approvals** (Hermes core, `approvals.deny`, about 50 globs) cover terminal-level commands such as `cf *`,
   `git push*`, `rm -rf*`, `docker run*`, `make gds*` and edits under `designs/ shared/ model/`.
   - Insight: a dry run showed Hermes's built-in list allows `make gds` and `cf push`. Generic agents do not know which
     commands are expensive or irreversible in **this** repo.
2. **`pre_tool_call` hook** (`scripts/hermes/hooks/pre_tool_call.py`, `fail_closed`, 66 test cases) sees every tool call
   and its arguments.
   - It blocks `write_file`/`patch`, reads of secrets, and anything that touches a HARD RULE (`CLOCK_PERIOD`,
     `MAX_TRANSITION_CONSTRAINT`, `DISABLE_LVS`, `SYNTH_STRATEGY DELAY`, `ERROR_ON_SYNTH_CHECKS`).
   - It blocks flows on frozen designs.
   - It shows an **approval card** for physical flows.
   - Every decision is logged to `build/agent/hermes_hook.log`.
   - Insight: HARD RULES are about **file content** as much as commands, and only a hook sees arguments.
3. **Confirm guard** (`scripts/hermes/hooks/confirm_guard.py`):
   - A gated run returns `confirm_id`. It starts only when **your** latest message says `yes, run <id>`.
   - Why it exists: in the eval, the model confirmed its own run in 3 of 6 variants. A confirmation the model can give
     itself is not a confirmation.
   - The guard reads the session from Hermes's `state.db`, read-only, and blocks if it cannot read it.
   - Verified live: the model's self-confirmation was blocked 3 times and the user's confirmation passed.
- **One flow at a time:** the tool server queues `make` jobs, because the Docker flow container takes 2 CPUs and 8 GB.

## 7. Freeze: why "run it again" cannot damage the evidence

- All 25 designs were validated (`make test-full`: 130 PASS) and frozen on 2026-10-07.
  - `designs/FROZEN.json` holds the hashes of every input and output.
  - `make check-frozen` runs inside `make test`.
  - `scripts/flow/frozen.py` is the guard the hook uses.
- **Intuition:** the committed results are the evidence every doc quotes. An agent that "just tries a change" on a
  frozen design would make the evidence stale. So the agent can **look** at frozen designs, re-run flows only through
  what-if **copies**, and compare the copy with the frozen original.
- To change a frozen design on purpose: unfreeze it by hand, change it, re-run the flow, then `make freeze`. That is
  owner work, not agent work.

## 8. Mini RAG: answers you can check

- **What:** `rag_answer` / `rag_search` / `rag_index` over the repo's own text: NOTES.md, docs, skills, script headers
  and one fact chunk per `metrics.json`. Generated files, `build/`, `runs/` and the Hermes pages themselves (this one included) are
  left out: they quote the eval questions, so the eval would retrieve its own answers. Adding `docs/HERMES_DEMOS.md` without
  this exclusion dropped v1 recall@4 from 8/10 to 7/10.
- **How:** BM25 + local embeddings (`qwen3-embedding:0.6b`), fused by reciprocal rank. The index is cached in
  `build/agent/rag/` and keyed by chunk hash, so an edit re-embeds only that file.
- **Insight:** the answer is assembled **in code** from verbatim sentences with `file:line`. The model's job is only to
  pass them on. A 9B model paraphrasing tends to invent; quoting does not. When nothing matches, the tool returns
  `found: false` and the model should answer "I do not know".
- **Measured:** recall numbers are in `examples/hermes_rag/README.md` ("Hybrid mini RAG for Hermes Agent") and
  `results_summary.json`. That file now records a `corpus_fingerprint`, because the numbers drift as docs grow. The
  tests compare exact numbers only on the same corpus and otherwise check floors.
- If Ollama is down, the tools fall back to BM25 (`mode: "bm25-fallback"`).

## 9. Claude when needed, local otherwise

- **Default:** everything runs locally (Ollama on loopback, tool server on 127.0.0.1). No cloud provider is configured
  in the `chip` profile.
- **`ask_claude`** forwards a task to Claude Code (`claude_task` in the tool server). It goes through the same confirm
  gate and guard, and `claude_status` follows it.
- **When to use it:** you ask for Claude, or a local tool was tried and was inconclusive. It uses your Claude plan.
- **Insight:** treat the local model as the **front desk** and Claude as the **specialist**: cheap, private routing
  first, expensive reasoning only on referral.

## 10. Cron, sessions and run history

- **Cron:** nightly 02:30 `make test` and Sunday 03:00 `make test-full` (`scripts/hermes/cron_run.sh`, `--no-agent`,
  local delivery). Headless runs use `approvals.cron_mode: deny`, so cron can never start a physical flow.
  - Status: jobs created in isolated home. They fire only while a Hermes gateway (scheduler) runs, which is the
    owner's choice.
- **Sessions:** the Hermes.app sessions were cleared on 2026-10-07 after a backup to
  `~/.hermes/backups/sessions_before_clear_*.jsonl`.
  - To clear them again, quit Hermes.app first: the app holds `state.db`.
  - Then run `hermes sessions prune --older-than 0` or `hermes sessions delete <id>`.
- **Memory:** Hermes's built-in memory is global and small, so project facts and run history use the repo's
  `remember`/`recall` tools (`build/agent/memory/`, never committed). `session_search` finds past chats.

## 11. Demos

Eleven demos ("run demo 4" in the app). The skill is `.claude/skills/chip-demos/` and the page is
[docs/HERMES_DEMOS.md](docs/HERMES_DEMOS.md).

| # | Demo |
|---|---|
| 1 | Numbers |
| 2 | Why, from the notes |
| 3 | Logs |
| 4 | KLayout/Magic by text |
| 5 | Gated flow run |
| 6 | What-if on a copy |
| 7 | Experiments |
| 8 | Skills |
| 9 | Memory |
| 10 | Proof it is local |
| 11 | Claude steps in |

- Demos 1 to 3 are verified with transcripts. The rest are marked "not run" or "checked against the tool server".
- Known flake: `log_digest` sometimes produces an invalid tool call on the 9B model (demo 3).

## 12. Grafana: the evidence as dashboards

- **What:** a local Grafana (Homebrew, http://127.0.0.1:3000) with three dashboards: overview (all 25 designs), runs
  (jobs, what-ifs) and agent (eval scores, tool calls). They read a SQLite file that `make grafana-db` builds from the
  committed evidence. Guide: [docs/GRAFANA.md](docs/GRAFANA.md).
- **Hermes link:** `bash scripts/hermes_agent_setup.sh --grafana` adds `mcp-grafana` as a second MCP server. It is opt-in,
  so a Mac without Grafana gets no broken server.
- **Read-only, four times over:**
  - the server starts with `-disable-write`;
  - only four read tools are listed;
  - the hook blocks every other Grafana tool;
  - the token is a Viewer account.
  The admin password and the token live only in the macOS Keychain.
- **Limit:** `run_panel_query` does not support SQLite. Hermes therefore reads dashboard titles and the SQL behind each
  panel from Grafana, but takes the numbers from the chip tools. Enabling the generic `grafana_api_request` tool would close
  that gap; that is an owner decision and it is not enabled.
- **Status:** verified in an isolated home (`hermes mcp test grafana`, two live questions); pending owner apply.
- **Insight:** dashboards are for **you** (trends at a glance); the chip tools are for **the model** (exact numbers with a
  source file).

## 13. Other front ends (kept, not maintained)

- Open WebUI (`make hermes`, `make demo*`), the repo's own "Hermes Chip Agent.app" wrapper and the terminal loops still
  work as they did, but get no new work.
- Their known gaps are listed in [SPEC.md](SPEC.md#agent-front-ends-decision-2026-10-07).
- **Measured reason:** on the same cases, the Open WebUI path scored 60.7 % against 87.9 % for Hermes. Its ~18.5k-token
  tool prompt is cut to an 8k context, so the model never sees most tools.

## 14. What is not done, and what needs you

- **Owner actions:**
  - Optional: decide whether to enable Grafana's `grafana_api_request` (section 12).
  - Run `bash scripts/hermes_agent_setup.sh`, read the diff, then rerun with `--apply`.
  - Decide whether a Hermes gateway runs (needed for cron).
- **Limits:**
  - Demos 4 and 7 to 11 have not been run end to end.
  - The four demo models are untuned (for example, `mistral-nemo` runs with a 4k context).
  - Hermes.app desktop plugins (status-bar job chip, palette commands) are planned, not built.
- **Next (agreed):** a careful refactor and a full integration audit (a coverage test that every tool, skill and demo is
  reachable from Hermes.app).

## 15. Map of files

| Concern | Files |
|---|---|
| Setup | `scripts/hermes_agent_setup.sh`, `scripts/hermes/setup_profile.py` |
| Safety | `scripts/hermes/hooks/pre_tool_call.py`, `scripts/hermes/hooks/confirm_guard.py`; tests `tests/tools/test_hermes_hook.py`, `tests/tools/test_hermes_confirm_guard.py` |
| MCP | `tools/hermes_mcp_bridge.py`, `tools/hermes_tools.json` |
| Tool server | `examples/hermes_desktop/tool_server/` (`*_tools.py` are auto-mounted) |
| Context | `.hermes.md` (generated), `scripts/docs/make_hermes_context.py`, `CLAUDE.md` "For Hermes", `.hermes/environment.json` |
| Skills | `.claude/skills/*/SKILL.md` |
| Freeze | `designs/FROZEN.json`, `designs/FROZEN.md`, `scripts/flow/freeze.py`, `scripts/flow/frozen.py` |
| RAG | `examples/hermes_desktop/tool_server/rag_tools.py`, `examples/hermes_rag/` |
| Cron | `scripts/hermes/cron_run.sh` |
| Grafana | `scripts/grafana/`, `scripts/hermes/grafana_mcp.{sh,json}`, `examples/grafana/`, [docs/GRAFANA.md](docs/GRAFANA.md) |
| Eval | `examples/hermes_desktop/eval_tools/` (`cases.json`, `run_eval.py`, results summaries) |
| Docs | [docs/HERMES_AGENT_INTEGRATION.md](docs/HERMES_AGENT_INTEGRATION.md), [docs/HERMES_DEMOS.md](docs/HERMES_DEMOS.md), [SPEC.md](SPEC.md) |
