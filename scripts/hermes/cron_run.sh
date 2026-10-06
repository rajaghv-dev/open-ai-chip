#!/usr/bin/env bash
# Docs: docs/HERMES_AGENT_INTEGRATION.md
# cron_run.sh <test|test-full>: the job body of the Hermes cron entries created by scripts/hermes_agent_setup.sh.
# Runs `make test` (nightly) or `make test-full` (weekly) from the repo, prints a short verdict on stdout (Hermes
# delivers it locally), keeps the full log in build/agent/cron/<kind>_<ts>.log and appends one line to the repo run
# history (build/agent/memory/runs.md, via skills_memory_tools.record_run). No LLM, no network, no messaging.
# Env: CRON_TIMEOUT seconds (default 900 for test, 7200 for test-full). Exit code = the make exit code.
set -u
KIND="${1:-test}"
case "$KIND" in test|test-full) ;; *) echo "usage: $0 test|test-full" >&2; exit 64 ;; esac
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
[ -S "$HOME/.colima/osl/docker.sock" ] && export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.colima/osl/docker.sock}"
if [ "$KIND" = test ]; then CAP="${CRON_TIMEOUT:-900}"; else CAP="${CRON_TIMEOUT:-7200}"; fi
TS="$(date +%Y%m%d_%H%M%S)"
mkdir -p build/agent/cron
LOG="build/agent/cron/${KIND}_${TS}.log"
T0=$(date +%s)
perl -e 'alarm shift; exec @ARGV' "$CAP" make "$KIND" >"$LOG" 2>&1
RC=$?
SECS=$(( $(date +%s) - T0 ))
if [ $RC -eq 0 ]; then RES=PASS; else RES=FAIL; fi
[ $RC -eq 142 ] && RES="FAIL(timeout ${CAP}s)"
PASSN=$(grep -c '^PASS\|^  PASS\|PASS:' "$LOG" 2>/dev/null || true)
FAILN=$(grep -c '^FAIL\|^  FAIL\|FAIL:' "$LOG" 2>/dev/null || true)
PY="$REPO/build/agent/venv/bin/python"; [ -x "$PY" ] || PY=python3
CHIP_CRON_KIND="$KIND" CHIP_CRON_RES="$RES" CHIP_CRON_NUM="rc=$RC secs=$SECS pass_lines=$PASSN fail_lines=$FAILN" CHIP_CRON_LOG="$LOG" \
  "$PY" - <<'PYEOF' >/dev/null 2>&1 || true
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "examples", "hermes_desktop", "tool_server"))
import skills_memory_tools as m
m.record_run(None, "make " + os.environ["CHIP_CRON_KIND"] + " (hermes cron)", os.environ["CHIP_CRON_RES"],
             os.environ["CHIP_CRON_NUM"], os.environ["CHIP_CRON_LOG"])
PYEOF
echo "open-ai-chip make $KIND: $RES (rc=$RC, ${SECS}s, $(date '+%Y-%m-%d %H:%M'))"
echo "log: $LOG"
[ $RC -ne 0 ] && { echo "last lines:"; tail -n 8 "$LOG"; }
exit $RC
