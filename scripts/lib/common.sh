#!/usr/bin/env bash
# Docs: docs/CODE_MAP.md, CLAUDE.md
# common.sh -- shared shell preamble of the repository scripts (sourced, never run). Python twin: scripts/lib/repo.py.
#   . "<repo>/scripts/lib/common.sh"
#   OAC_ROOT              repository root (from this file's location)
#   OAC_LIBRELANE_IMAGE   LIBRELANE_IMAGE of versions.lock (the single pin of the LibreLane image)
#   OAC_PDK_ROOT          ${PDK_ROOT:-$HOME/.volare}
#   oac_docker_host       export DOCKER_HOST when it is unset or empty: the Colima VM "osl" socket if it exists
#                         (the Makefile rule), else on Linux /var/run/docker.sock if it exists, else Docker's default
#                         (nothing exported). A DOCKER_HOST set by the caller is always kept.
OAC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OAC_LIBRELANE_IMAGE="$(sed -n 's/^LIBRELANE_IMAGE=\([^ #]*\).*/\1/p' "$OAC_ROOT/versions.lock")"
OAC_PDK_ROOT="${PDK_ROOT:-$HOME/.volare}"

oac_docker_host() {
  if [ -n "${DOCKER_HOST:-}" ]; then export DOCKER_HOST; return 0; fi
  if [ -e "$HOME/.colima/osl/docker.sock" ]; then
    export DOCKER_HOST="unix://$HOME/.colima/osl/docker.sock"
  elif [ "$(uname)" != Darwin ] && [ -e /var/run/docker.sock ]; then
    export DOCKER_HOST="unix:///var/run/docker.sock"
  fi
  return 0
}
