#!/usr/bin/env bash
# Hermes Agent setup for this repo: creates/updates the separate `chip` profile (local qwen3.5-64k:9b, read-only
# session, gated MCP tools, deny rules, hook, nightly cron). Dry run by default: prints a unified diff of every file
# it would create or change under the Hermes home plus the exact `hermes` commands. Nothing changes without --apply.
#   bash scripts/hermes_agent_setup.sh                    # review (diff only)
#   bash scripts/hermes_agent_setup.sh --apply            # back up to <home>/backups/open-ai-chip-<ts>/, then apply
#   bash scripts/hermes_agent_setup.sh --uninstall [--apply]   # plan / undo using the latest backup
#   options: --profile NAME (default chip), --hermes-home DIR (default $HERMES_HOME or ~/.hermes; use build/hermes_test_home to try)
# Never reads .env, auth.json, pairing/ or any secret. Docs: docs/HERMES_AGENT_INTEGRATION.md. Logic: scripts/hermes/setup_profile.py
set -euo pipefail
exec python3 "$(cd "$(dirname "$0")" && pwd)/hermes/setup_profile.py" "$@"
