# Local Grafana for open-ai-chip

A local Grafana shows the repo's committed evidence (designs, runs, what-ifs, agent eval scores, tool-call log) as three
dashboards, and the Hermes `chip` profile can read them through Grafana's MCP server. Everything is on this Mac, bound to
127.0.0.1, offline. Nothing here is published and nothing is committed except scripts, dashboards and docs.

## What is installed

| Item | Version | Where | Source |
|---|---|---|---|
| Grafana OSS | 13.2.3 | Homebrew formula `grafana`, service `brew services start grafana`, URL http://127.0.0.1:3000 | `brew info grafana` |
| SQLite datasource plugin `frser-sqlite-datasource` | 4.0.6 | `$(brew --prefix)/var/lib/grafana/plugins/` | `grafana cli plugins install` |
| Grafana MCP server `mcp-grafana` | 2.0.1 | Homebrew formula `mcp-grafana` (bottle, sha256 verified by Homebrew) | `brew info mcp-grafana` |
| Config | managed block `# BEGIN open-ai-chip` appended to `$(brew --prefix)/etc/grafana/grafana.ini` (backup `grafana.ini.bak-chip-<time>` beside it) | | `scripts/grafana/setup_grafana.sh` |
| Provisioning | `$(brew --prefix)/etc/grafana/provisioning/{datasources,dashboards}/chip.yaml` (paths filled in at install) | | `examples/grafana/provisioning/` |

Config in the managed block: `http_addr = 127.0.0.1`, `http_port = 3000`, analytics reporting, update checks and plugin
update checks off, anonymous access off, sign-up and org creation off, Gravatar off.

## Install, apply, undo

```
brew install grafana mcp-grafana
scripts/grafana/setup_grafana.sh              # dry run: prints every change
scripts/grafana/setup_grafana.sh --apply      # backup, config, plugin, db, provisioning, restart, password, token
scripts/grafana/setup_grafana.sh --uninstall  # removes block, provisioning files, plugin; keeps service and Keychain
```

`--apply` is idempotent. It also builds the database, replaces the default `admin/admin` with a random password and creates
the MCP token (below). Uninstall leaves Grafana itself (`brew uninstall grafana` removes it) and the Keychain entries
(`security delete-generic-password -s open-ai-chip-grafana`, same for `-mcp`).

## Secrets

- Admin password: random, set through the API on the first `--apply`, stored in the macOS Keychain
  (service `open-ai-chip-grafana`, account `admin`). Read it with `security find-generic-password -s open-ai-chip-grafana -w`
  (this prints it: do it only to log in). Scripts never print it.
- MCP token: Viewer service account `hermes-chip-mcp`, token in the Keychain (service `open-ai-chip-grafana-mcp`).
  `scripts/grafana/grafana_token.py check` reports whether both authenticate (ok or FAIL only).
- Nothing secret is in the repo, in `grafana.ini`, or in a Hermes config. `tests/tools/test_grafana_export.py` scans the new
  files for the Keychain values and for `glsa` token prefixes.

## Where the data comes from

`make grafana-db` (= `python3 scripts/grafana/export_db.py`) rebuilds `build/grafana/chip.db` (SQLite, git-ignored) from files
that already exist; nothing is measured here. Re-run it whenever evidence changes; Grafana reads the file on every query, so no
restart is needed.

| Table | Built from |
|---|---|
| `designs` (25 rows: family, cells, ff, die, util, worst setup and hold slack with corner, DRC/LVS/XOR/antenna, slew count, power, flow seconds, peak GB, frozen, signoff) | `designs/*/output/metrics.json`, `resources.json`, `designs/FROZEN.json` |
| `runs` (job id, time, design, command, PASS/FAIL, seconds, log) | `build/agent/jobs/*.log` (exit line), `build/agent/memory/runs.md` (rows without a job log) |
| `whatifs` (changes, slack, cells, deltas vs the committed baseline) | `build/whatif/*/whatif_meta.json`, `runs/*/final/metrics.json`, `whatif_resources.json` |
| `eval_scores` (backend, model, date, group, accuracy) | `examples/hermes_desktop/eval_tools/results_summary_*.json` |
| `proof_calls` (time, tool, status, ms) | `build/agent/proof/calls.jsonl` |

Slack is the worst value over the nine timing corners. The signoff column is PASS when DRC, LVS, XOR and antenna counts are
zero and setup and hold slack are not negative (slew and cap counts are informational, as in `scripts/flow/check_signoff.py`).
The numbers are the repo's own; read them in the dashboards or `sqlite3 build/grafana/chip.db`.

Refresh on a schedule (documented only, not installed): a Hermes cron job in the `chip` profile or a launchd agent that runs
`make -C <repo> grafana-db`. A refresh is a pure read of repo files plus one file replace (`chip.db.tmp` then rename).

## Dashboards

Defined in `scripts/grafana/gen_dashboards.py`, written to `examples/grafana/dashboards/*.json` (do not hand-edit; the test
fails if they differ from the generator). Folder `open-ai-chip`; datasource uid `chipdb`.

| Dashboard (uid) | Panels |
|---|---|
| open-ai-chip overview (`chip-overview`) | stats (designs, signoff PASS, frozen, total cells, worst setup and hold), table of all designs, bars for cells, flip-flops, die area, setup slack, hold slack, flow time, status by family |
| Runs and jobs (`chip-runs`) | stats, recent jobs table, pass/fail per hour, longest jobs, what-ifs vs baseline (table and slack delta bars), jobs per design |
| Agent (`chip-agent`) | stats from the proof log, eval overall table, eval accuracy by group (direct vs hermes backend), tool calls by tool and per hour |

Check without a browser: every panel query runs through `POST /api/ds/query` and `tests/tools/test_grafana_export.py` runs each
panel's SQL on the exported db.

## Hermes connection (Grafana MCP, read only)

`bash scripts/hermes_agent_setup.sh --grafana` (dry run; then add `--apply`) adds the `mcp_servers.grafana` entry from
`scripts/hermes/grafana_mcp.json` to the `chip` profile, with `__REPO__` filled in. Without `--grafana` the profile has no Grafana server.
It starts `scripts/hermes/grafana_mcp.sh`, which reads the Viewer token from the Keychain,
puts it in the child's environment only, requires a loopback `GRAFANA_URL`, and runs
`mcp-grafana -t stdio -enabled-tools search,dashboard,datasource -disable-write -usage-stats disabled`. Hermes exposes four tools
through `tools.include`: `search_dashboards`, `get_dashboard_summary`, `get_dashboard_panel_queries`, `list_datasources`.

Approvals and hooks: `approvals.mode: manual` stays. The `pre_tool_call` hook allows exactly the four read tools
(`mcp__grafana__search_dashboards` and so on) and blocks every other Grafana tool (`update_dashboard`, `grafana_api_request`,
`query_sql`, ...); tests in `tests/tools/test_hermes_hook.py`. Defence in depth: the server does not register write tools,
Hermes does not list them, the hook blocks them, and the Viewer token makes Grafana itself refuse writes.

Known limit: `mcp-grafana`'s `run_panel_query` does not support the SQLite datasource ("not supported by run_panel_query"), and
the only other route to panel results, `grafana_api_request`, is a generic request tool that is deliberately not enabled
(owner decision). So through Grafana the agent finds dashboards, reads panel titles and the SQL behind them; for the numbers it
uses the chip tools (`compare_designs`, `read_metrics`), which read the same `metrics.json` files.

Isolated test home (never touches `~/.hermes`):

```
python3 scripts/grafana/make_test_home.py          # writes build/hermes_grafana_home/config.yaml
HERMES_HOME=build/hermes_grafana_home hermes mcp test grafana
HERMES_HOME=build/hermes_grafana_home hermes -z "List the Grafana dashboards"
HERMES_HOME=build/hermes_grafana_home hermes -z "Which design has the worst setup slack according to the Grafana dashboard?"
```

Result of the last run (local `qwen3.5-64k:9b`, Hermes v0.21.5): `mcp test` connected and listed the Grafana tools; "List the Grafana
dashboards" called `search_dashboards` once and returned the three dashboards with uid and description; the slack question
called `search_dashboards`, `get_dashboard_panel_queries` and `chip__compare_designs`, and answered prec_bf16 (0.0443 ns) then prec_fp16
(0.1106 ns), which equal the two lowest `setup_ws` rows of the `designs` table (`sqlite3 build/grafana/chip.db "select name,
setup_ws from designs order by setup_ws limit 2"`). The small model is not deterministic: one earlier attempt ended with
"Model generated invalid tool call", another tried `run_panel_query` repeatedly before answering from the chip tools.

## Security summary

Loopback only (Grafana `127.0.0.1:3000`; `mcp-grafana` runs over stdio, no listening port); no anonymous access, no sign-up,
no telemetry or update checks; admin password and token only in the Keychain; Viewer token for the agent; no write tools
registered; the repo never holds a secret or an absolute home path. The database contains only data already in the repo.
