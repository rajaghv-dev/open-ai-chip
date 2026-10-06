#!/usr/bin/env bash
# Fetch the sky130A PDK build that ChipFoundry's caravel_user_project template pins for its precheck
# (OPEN_PDKS_COMMIT in build/template/Makefile) from ChipFoundry's PUBLIC static ciel mirror. No login, no account.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMMIT="${OPEN_PDKS_COMMIT:-3e0e31dcce8519a7dbb82590346db16d91b7244f}"
PY="${PYTHON311:-$HOME/.local/bin/python3.11}"
[ -x "$ROOT/build/precheck/venv/bin/ciel" ] || { "$PY" -m venv "$ROOT/build/precheck/venv"; "$ROOT/build/precheck/venv/bin/pip" install -q ciel; }
export CIEL_DATA_SOURCE=static-web:https://chipfoundry.github.io/ciel-releases
"$ROOT/build/precheck/venv/bin/ciel" enable --pdk-family sky130 --pdk-root "$ROOT/build/precheck/pdk_cf" "$COMMIT"
