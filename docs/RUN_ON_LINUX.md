# Run everything from a Linux terminal

Step by step on Ubuntu 22.04 / 24.04 or Debian 12 (apt): prepare the machine once with one script, then run every part
of this repository (tests, flows, sims, Caravel, precheck, Hermes agents) and open every GUI (KLayout, OpenROAD heat
maps, Magic). The macOS guide is [RUN_ON_MAC.md](RUN_ON_MAC.md); the commands are the same, only the setup and a few
host details differ (section 9). Other distributions: section 2.

What was verified, and what was not, is in section 10. Read it before trusting this page for a physical flow.

## 1. Prerequisites

| Need | Why |
|---|---|
| Ubuntu 22.04 / 24.04 or Debian 12, x86_64 or aarch64, `sudo` | apt packages, Docker, `/usr/local/bin` links |
| 2 CPUs and 8 GB RAM free for Docker, 10 GB disk (30+ GB with all runs, 5 GB more for Caravel) | the flow container is capped at 2 CPUs / 8 GB (`PROFILE=tight`) |
| Internet for: apt, the LibreLane image (about 2 GB), the sky130A PDK (1 to 2 GB), `hermes3:8b` (4.7 GB) | all downloads are pinned in `versions.lock` |
| A desktop session (X11 or Wayland) for the GUI windows only | everything else works headless and over ssh |

Nothing here logs in anywhere, pushes, submits or publishes.

## 2. One-time setup

```bash
git clone <this repository> ~/open-ai-chip && cd ~/open-ai-chip
bash scripts/setup_linux.sh --dry-run        # read what it will run (nothing is executed)
bash scripts/setup_linux.sh                  # then run it; sudo asks for your password
```

Flags: `--yes` (non-interactive: apt `-y`, and permission to pipe the Ollama installer to `sh`), `--no-docker`,
`--no-pdk`, `--no-ollama`, `--no-gui`, `--no-apt`, `--webui` (Open WebUI and desktop dependencies; calls
`examples/hermes_desktop/setup_webui.sh`), `--caravel` (clone the Caravel sources, about 5 GB). The script is
idempotent: rerun it any time, finished steps are skipped.

| # | Step | What it does |
|---|---|---|
| 1 | apt packages | git, make, python3, python3-venv, python3-pip, jq, curl, iverilog, `gcc-riscv64-unknown-elf` + binutils (the RISC-V bare-metal compiler), `klayout`, `x11-xserver-utils` (xhost), `zstd` and `xz-utils` (Ollama installer) |
| 2 | RISC-V names | the firmware Makefiles use `CROSS ?= riscv64-elf-` (Homebrew's name); Debian names the same compiler `riscv64-unknown-elf-`. The script links `riscv64-elf-{gcc,as,ld,objcopy,size,...}` to the Debian binaries in `/usr/local/bin` (or `~/.local/bin` without sudo), so no repository file changes. Alternative without links: `make soc-sim CROSS=riscv64-unknown-elf-` (but `make test` looks for `riscv64-elf-gcc` and would skip the SoC sims) |
| 3 | Docker Engine | `docker.io`, `systemctl enable --now docker`, you are added to the `docker` group |
| 4 | LibreLane image | `docker pull ghcr.io/librelane/librelane:3.0.2` (from `versions.lock`) |
| 5 | sky130A PDK | `ciel enable --pdk-family sky130 --pdk-root ~/.volare 8afc8346...` in `build/setup/ciel-venv`; `~/.volare/sky130A` points at the pinned commit, which is what `make doctor` checks |
| 6 | agent venv | `build/agent/venv` with `klayout mcp pytest fastapi uvicorn httpx` (Python 3.10 or newer) |
| 7 | KLayout, X11 | apt `klayout` (Ubuntu 22.04: 0.27.x, 24.04: 0.28.x; the venv's `klayout` module is a separate, newer wheel), `xhost +SI:localuser:root` so the container may draw on your screen |
| 8 | Ollama | official installer `curl -fsSL https://ollama.com/install.sh \| sh` (printed, run only with `--yes` or after you answer `y`), then `ollama pull hermes3:8b`. Env `HERMES_MODEL=<tag>` pulls another model |
| 9 | optional | `--webui`, `--caravel`; without them the Caravel `git clone` commands are only printed |

Then the script runs `make doctor` and prints a summary table.

**Log out and in again** (or run `newgrp docker`) once, because the `docker` group applies only to new sessions. Check:

```bash
groups | grep -w docker && docker ps        # no sudo needed
make doctor                                 # all PASS: python3, iverilog, docker, image, sky130A, disk
```

Other distributions: the script prints the Debian package names and stops short of apt. Install the equivalents
(Fedora `dnf`, Arch `pacman -S riscv64-elf-gcc iverilog ...`; Arch already uses the `riscv64-elf-` name), Docker, then
rerun `bash scripts/setup_linux.sh --no-apt`. The RISC-V compiler needs the `rv32i/ilp32` multilib
(`riscv64-elf-gcc -print-multi-lib | grep rv32i/ilp32`); Debian's package has it.

## 3. Run all, one command

```bash
bash scripts/run_all.sh                         # verify everything without re-running physical flows
bash scripts/run_all.sh --all                   # + flows, precheck, Hermes agents, GUI windows
bash scripts/run_all.sh --all --keep-going      # the full run; does not stop at a failure
bash scripts/run_all.sh --no-docker             # host without Docker: only the stages that need none
```

`run_all.sh` is the OS-aware version of `run_all_mac.sh` (which now just calls it). Options and the stage table are
those of [RUN_ON_MAC.md](RUN_ON_MAC.md) section 3. On Linux it exports `DOCKER_HOST=unix:///var/run/docker.sock`
(no Colima), wraps itself in `systemd-inhibit` so the machine does not suspend during a long run, checks `DISPLAY` and
`/tmp/.X11-unix/X<n>` instead of XQuartz, and finds KLayout with `command -v klayout` (override `KLAYOUT_BIN`).
One PASS/FAIL/SKIP line per stage, logs in `build/run_all/<timestamp>/`.

## 4. Run the parts one by one

Same targets as on the Mac; times are from the Mac build host (section 6 of RUN_ON_MAC.md), not measured on Linux.

| Goal | Command |
|---|---|
| Machine check | `make doctor` |
| Fast gate (no Docker) | `make test` |
| One design RTL to GDSII | `make flow-all DESIGN=kv_attn_n8` |
| All designs (current runs reused) | `make all-designs` |
| Heavy local checks | `make test-full` (`FLAGS="--precheck --fullgl"`) |
| RTL / gate-level sim of one design | `make simulate DESIGN=<d>`, `make gl DESIGN=<d>`, `make gl-final DESIGN=<d>` |
| Firmware on the PicoRV32 SoC | `make soc-sim`, `make soc-kv` (need `riscv64-elf-gcc`, step 2) |
| Caravel full-chip | download first: `bash scripts/setup_linux.sh --caravel` (or the two `git clone` lines it prints), then `make caravel-rtl`, `make caravel-gl`, `make caravel-fullgl`; `caravel-sdf-wrapper` also needs an amd64 CVC image (on an aarch64 host it runs under emulation) |
| Tapeout precheck | `make precheck` (own container, 14 checks) |
| Results tables | `make table` |

Physical flows run one at a time (the Makefile caps each at 10 minutes). `docker ps` first: another session may be running.
On Linux the LibreLane container runs as root, so `designs/<d>/runs/` and `build/` files it creates are owned by root;
if `make clean` or `git clean` complains: `sudo chown -R "$USER" designs build`. (`make flow-all DOCKER_EXTRA="-u $(id -u):$(id -g)"` should
avoid it; it was not tried here.)

## 5. The Hermes agents

All need `ollama serve` (the installer starts a systemd service; otherwise `ollama serve &`) and `ollama pull hermes3:8b`.
Details: [HERMES_FROM_TERMINAL.md](HERMES_FROM_TERMINAL.md), [HERMES_AGENT.md](HERMES_AGENT.md).

```bash
PY=build/agent/venv/bin/python
curl -s localhost:11434/api/tags | grep -c hermes3                            # 1 = model present
$PY tools/hermes_agent.py "How many standard cells does vision_block have?"     # chip Q&A, 10 read-only tools
$PY examples/hermes_harness/harness.py "which design has the most cells?" --all   # harness: guardrails, grounding, plan
$PY examples/hermes_rag/rag_agent.py "Why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8?" --router --guardrail
$PY examples/hermes_klayout_demo/demo.py --dry-run                            # no model needed
```

KLayout demo, offscreen (PNG files, no window, works over ssh):

```bash
$PY examples/hermes_klayout_gui/agent.py "open kv_attn_n8, show li1 and met1, zoom to the lower-left 50x50 um, snapshot"
xdg-open build/agent/klayout_gui/*.png
$PY examples/hermes_klayout_gui/demo.py --live          # the 5 scripted scenarios, offscreen
```

KLayout live window (needs a desktop session; terminal 1 opens the window, terminal 2 talks to it):

```bash
bash examples/hermes_klayout_gui/start_live.sh                                 # terminal 1; KLAYOUT_BIN=... to override
$PY examples/hermes_klayout_gui/agent.py --backend live "open user_project_wrapper_soc_kv, show only met4 and met5, zoom to the macro mprj"
$PY examples/hermes_klayout_gui/demo.py --live --backend live                  # all scenarios on the window
```

On Linux `start_live.sh` uses `klayout` from PATH (apt). Close the window with its button; if it hangs, `pkill -9 klayout`.
Opt-in tests: `HERMES_LIVE=1 $PY -m pytest -q tests/tools/test_live_smoke.py`, `KLAYOUT_LIVE=1 $PY -m pytest -q tests/tools/test_klayout_live.py`.

Desktop / Open WebUI (only if `examples/hermes_desktop/` exists; see [HERMES_DESKTOP.md](HERMES_DESKTOP.md)):

```bash
bash scripts/setup_linux.sh --webui           # Open WebUI into build/webui/venv (several GB), pywebview GTK backend
bash examples/hermes_desktop/start.sh         # Ollama, tool server 127.0.0.1:8770, Open WebUI 127.0.0.1:8080
xdg-open http://127.0.0.1:8080
bash examples/hermes_desktop/stop.sh
```

If the desktop window (pywebview) does not start, use the browser URL: it is the same UI. Open WebUI wants Python 3.11
or 3.12 (Ubuntu 24.04 has 3.12; on 22.04 install 3.11 from the deadsnakes PPA and run `PY312=python3.11 bash examples/hermes_desktop/setup_webui.sh`).

## 6. GUIs on Linux

OpenROAD GUI and Magic run inside the LibreLane container and draw on your X server through `/tmp/.X11-unix`.
`scripts/gui/open_gui.sh` passes `DISPLAY` (and `XAUTHORITY` when set) in; the container is root, so grant it once per login:

```bash
xhost +SI:localuser:root                        # setup_linux.sh does this when DISPLAY is set; undo: xhost -SI:localuser:root
bash scripts/gui/open_gui.sh openroad kv_attn_n8
bash scripts/gui/open_gui.sh heatmaps kv_attn_n8        # DWELL=12 ROUNDS=3: layout, placement density, congestion, power, IR drop
bash scripts/gui/open_gui.sh magic kv_attn_n8           # type Magic commands in the terminal, quit to end
bash examples/openroad_gui/render_views.sh kv_attn_n8   # PNGs without a window; export DOCKER_HOST=unix:///var/run/docker.sock first
```

KLayout on a finished GDS with sky130 colours:

```bash
klayout "$(ls $(python3 scripts/flow/find_reusable_run.py kv_attn_n8)/final/gds/*.gds)" -l ~/.volare/sky130A/libs.tech/klayout/tech/sky130A.lyp
```

Layout picture without any viewer setup: `xdg-open designs/kv_attn_n8/output/layout.png`.

Wayland: GNOME and KDE start XWayland, so `DISPLAY` (for example `:0` or `:1`) is set and the commands are unchanged.
`xhost` works against XWayland; if a window does not appear, check `echo $DISPLAY` and `ls /tmp/.X11-unix`.
Windows over `ssh -X` are not supported by `open_gui.sh` (the container needs the local X socket): use a local desktop session.
Headless server: everything except the windows works; use the offscreen commands.

## 7. If something fails

| Symptom | Fix |
|---|---|
| `permission denied ... /var/run/docker.sock` | the docker group is not active yet: log out and in, or `newgrp docker` |
| `Cannot connect to the Docker daemon` | `sudo systemctl start docker`; check `echo $DOCKER_HOST` (unset or `unix:///var/run/docker.sock`) |
| `make test` NOTE `riscv64-elf-gcc not found` | step 2 of the setup; check `which riscv64-elf-gcc` and that `/usr/local/bin` is on PATH |
| `make test` docs links FAIL `../open-ai-silicon/...` | the sibling reference repository is not checked out next to this one (it is only used for links in `SPEC.md`); clone it beside this repo or ignore |
| RAG tests fail (`search_docs`) | the corpus comes from `git ls-files`: use a `git clone`, not a tarball without `.git` |
| `make doctor` sky130A FAIL | `bash scripts/setup_linux.sh --no-apt --no-docker --no-ollama --no-gui`, or `PDK_ROOT=...` |
| Hermes stages SKIP | `ollama serve &` and `ollama pull hermes3:8b` |
| windows do not open | `echo $DISPLAY; ls /tmp/.X11-unix; xhost +SI:localuser:root`; on SSH use the offscreen commands |
| root-owned files in `build/` or `runs/` | `sudo chown -R "$USER" designs build` |
| `port 8765 is already in use` | an old KLayout bridge window is open: close it or `pkill -9 klayout` |
| a design shows STALE | `make flow-all DESIGN=<d>` re-hardens it (one flow at a time) |

## 8. Where the results are

Same as the Mac: `build/run_all/<timestamp>/summary.txt` and one log per stage, `designs/<d>/output/`, the README tables
(`make table`), [VALIDATION.md](VALIDATION.md), [GUI_AND_LOGS.md](GUI_AND_LOGS.md).

## 9. What differs from macOS

| | macOS (RUN_ON_MAC.md) | Linux (this page) |
|---|---|---|
| RISC-V compiler | `brew install riscv64-elf-gcc` | apt `gcc-riscv64-unknown-elf` + `riscv64-elf-*` links |
| Docker | Colima VM `osl`, `DOCKER_HOST=unix://$HOME/.colima/osl/docker.sock` | Docker Engine, `unix:///var/run/docker.sock`, `docker` group |
| Keep awake | `caffeinate -dimsu` | `systemd-inhibit` (automatic in `run_all.sh`) |
| X server | XQuartz over TCP 6000, container display `192.168.5.2:0` | native X11 / XWayland socket, `xhost +SI:localuser:root` |
| KLayout | `/Applications/KLayout/klayout.app` | `klayout` from apt (`KLAYOUT_BIN` overrides) |
| open files | `open` | `xdg-open` |
| Files made by the container | owned by you (VM mapping) | owned by root (see section 4) |

## 10. What was verified, and how

**Status: the Linux steps on this page are not validated on a real Linux host with Docker, a GPU, Wayland or a desktop.**
`scripts/setup_linux.sh`, `scripts/run_all.sh`, the Linux branches of `scripts/gui/open_gui.sh` and
`examples/hermes_klayout_gui/start_live.sh` and this page were written from the macOS scripts and the Debian package
names, and checked only as follows (2026-10-06, on the build Mac):

- `bash -n` on every script; `bash scripts/setup_linux.sh --dry-run --all` prints every command and runs none.
- `bash tests/run_tests.sh` on macOS: ALL PASSED (the macOS behaviour of the edited scripts is unchanged).
- An earlier trial in a fresh `ubuntu:24.04` aarch64 container (apt step, `riscv64-elf-*` links, agent venv, no Docker,
  PDK, Ollama or GUI): setup took about 60 s and `make test` ran in about 70 s with the SoC firmware sims and the tools
  pytest PASS. That trial found two things, both handled: a root HOME made the "no absolute home paths" check hit a
  comment in `open_gui.sh` (reworded), and the docs-links check fails without the sibling `../open-ai-silicon`
  checkout (section 7). It was not repeated after the final edits, and the Ollama installer download did not finish.

Untested: real Docker flows and the group re-login, the ciel PDK download, the Ollama installer, Open WebUI and
pywebview on Linux, X11/XWayland windows, `systemd-inhibit`, the root-owned-files behaviour, x86_64 hosts.
