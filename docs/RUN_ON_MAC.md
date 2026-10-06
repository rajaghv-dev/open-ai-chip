# Run everything from the MacBook terminal

On Linux (Ubuntu/Debian) see [RUN_ON_LINUX.md](RUN_ON_LINUX.md): `scripts/setup_linux.sh` and the OS-aware `scripts/run_all.sh`.

Step by step: open a terminal, prepare the machine once, then run every part of this repository (tests, flows, sims,
precheck, Hermes agents) and open every GUI (KLayout, OpenROAD with heat maps, Magic). One script does it all in order:
`scripts/run_all_mac.sh`. Verified on the build Mac (Apple M4 Pro, 24 GB, macOS, Colima profile `osl`); measured times
are in section 6.

## 1. Open a terminal in the repo

```bash
# Terminal.app or iTerm: Cmd+Space, type "Terminal", Enter
cd ~/raja/open-ai-chip          # or wherever you cloned it
git pull                        # optional: latest version
```

Every command below is run from this directory.

## 2. One-time setup

| What | Command | Check |
|---|---|---|
| Homebrew tools (simulator, RISC-V compiler) | `brew install icarus-verilog riscv64-elf-gcc` (`python3` and `jq` ship with macOS) | `iverilog -V`, `riscv64-elf-gcc --version` |
| Docker runtime: Colima VM `osl` (6 CPU, 16 GB) | `brew install colima docker && colima start -p osl --cpu 6 --memory 16 --disk 60 --vm-type vz` | `colima status -p osl` |
| LibreLane image and sky130A PDK | `make doctor` (lists what is missing and how to get it) | `make doctor` all OK |
| Agent Python environment | `python3 -m venv build/agent/venv && build/agent/venv/bin/pip install klayout mcp pytest` | `build/agent/venv/bin/python -c "import klayout.lay, mcp"` |
| Local model (Hermes agents) | `brew install ollama` (or the Ollama app), `ollama serve &`, `ollama pull hermes3:8b` (4.7 GB) | `ollama list` |
| KLayout desktop app | `brew install --cask klayout`, then approve it once: right-click `/Applications/KLayout/klayout.app` > Open | `/Applications/KLayout/klayout.app/Contents/MacOS/klayout -b -r /dev/null` returns |
| XQuartz (OpenROAD GUI and Magic windows) | `brew install --cask xquartz`, log out and in, then `defaults write org.xquartz.X11 nolisten_tcp -bool false`, `open -a XQuartz`, `DISPLAY=:0 /opt/X11/bin/xhost +localhost` | `lsof -nP -iTCP:6000 -sTCP:LISTEN` shows X11.bin |

The Docker socket of this repo is the `osl` profile: `export DOCKER_HOST=unix://$HOME/.colima/osl/docker.sock` (the
Makefile and the scripts set it themselves; a bare `docker ps` without it talks to the wrong socket).

Keep the Mac awake during long runs: prefix a command with `caffeinate -dimsu`.

## 3. Run all, one command

```bash
bash scripts/run_all_mac.sh                  # verify everything, no physical flow re-run (section 6: time)
bash scripts/run_all_mac.sh --all            # + flows, precheck, Hermes agents and the GUI windows
caffeinate -dimsu bash scripts/run_all_mac.sh --all --keep-going   # the full run, Mac kept awake, does not stop at a failure
```

Options: `--flows` (`make all-designs`; current runs are reused, only stale designs are re-hardened), `--precheck`,
`--fullgl` (full-chip gate-level Caravel, about 14 min), `--agents` (needs Ollama), `--gui` (opens windows on your
screen), `--design <d>` (design for the pictures and windows, default `kv_attn_n8`), `--keep-going`.

It prints one line per stage and a summary; every stage's log is under `build/run_all/<timestamp>/`
(`summary.txt` there). The stages, in order:

| # | Stage | What it runs |
|---|---|---|
| 0 | preflight | Colima `osl` running (starts it if not), `make doctor`, the agent venv |
| 1 | `make test` | structure, configs, lint of all 25 designs, model checks, generated-file reproducibility, RTL sims, adapter, SoC sims, negative tests, docs checks, agent/tools pytest |
| 2 | `make all-designs` (with `--flows`) | the full RTL-to-GDSII flow of all 25 designs in order; current runs are reused |
| 3 | `make test-full` | every design: run state, simulate, signoff check, routed gate-level sim; adapter, SoC sims, Caravel RTL and hybrid gate-level; plus precheck / full-chip GL when asked |
| 4 | pictures, no window | OpenROAD engine views offscreen, the KLayout + Hermes plumbing in dry-run, the tools pytest |
| 5 | Hermes (with `--agents`) | the 6 live smoke tests, a chip question, the KLayout demo offscreen |
| 6 | GUI windows (with `--gui`) | KLayout live-window tests, Magic and OpenROAD windows on XQuartz, the OpenROAD heat-map cycle |

## 4. Run the parts one by one

| Goal | Command | Time on this Mac |
|---|---|---|
| Machine check | `make doctor` | seconds |
| Fast gate | `make test` | about 100 s |
| One design, RTL to GDSII | `make flow-all DESIGN=kv_attn_n8` | 105 s flow (all 25 from scratch: 2,047 s, about 34 min, `resources.json` sum) |
| All designs | `make all-designs` | reuses current runs |
| Heavy local checks | `make test-full` (`FLAGS="--precheck --fullgl"`) | about 5 min without the flags |
| RTL sim / gate-level of one design | `make simulate DESIGN=<d>`, `make gl DESIGN=<d>`, `make gl-final DESIGN=<d>` | seconds |
| Firmware on the RISC-V SoC | `make soc-sim`, `make soc-kv` | about 25 s, 14 s |
| Caravel full-chip | `make caravel-rtl`, `make caravel-gl`, `make caravel-fullgl` | about 53 s, 58 s, 14 min |
| Tapeout precheck | `make precheck` | about 1 min |
| Results tables in README | `make table` | seconds |

## 5. Open every GUI from the terminal

| GUI | Command | Notes |
|---|---|---|
| Layout picture of a design | `open designs/kv_attn_n8/output/layout.png` or `make view DESIGN=kv_attn_n8` | no setup |
| KLayout, a design's GDS with sky130 colours | `open -a /Applications/KLayout/klayout.app --args "$(ls $(python3 scripts/flow/find_reusable_run.py kv_attn_n8)/final/gds/*.gds)" -l ~/.volare/sky130A/libs.tech/klayout/tech/sky130A.lyp` | the KLayout app |
| KLayout driven by Hermes | terminal 1: `bash examples/hermes_klayout_gui/start_live.sh`; terminal 2: `build/agent/venv/bin/python examples/hermes_klayout_gui/agent.py --backend live "open kv_attn_n8, show li1 and met1, zoom to the lower-left 50x50 um, snapshot"` | Ollama running; guide: [HERMES_FROM_TERMINAL.md](HERMES_FROM_TERMINAL.md) |
| All 5 KLayout scenarios | `build/agent/venv/bin/python examples/hermes_klayout_gui/demo.py --live --backend live` | with the window of terminal 1 |
| OpenROAD GUI | `bash scripts/gui/open_gui.sh openroad kv_attn_n8` | XQuartz |
| OpenROAD heat maps, live | `bash scripts/gui/open_gui.sh heatmaps kv_attn_n8` (`DWELL=12 ROUNDS=3`) | layout, placement density, routing congestion, power density, IR drop |
| Magic | `bash scripts/gui/open_gui.sh magic kv_attn_n8` | XQuartz; type Magic commands in the terminal, `quit` to end |
| OpenROAD views as PNG, no window | `bash examples/openroad_gui/render_views.sh kv_attn_n8`, then `open build/agent/openroad_gui/kv_attn_n8/*.png` | Docker only |

Close a window with its window button. KLayout ignores a plain `kill`; from a terminal use `pkill -9 -f klayout.app`.
After using the X11 windows you may withdraw the access: `DISPLAY=:0 /opt/X11/bin/xhost -localhost`.

## 6. Measured: one full run on this Mac

`caffeinate -dimsu bash scripts/run_all_mac.sh --all --keep-going`, 2026-10-06, all 25 designs current (19 min in total):

| Stage | Result | Time |
|---|---|---|
| preflight: Colima `osl`, `make doctor`, agent venv | PASS | 2 s |
| `make test` | PASS | 100 s |
| `make all-designs` (all 25 runs current, so reused: gds 0 to 1 s each; the time is simulate, check, gate-level, collect) | PASS | 395 s |
| `make test-full --precheck` | PASS | 344 s |
| OpenROAD engine views offscreen | FAIL in the run, PASS on rerun (8 views) | - |
| KLayout + Hermes dry run, agent/tools pytest | PASS | 5 s |
| Hermes live smoke tests (6 agents), chip Q&A, KLayout demo offscreen | PASS | 57 s, 8 s, 23 s |
| KLayout live window tests (11) | PASS | 2 s (an earlier bridge window was still open, so the test used it) |
| Magic + OpenROAD windows on XQuartz | PASS | 11 s |
| OpenROAD live heat maps, 1 round | PASS | 220 s (the stage ends when the window is closed) |

The one failure: the off-screen renderer refuses to start beside another container, and an OpenROAD heat-map window
from an earlier session was still open. The script now reports SKIP with that reason instead of FAIL; close the window
and rerun. With physical flows from scratch (no current runs) add the flow time: 2,047 s for all 25 designs
(`designs/*/output/resources.json`, my sum).

## 7. Where the results are

| What | Where |
|---|---|
| Summary of a run_all run | `build/run_all/<timestamp>/summary.txt`, one log per stage next to it |
| Per-design evidence | `designs/<d>/output/` (metrics.json, reports, layout.png), the page `designs/<d>/NOTES.md` |
| Results tables | `README.md` "Measured results" (`make table`) |
| Validation of all runs | [docs/VALIDATION.md](VALIDATION.md) |
| Every log of the flows and sims | [docs/GUI_AND_LOGS.md](GUI_AND_LOGS.md) |

## 8. If something fails

| Symptom | Fix |
|---|---|
| `Cannot connect to the Docker daemon` | `colima start -p osl`; use `DOCKER_HOST=unix://$HOME/.colima/osl/docker.sock` |
| A design shows STALE | its inputs changed after its run; `make flow-all DESIGN=<d>` re-hardens it (one flow at a time) |
| Hermes stages SKIP | `ollama serve &` and `ollama pull hermes3:8b` |
| KLayout hangs at launch | approve it once in Finder (right-click > Open) |
| XQuartz stages SKIP | the XQuartz setup line of section 2, then `open -a XQuartz` |
| `port 8765 is already in use` | an old KLayout bridge window is open: close it or `pkill -9 -f klayout.app` |
| The Mac slept during a long run | rerun with `caffeinate -dimsu` in front |
