#!/usr/bin/env bash
# Stops only what start.sh started (pid files in build/webui/pids).
# Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
P="$REPO/build/webui/pids"
for n in webui tool_server ollama; do
  f="$P/$n.pid"
  [ -f "$f" ] || continue
  pid=$(cat "$f")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null; for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    echo "stopped $n ($pid)"
  fi
  rm -f "$f"
done
echo "stop done (anything not started by start.sh was left running)"
