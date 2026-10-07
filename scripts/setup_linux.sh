#!/usr/bin/env bash
# setup_linux.sh -- one-time machine setup for the whole open-ai-chip flow on Linux (Ubuntu 22.04/24.04, Debian 12+).
# Guide: docs/RUN_ON_LINUX.md. Idempotent: every step checks first and skips what is already there.
#
#   bash scripts/setup_linux.sh                 # everything below (same as --all)
#   bash scripts/setup_linux.sh --dry-run       # print every command, run none
#   bash scripts/setup_linux.sh --yes --no-gui  # non-interactive, no desktop tools
#
# Steps: 1 apt packages (git make python3 venv pip jq curl iverilog, RISC-V bare-metal gcc)  2 RISC-V name shims
#        3 Docker Engine + docker group  4 LibreLane image (versions.lock)  5 sky130A PDK at the pinned commit (ciel)
#        6 agent venv build/agent/venv   7 KLayout + X11 notes   8 Ollama + hermes3:8b   9 optional: Open WebUI, Caravel
# Note: hermes3:8b serves the older Open WebUI path (scripts/hermes.sh) and the live smoke tests. The maintained front end,
#   the Nous Hermes desktop app (scripts/hermes_start.sh: qwen3.5-64k:9b + qwen3-embedding:0.6b), is macOS-only here
#   (open -a, osascript, date -j, stat -f and Hermes.app).
# Flags: --all (default set)  --dry-run  --yes  --no-apt  --no-docker  --no-pdk  --no-ollama  --no-gui
#        --webui (Open WebUI + desktop deps; calls examples/hermes_desktop/setup_webui.sh if present)
#        --caravel (clone the Caravel sim sources, about 5 GB; otherwise the commands are only printed)
# Needs sudo (or root) for apt, Docker and the /usr/local/bin shims. Nothing here logs in, pushes or publishes anything.
# Other distributions: the package names are printed; install them with your package manager and rerun with --no-apt.
# Docs: docs/RUN_ON_LINUX.md
set -uo pipefail
cd "$(dirname "$0")/.."
REPO=$PWD
. ./versions.lock

DRY=0 YES=0 APT=1 DOCKER=1 PDK=1 OLLAMA=1 GUI=1 WEBUI=0 CARAVEL=0
for a in "$@"; do
  case "$a" in
    --all) ;; --dry-run) DRY=1 ;; --yes|-y) YES=1 ;; --no-apt) APT=0 ;; --no-docker) DOCKER=0 ;; --no-pdk) PDK=0 ;;
    --no-ollama) OLLAMA=0 ;; --no-gui) GUI=0 ;; --webui) WEBUI=1 ;; --caravel) CARAVEL=1 ;;
    -h|--help) sed -n 2,17p "$0"; exit 0 ;;
    *) echo "unknown option $a (see --help)"; exit 2 ;;
  esac
done

PDK_ROOT="${PDK_ROOT:-$HOME/.volare}"
MODEL="${HERMES_MODEL:-hermes3:8b}"
RESULTS=()   # "STATUS|step|note"
note()   { printf '  NOTE  %s\n' "$*"; }
step()   { printf '\n== %s\n' "$*"; }
rec()    { RESULTS+=("$1|$2|$3"); }
run()    { printf '  + %s\n' "$*"; [ "$DRY" = 1 ] && return 0; "$@"; }
# run a shell snippet (pipes, redirects); printed first
runsh()  { printf '  + %s\n' "$1"; [ "$DRY" = 1 ] && return 0; bash -c "$1"; }
have()   { command -v "$1" >/dev/null 2>&1; }

# ---- host detection
OS_ID=unknown OS_LIKE="" OS_VER=""
if [ -r /etc/os-release ]; then . /etc/os-release; OS_ID=${ID:-unknown}; OS_LIKE=${ID_LIKE:-}; OS_VER=${VERSION_ID:-}; fi
ARCH=$(uname -m)
IS_DEB=0; case " $OS_ID $OS_LIKE " in *" debian "*|*" ubuntu "*) IS_DEB=1 ;; esac
if [ "$(uname -s)" != Linux ]; then
  if [ "$DRY" = 1 ]; then note "dry-run on $(uname -s): showing what a Debian/Ubuntu Linux host would run"; IS_DEB=1; OS_ID=ubuntu
  else echo "setup_linux.sh is for Linux (this is $(uname -s)); on macOS see docs/RUN_ON_MAC.md"; exit 1; fi
fi
SUDO=""; if [ "$(id -u)" != 0 ]; then SUDO=sudo; fi
USER_NAME="${SUDO_USER:-${USER:-$(id -un)}}"
if [ -n "$SUDO" ] && [ "$DRY" = 0 ] && ! have sudo; then echo "need root or sudo for the system steps"; exit 1; fi
APT_Y=""; [ "$YES" = 1 ] && APT_Y="-y"
echo "open-ai-chip Linux setup: $OS_ID $OS_VER $ARCH, repo $REPO, PDK_ROOT=$PDK_ROOT$([ $DRY = 1 ] && echo ', DRY RUN')"

# ---- 1. apt packages
step "1. system packages"
PKGS=(git make python3 python3-venv python3-pip jq curl ca-certificates xz-utils zstd iverilog libdigest-sha-perl
      gcc-riscv64-unknown-elf binutils-riscv64-unknown-elf)
[ "$GUI" = 1 ] && PKGS+=(klayout x11-xserver-utils)
if [ "$WEBUI" = 1 ]; then
  PKGS+=(build-essential python3-dev pkg-config libgirepository1.0-dev libcairo2-dev python3-gi)
fi
if [ "$APT" = 0 ]; then
  note "--no-apt: install these yourself: ${PKGS[*]}"; rec SKIP "apt packages" "--no-apt"
elif [ "$IS_DEB" = 0 ]; then
  note "$OS_ID is not Debian/Ubuntu: apt is not used. Needed (Debian names): ${PKGS[*]}"
  note "Fedora: dnf install git make python3 jq curl iverilog gcc-riscv64-linux-gnu (needs a bare-metal rv32i gcc: build riscv-gnu-toolchain, or use xPack)."
  note "Arch: pacman -S git make python jq curl iverilog riscv64-elf-gcc (already named riscv64-elf-, no shim needed). Then rerun with --no-apt."
  rec SKIP "apt packages" "not Debian/Ubuntu"
else
  MISSING=()
  for p in "${PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
  if [ ${#MISSING[@]} -eq 0 ]; then echo "  all ${#PKGS[@]} packages already installed"; rec OK "apt packages" "already installed"
  else
    run $SUDO apt-get update
    if run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install $APT_Y --no-install-recommends "${MISSING[@]}"; then rec OK "apt packages" "${MISSING[*]}"
    else rec FAIL "apt packages" "apt-get install failed"; fi
  fi
fi

# ---- 2. RISC-V name shims: the firmware Makefiles use CROSS ?= riscv64-elf- (Homebrew); Debian names it riscv64-unknown-elf-
step "2. RISC-V toolchain name (firmware/Makefile CROSS=riscv64-elf-, Debian ships riscv64-unknown-elf-)"
if have riscv64-elf-gcc; then echo "  riscv64-elf-gcc already on PATH: $(command -v riscv64-elf-gcc)"; rec OK "riscv shims" "native riscv64-elf-gcc"
elif have riscv64-unknown-elf-gcc || [ "$DRY" = 1 ]; then
  SHIMDIR=/usr/local/bin; SUDO_LN=""
  if [ ! -w "$SHIMDIR" ]; then if [ -n "$SUDO" ]; then SUDO_LN=$SUDO; else SHIMDIR="$HOME/.local/bin"; fi; fi
  [ "$SHIMDIR" = "$HOME/.local/bin" ] && run mkdir -p "$SHIMDIR"
  for t in gcc g++ as ld ar nm ranlib objcopy objdump readelf size strip; do
    run $SUDO_LN ln -sf "$(command -v riscv64-unknown-elf-$t 2>/dev/null || echo /usr/bin/riscv64-unknown-elf-$t)" "$SHIMDIR/riscv64-elf-$t"
  done
  case ":$PATH:" in *":$SHIMDIR:"*) ;; *) export PATH="$SHIMDIR:$PATH"; note "add to your shell profile: export PATH=\"$SHIMDIR:\$PATH\"" ;; esac
  rec OK "riscv shims" "riscv64-elf-* -> riscv64-unknown-elf-* in $SHIMDIR (alternative without shims: make soc-sim CROSS=riscv64-unknown-elf-)"
else
  note "no RISC-V bare-metal gcc: make test SKIPs the firmware SoC sims (apt install gcc-riscv64-unknown-elf)"; rec WARN "riscv shims" "no toolchain"
fi
if have riscv64-elf-gcc && [ "$DRY" = 0 ]; then
  if riscv64-elf-gcc -print-multi-lib 2>/dev/null | grep -q '^rv32i/ilp32;'; then echo "  multilib rv32i/ilp32 present: $(riscv64-elf-gcc --version | head -1)"
  else note "this gcc has no rv32i/ilp32 multilib; firmware builds will fail (use the xPack riscv-none-elf-gcc or build riscv-gnu-toolchain)"; rec WARN "riscv multilib" "rv32i/ilp32 missing"; fi
fi

# ---- 3. Docker Engine
step "3. Docker Engine"
if [ "$DOCKER" = 0 ]; then rec SKIP "docker" "--no-docker"; echo "  skipped (--no-docker): no physical flow, precheck or GUI windows without it"
else
  if have docker; then echo "  docker present: $(docker --version 2>&1)"
  elif [ "$IS_DEB" = 1 ] && [ "$APT" = 1 ]; then
    run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install $APT_Y --no-install-recommends docker.io
    note "docker.io is the distribution's Engine; Docker's own docker-ce repository (docs.docker.com/engine/install) works as well"
  else note "install Docker Engine for $OS_ID (docs.docker.com/engine/install)"; fi
  if [ "$DRY" = 1 ] || have docker; then
    [ -d /run/systemd/system ] && run $SUDO systemctl enable --now docker
    if ! id -nG "$USER_NAME" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
      run $SUDO usermod -aG docker "$USER_NAME"
      note "you were added to the docker group: log out and in again (or run 'newgrp docker') before using docker without sudo"
    fi
    rec OK "docker" "engine + group"
  else rec FAIL "docker" "not installed"; fi
fi

# ---- 4. LibreLane image
step "4. LibreLane image $LIBRELANE_IMAGE"
if [ "$DOCKER" = 0 ]; then rec SKIP "librelane image" "--no-docker"
else
  D=docker; if [ "$DRY" = 0 ] && ! docker info >/dev/null 2>&1; then
    if [ -n "$SUDO" ] && $SUDO docker info >/dev/null 2>&1; then D="$SUDO docker"; note "docker needs sudo until the docker group is active"
    else note "Docker daemon not reachable (not started, or group not active yet): pull later with 'docker pull $LIBRELANE_IMAGE'"; D=""; fi
  fi
  if [ -z "$D" ]; then rec WARN "librelane image" "daemon not reachable; pull later"
  elif [ "$DRY" = 0 ] && $D image inspect "$LIBRELANE_IMAGE" >/dev/null 2>&1; then echo "  image already present"; rec OK "librelane image" "present"
  else run $D pull "$LIBRELANE_IMAGE" && rec OK "librelane image" "pulled" || rec FAIL "librelane image" "pull failed"; fi
fi

# ---- 5. sky130A PDK at the pinned commit, with the ciel tool (what LibreLane 3.0.2 uses; make doctor checks this layout)
step "5. sky130A PDK $SKY130_PDK_COMMIT in $PDK_ROOT"
if [ "$PDK" = 0 ]; then rec SKIP "sky130A PDK" "--no-pdk"
elif [ -d "$PDK_ROOT/sky130A" ] && case "$(cd "$PDK_ROOT/sky130A" && pwd -P)" in *"$SKY130_PDK_COMMIT"*) true ;; *) false ;; esac; then
  echo "  already at the pinned commit"; rec OK "sky130A PDK" "present"
else
  CV=build/setup/ciel-venv
  [ -x "$CV/bin/ciel" ] || { run mkdir -p build/setup; run python3 -m venv "$CV"; run "$CV/bin/pip" install -q ciel; }
  if run "$CV/bin/ciel" enable --pdk-family sky130 --pdk-root "$PDK_ROOT" "$SKY130_PDK_COMMIT"; then rec OK "sky130A PDK" "ciel enable (about 1-2 GB download)"
  else rec FAIL "sky130A PDK" "ciel enable failed (network?)"; fi
fi

# ---- 6. agent venv
step "6. agent venv build/agent/venv (tools/requirements.txt: klayout mcp pytest fastapi uvicorn httpx)"
PYMAJ=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 0.0)
case "$PYMAJ" in 3.9|3.8|3.7|2.*|0.0) [ "$DRY" = 1 ] || note "python3 is $PYMAJ; the mcp package needs 3.10+ (Ubuntu 22.04+ and Debian 12 have it)";; esac
AP=build/agent/venv/bin/python
if [ "$DRY" = 0 ] && [ -x "$AP" ] && "$AP" -c 'import klayout.lay, mcp, fastapi, uvicorn, httpx, pytest' >/dev/null 2>&1; then
  echo "  venv already complete"; rec OK "agent venv" "present"
else
  [ -x "$AP" ] || { run mkdir -p build/agent; run python3 -m venv build/agent/venv; }
  if run build/agent/venv/bin/pip install -q -r tools/requirements.txt; then rec OK "agent venv" "installed"
  else rec FAIL "agent venv" "pip install failed"; fi
fi

# ---- 7. KLayout desktop and X11
step "7. KLayout desktop and X11 for the OpenROAD GUI / Magic windows"
if [ "$GUI" = 0 ]; then rec SKIP "gui" "--no-gui"
else
  if have klayout; then echo "  $(klayout -v 2>&1 | head -1)  ($(command -v klayout))"; else [ "$DRY" = 1 ] || note "klayout not installed (apt install klayout); the agent venv still has the klayout Python module"; fi
  note "OpenROAD GUI and Magic run in the LibreLane container and draw on your X server: DISPLAY and /tmp/.X11-unix are passed in"
  if [ -n "${DISPLAY:-}" ] && have xhost; then
    if [ "$DRY" = 1 ]; then echo "  + xhost +SI:localuser:root     # lets the container (root) draw on this X server; undo: xhost -SI:localuser:root"
    elif xhost +SI:localuser:root >/dev/null 2>&1; then echo "  X access for the container granted (xhost +SI:localuser:root; undo: xhost -SI:localuser:root)"
    else note "xhost could not reach DISPLAY=$DISPLAY"; fi
  else
    note "no DISPLAY here (ssh or headless): run 'xhost +SI:localuser:root' in a desktop terminal before the GUI windows; headless works for everything else"
  fi
  note "Wayland: GNOME/KDE start XWayland, so DISPLAY is set and the same commands work"
  rec OK "gui" "klayout $(have klayout && echo present || echo missing), X notes"
fi

# ---- 8. Ollama and the Hermes model
step "8. Ollama and $MODEL"
if [ "$OLLAMA" = 0 ]; then rec SKIP "ollama" "--no-ollama"
else
  if ! have ollama; then
    CMD='curl -fsSL https://ollama.com/install.sh | sh'
    echo "  the official installer is a remote script piped to sh: $CMD"
    go=0
    if [ "$DRY" = 1 ]; then echo "  + $CMD    (needs --yes or an interactive 'y')"
    elif [ "$YES" = 1 ]; then go=1
    elif [ -t 0 ]; then read -r -p "  run it now? [y/N] " ans; case "$ans" in y|Y|yes) go=1 ;; esac
    else note "not a terminal and no --yes: skipped; run the command above yourself"; fi
    [ "$go" = 1 ] && runsh "$CMD"
  else echo "  ollama present: $(ollama --version 2>&1 | tail -1)"; fi
  if have ollama || [ "$DRY" = 1 ]; then
    if [ "$DRY" = 0 ] && ! curl -fsS -m 3 localhost:11434/api/tags >/dev/null 2>&1; then
      mkdir -p build/setup; echo "  starting ollama serve (log build/setup/ollama.log)"
      nohup ollama serve >build/setup/ollama.log 2>&1 & for _ in $(seq 1 30); do curl -fsS -m 2 localhost:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
    fi
    if [ "$DRY" = 0 ] && ollama list 2>/dev/null | grep -q "^$MODEL"; then echo "  $MODEL already pulled"; rec OK "ollama" "$MODEL present"
    elif run ollama pull "$MODEL"; then rec OK "ollama" "$MODEL pulled (4.7 GB)"; else rec WARN "ollama" "pull failed"; fi
  else rec WARN "ollama" "not installed"; fi
fi

# ---- 9. optional: Open WebUI desktop, Caravel sources
step "9. optional parts"
if [ "$WEBUI" = 1 ]; then
  if [ -f examples/hermes_desktop/setup_webui.sh ]; then
    PY312=$(command -v python3.12 || command -v python3.11 || command -v python3 || echo python3)
    echo "  Open WebUI (large, several GB) with $PY312"
    run env PY312="$PY312" bash examples/hermes_desktop/setup_webui.sh && rec OK "open webui" "installed" || rec FAIL "open webui" "setup_webui.sh failed"
    run build/agent/venv/bin/pip install -q 'pywebview[gtk]' || note "pywebview GTK backend not installed: the desktop window is optional, use http://127.0.0.1:8080 in a browser"
  else note "examples/hermes_desktop/setup_webui.sh not found: nothing to do for --webui"; rec SKIP "open webui" "no examples/hermes_desktop"; fi
else rec SKIP "open webui" "add --webui"; fi
C1="git clone -b CC2509 --depth=1 https://github.com/chipfoundry/caravel-lite build/caravel/caravel"
C2="git clone -b CC2509 --depth=1 https://github.com/chipfoundry/caravel_mgmt_soc_litex build/caravel/mgmt_core_wrapper"
if [ "$CARAVEL" = 1 ]; then
  run mkdir -p build/caravel
  [ -d build/caravel/caravel ] || runsh "$C1"; [ -d build/caravel/mgmt_core_wrapper ] || runsh "$C2"; rec OK "caravel sources" "cloned"
else
  echo "  Caravel simulations (make caravel-rtl / caravel-gl / caravel-fullgl) need about 5 GB of sources; not downloaded. Commands:"
  echo "    $C1"; echo "    $C2"; echo "  or rerun this script with --caravel"; rec SKIP "caravel sources" "add --caravel"
fi

# ---- end: doctor and summary
step "make doctor"
if [ "$DRY" = 1 ]; then echo "  + make doctor"; else make doctor || note "doctor reported failures: see above (Docker group re-login, PDK, image)"; fi
echo; echo "== summary"
NF=0; for r in "${RESULTS[@]}"; do IFS='|' read -r s n t <<<"$r"; printf '  %-5s %-18s %s\n' "$s" "$n" "$t"; [ "$s" = FAIL ] && NF=$((NF+1)); done
echo
echo "next: log out and in if you were added to the docker group, then:  make doctor && bash scripts/run_all.sh"
echo "env for physical flows on Linux: DOCKER_HOST defaults to unix:///var/run/docker.sock (scripts/run_all.sh exports it)"
[ "$NF" = 0 ]
