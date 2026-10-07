#!/usr/bin/env bash
# Set up the local Homebrew Grafana for open-ai-chip: config block, SQLite plugin, provisioning, dashboards, admin
# password and the read-only MCP token (both in the macOS Keychain, never printed).
#   scripts/grafana/setup_grafana.sh              dry run: print what would change (no writes)
#   scripts/grafana/setup_grafana.sh --apply      back up grafana.ini, apply, restart the brew service, verify
#   scripts/grafana/setup_grafana.sh --uninstall  remove the config block, provisioning files and plugin
#                                                 (service, Keychain entries and the Grafana package stay)
# Prerequisite: brew install grafana mcp-grafana. Docs: docs/GRAFANA.md.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PFX="$(brew --prefix)"
ETC="$PFX/etc/grafana"
INI="$ETC/grafana.ini"
PROV="$ETC/provisioning"
PLUGDIR="$PFX/var/lib/grafana/plugins"
PLUGIN="frser-sqlite-datasource"
DB="$ROOT/build/grafana/chip.db"
BEGIN="# BEGIN open-ai-chip (managed by scripts/grafana/setup_grafana.sh)"
END="# END open-ai-chip"
MODE="dry"
case "${1:-}" in --apply) MODE=apply;; --uninstall) MODE=uninstall;; "") ;; *) sed -n 2,9p "$0"; exit 2;; esac

block() {
cat <<EOB
$BEGIN
[paths]
provisioning = $PROV
[server]
http_addr = 127.0.0.1
http_port = 3000
[analytics]
reporting_enabled = false
check_for_updates = false
check_for_plugin_updates = false
feedback_links_enabled = false
[auth.anonymous]
enabled = false
[users]
allow_sign_up = false
allow_org_create = false
[security]
disable_gravatar = true
cookie_samesite = strict
$END
EOB
}
strip_block() { awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1} !skip{print} $0==e{skip=0}' "$INI"; }
render() { sed -e "s#__CHIP_DB_PATH__#$DB#" -e "s#__CHIP_DASH_DIR__#$ROOT/examples/grafana/dashboards#" "$1"; }
health() { curl -fsS --max-time 3 http://127.0.0.1:3000/api/health >/dev/null 2>&1; }

if [ "$MODE" = dry ]; then
  echo "DRY RUN (use --apply). Brew Grafana config dir: $ETC"
  echo "1. append to $INI (after a timestamped backup):"; block | sed 's/^/     /'
  echo "2. install provisioning (paths filled in):"
  echo "     $PROV/datasources/chip.yaml  (SQLite datasource -> $DB)"
  echo "     $PROV/dashboards/chip.yaml   (dashboards from $ROOT/examples/grafana/dashboards)"
  echo "3. grafana cli plugins install $PLUGIN  (into $PLUGDIR)"
  echo "4. python3 scripts/grafana/export_db.py -> $DB"
  echo "5. brew services restart grafana; wait for /api/health"
  echo "6. scripts/grafana/grafana_token.py init-admin  (random admin password -> Keychain open-ai-chip-grafana)"
  echo "7. scripts/grafana/grafana_token.py mcp-token   (Viewer service account token -> Keychain open-ai-chip-grafana-mcp)"
  exit 0
fi

if [ "$MODE" = uninstall ]; then
  [ -f "$INI" ] && strip_block > "$INI.new" && mv "$INI.new" "$INI"
  rm -f "$PROV/datasources/chip.yaml" "$PROV/dashboards/chip.yaml"
  rm -rf "$PLUGDIR/$PLUGIN"
  brew services restart grafana >/dev/null 2>&1 || true
  echo "removed the managed block, provisioning files and the $PLUGIN plugin. Kept: brew grafana, the service,"
  echo "Keychain entries (delete: security delete-generic-password -s open-ai-chip-grafana; same for -mcp)."
  exit 0
fi

[ -f "$INI" ] || { echo "no $INI: brew install grafana first" >&2; exit 1; }
cp -n "$INI" "$INI.bak-chip-$(date +%Y%m%d_%H%M%S)" || true
{ strip_block; block; } > "$INI.new" && mv "$INI.new" "$INI"
mkdir -p "$PROV/datasources" "$PROV/dashboards"
python3 "$ROOT/scripts/grafana/export_db.py"
render "$ROOT/examples/grafana/provisioning/datasources/chip.yaml" > "$PROV/datasources/chip.yaml"
render "$ROOT/examples/grafana/provisioning/dashboards/chip.yaml" > "$PROV/dashboards/chip.yaml"
if [ ! -d "$PLUGDIR/$PLUGIN" ]; then
  grafana cli --homepath "$PFX/opt/grafana/share/grafana" --pluginsDir "$PLUGDIR" plugins install "$PLUGIN"
fi
brew services restart grafana >/dev/null
for _ in $(seq 1 30); do health && break; sleep 2; done
health || { echo "Grafana did not become healthy; see $PFX/var/log/grafana/grafana.log" >&2; exit 1; }
python3 "$ROOT/scripts/grafana/grafana_token.py" init-admin
python3 "$ROOT/scripts/grafana/grafana_token.py" mcp-token
python3 "$ROOT/scripts/grafana/grafana_token.py" check
echo "Grafana: http://127.0.0.1:3000  (login admin, password in the Keychain: security find-generic-password -s open-ai-chip-grafana -w)"
