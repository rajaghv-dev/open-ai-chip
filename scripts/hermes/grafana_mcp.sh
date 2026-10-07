#!/bin/sh
# Hermes MCP server entry for the local Grafana (grafana/mcp-grafana, read-only subset).
# The Viewer service-account token is read from the macOS Keychain (service open-ai-chip-grafana-mcp) at start-up and
# handed to the child process through its environment only: it is never written to a file or a config.
# Tools: search, dashboard, datasource; all write tools off (--disable-write). Usage stats off.
# Limit: mcp-grafana's run_panel_query tool (so not enabled) does not support the SQLite datasource, so panel SQL can be read but not run
# through this server (see docs/GRAFANA.md); the generic grafana_api_request tool is deliberately NOT enabled.
# Docs: docs/GRAFANA.md. Setup of the token: scripts/grafana/setup_grafana.sh --apply
set -eu
TOKEN="$(security find-generic-password -s open-ai-chip-grafana-mcp -w 2>/dev/null || true)"
if [ -z "$TOKEN" ]; then
  echo "grafana_mcp: no token in the Keychain (service open-ai-chip-grafana-mcp); run scripts/grafana/setup_grafana.sh --apply" >&2
  exit 1
fi
BIN="$(command -v mcp-grafana 2>/dev/null || true)"
[ -n "$BIN" ] || BIN=/opt/homebrew/bin/mcp-grafana
[ -x "$BIN" ] || { echo "grafana_mcp: mcp-grafana not found (brew install mcp-grafana)" >&2; exit 1; }
GRAFANA_URL="${GRAFANA_URL:-http://127.0.0.1:3000}"
case "$GRAFANA_URL" in http://127.0.0.1:*|http://localhost:*) ;; *) echo "grafana_mcp: only a loopback GRAFANA_URL is allowed" >&2; exit 1;; esac
export GRAFANA_URL GRAFANA_SERVICE_ACCOUNT_TOKEN="$TOKEN" GRAFANA_USAGE_STATS=disabled
exec "$BIN" -t stdio -enabled-tools search,dashboard,datasource -disable-write -usage-stats disabled
