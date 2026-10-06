#!/usr/bin/env bash
# Docs: docs/RUN_ON_MAC.md, README.md
# doctor.sh -- check what the flow needs on this host; exit 1 if anything required is missing.
# Required: python3, iverilog/vvp, a reachable Docker daemon, the LibreLane image, the sky130A PDK at the pinned commit.
set -uo pipefail
cd "$(dirname "$0")/.."
. ./versions.lock
PDK_ROOT="${PDK_ROOT:-$HOME/.volare}"
FAIL=0
ok()  { printf '  PASS  %-14s %s\n' "$1" "$2"; }
bad() { printf '  FAIL  %-14s %s\n' "$1" "$2"; FAIL=1; }

for t in python3 iverilog vvp docker; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t" "$(command -v "$t")"; else bad "$t" "not on PATH"; fi
done
command -v iverilog >/dev/null 2>&1 && ok "iverilog ver" "$(iverilog -V 2>&1 | head -1)"
echo "  DOCKER_HOST=${DOCKER_HOST:-<default>}"
if docker info >/dev/null 2>&1; then
  ok docker-daemon "$(docker info --format '{{.ServerVersion}} {{.Architecture}} cpus={{.NCPU}} mem={{.MemTotal}}')"
  if docker image inspect "$LIBRELANE_IMAGE" >/dev/null 2>&1; then ok image "$LIBRELANE_IMAGE"
  else bad image "$LIBRELANE_IMAGE not pulled (docker pull $LIBRELANE_IMAGE)"; fi
else
  bad docker-daemon "not reachable; start Colima (colima start osl) or set DOCKER_HOST"
fi
if [ -d "$PDK_ROOT/sky130A/libs.ref/sky130_fd_sc_hd/verilog" ]; then
  cur="$(cd "$PDK_ROOT/sky130A" && pwd -P)"
  case "$cur" in *"$SKY130_PDK_COMMIT"*) ok sky130A "commit $SKY130_PDK_COMMIT" ;;
                 *) bad sky130A "$cur is not the pinned commit $SKY130_PDK_COMMIT" ;; esac
else
  bad sky130A "missing under PDK_ROOT=$PDK_ROOT (volare enable --pdk sky130 $SKY130_PDK_COMMIT)"
fi
free=$(df -Pk . | awk 'NR==2{print int($4/1048576)}')
[ "$free" -ge 10 ] && ok disk "${free} GiB free" || bad disk "only ${free} GiB free (need 10)"
[ $FAIL = 0 ] && echo "doctor: ALL CHECKS PASSED" || echo "doctor: FAILURES above"
exit $FAIL
