#!/usr/bin/env bash
# run_all_mac.sh -- kept so existing docs stay valid: the script is now OS-aware and lives in scripts/run_all.sh
# (macOS and Linux). Same options; see the header of run_all.sh.
# Docs: docs/RUN_ON_MAC.md
exec bash "$(dirname "$0")/run_all.sh" "$@"
