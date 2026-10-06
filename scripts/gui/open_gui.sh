#!/usr/bin/env bash
# open_gui.sh -- open a finished design in the OpenROAD GUI or in Magic, running inside the LibreLane container and
# drawing on the Mac's XQuartz (or the Linux X server). Read-only: nothing is written back to the run.
#   bash scripts/gui/open_gui.sh openroad <design>     # OpenROAD GUI on the final ODB of the current run
#   bash scripts/gui/open_gui.sh magic    <design>     # Magic on the final GDS with the sky130A tech
# One-time macOS setup (verified 2026-10-06, XQuartz 2.8.6, Colima profile osl):
#   defaults write org.xquartz.X11 nolisten_tcp -bool false     # let the Colima VM connect over TCP; restart XQuartz
#   open -a XQuartz && DISPLAY=:0 /opt/X11/bin/xhost +localhost  # Colima's networking delivers the VM as localhost
# Env: GUI_DISPLAY (default 192.168.5.2:0 on macOS/Colima = host.lima.internal; on Linux $DISPLAY with the X socket),
#      DOCKER_HOST (default the osl Colima socket), GUI_SECONDS (only when stdin is not a terminal: keep Magic open N s).
# Close the window to end. Afterwards you may remove the access again: DISPLAY=:0 /opt/X11/bin/xhost -localhost
set -euo pipefail
tool=${1:?usage: open_gui.sh openroad|magic <design>}; design=${2:?usage: open_gui.sh openroad|magic <design>}
cd "$(dirname "$0")/../.."
REPO=$PWD
IMAGE=$(sed -n 's/^LIBRELANE_IMAGE=//p' versions.lock)
PDK_ROOT=${PDK_ROOT:-$HOME/.volare}
run=$(python3 scripts/flow/find_reusable_run.py "$design") || { echo "no current run for $design"; exit 1; }
mkdir -p build/gui
DOCKER_ARGS=(--rm -v "$HOME:$HOME" -v "$REPO:$REPO" -w "$REPO" -e PDK_ROOT="$PDK_ROOT" -e PDK=sky130A)
if [ "$(uname)" = Darwin ]; then
  export DOCKER_HOST=${DOCKER_HOST:-unix://$HOME/.colima/osl/docker.sock}
  DOCKER_ARGS+=(-e DISPLAY="${GUI_DISPLAY:-192.168.5.2:0}")
  if ! lsof -nP -iTCP:6000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "XQuartz is not listening on TCP 6000: run the one-time setup in the header of $0"; exit 1
  fi
else
  DOCKER_ARGS+=(-e DISPLAY="${GUI_DISPLAY:-$DISPLAY}" -v /tmp/.X11-unix:/tmp/.X11-unix)   # Linux: not tested here
fi
if [ -t 0 ]; then DOCKER_ARGS+=(-it); else DOCKER_ARGS+=(-i); fi

case "$tool" in
  openroad)
    odb=$(ls "$run"/final/odb/*.odb | head -1)
    printf 'read_db %s\nputs "OPENROAD_GUI_LOADED [[ord::get_db_block] getName]"\n' "$odb" > build/gui/openroad_open.tcl
    echo "OpenROAD GUI: $odb"
    docker run "${DOCKER_ARGS[@]}" "$IMAGE" openroad -no_splash -gui "$REPO/build/gui/openroad_open.tcl"
    ;;
  magic)
    gds=$(ls "$run"/final/gds/*.gds | head -1)
    top=$(basename "$gds" .gds)
    T="$PDK_ROOT/sky130A/libs.tech/magic"
    # the PDK's magicrc names the tech file by its build path (/root/.ciel/...), so pass -T explicitly
    printf 'gds read %s\nload %s\nselect top cell\nexpand\nview\nputs "MAGIC_GUI_LOADED [cellname list self]"\n' "$gds" "$top" > build/gui/magic_open.tcl
    echo "Magic: $gds (top $top)"
    if [ -t 0 ]; then
      # interactive: Magic's console is this terminal; source the opener, then type Magic commands (quit to end)
      docker run "${DOCKER_ARGS[@]}" "$IMAGE" sh -c "printf 'source $REPO/build/gui/magic_open.tcl\n' > /tmp/rc.tcl; magic -d XR -T $T/sky130A.tech -rcfile $T/sky130A.magicrc /tmp/rc.tcl"
    else
      # no terminal: Magic reads commands from stdin and quits at EOF, so keep stdin open for GUI_SECONDS
      (cat build/gui/magic_open.tcl; sleep "${GUI_SECONDS:-120}"; echo "quit -noprompt") | \
        docker run "${DOCKER_ARGS[@]}" "$IMAGE" magic -d XR -noconsole -T "$T/sky130A.tech" -rcfile "$T/sky130A.magicrc"
    fi
    ;;
  *) echo "unknown tool $tool (openroad|magic)"; exit 1 ;;
esac
