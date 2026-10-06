# Opening every GUI and every log

A practical how-to for a macOS terminal and a Linux terminal. Commands marked "verified" were run on the Mac that built
this repo (Apple Silicon, Colima profile `osl`); "not tested here" means Linux-only or needs an approval/install that
was not done. Paths are repo-relative; run everything from the repo root. `<d>` is a design name (`make help` lists them).

## 0. Quick reference: "I want to see X"

| I want to see | macOS | Linux |
|---|---|---|
| The newest complete run dir of a design | `python3 scripts/flow/find_reusable_run.py <d>` (verified) | same |
| A flow that is running right now | `tail -f build/flow_<d>.log` or `tail -f designs/<d>/runs/RUN_*/flow.log` | same |
| Which stages passed | `cat build/flow/<d>/stages.txt` (verified) | same |
| Key numbers of a design | `jq '."design__instance__count"' designs/<d>/output/metrics.json` (verified) | same |
| The layout picture (PNG) | `open designs/<d>/output/layout.png` (verified, `open` exists) | `xdg-open designs/<d>/output/layout.png` (not tested here) |
| Summary plus picture of results | `make view DESIGN=<d>` (`ARGS=--no-gui` for text only) | same, uses `xdg-open` (not tested here) |
| The GDS in KLayout, sky130 colours | `open -a /Applications/KLayout/klayout.app --args FILE.gds -l LYP` (verified; a fresh install needs a one-time Gatekeeper approval, section 2) | `klayout FILE.gds -l LYP` (not tested here) |
| DRC markers in KLayout | add `-m FILE.lyrdb`, or Tools > Marker Browser (section 2) | same (not tested here) |
| A GDS without any window | `build/agent/venv/bin/python examples/hermes_klayout_gui/agent.py --dry-run` (offscreen, no display) | same (not tested here) |
| OpenROAD GUI on a finished run | `bash scripts/gui/open_gui.sh openroad <d>` with XQuartz (verified, section 3) | same with the X11 socket (not tested here) |
| OpenROAD engine heat maps, live | `bash scripts/gui/open_gui.sh heatmaps <d>` (cycles layout, placement density, routing congestion, power density, IR drop; `DWELL=12 ROUNDS=3`; verified) | same flow with X11/Wayland socket (not tested here) |
| The GDS in Magic (the repo's default layout viewer) | `bash scripts/gui/open_gui.sh magic <d>` with XQuartz (verified, section 3) | same with the X11 socket (not tested here) |
| Testbench result of an RTL sim | `cat build/sim/<d>/sim.log` | same |
| Gate-level sim result | `cat build/gl/<d>/result.txt build/gl/<d>/sim/gl.log` | same |
| Waveforms (VCD) | no GTK-based viewer is used in this repo; read the testbench PASS/FAIL logs instead (section 4) | same |
| Precheck results | `ls precheck/results; cat precheck/results/summary.tsv` | same |
| Agent eval results | `ls build/agent/*.json` | same |
| Docker/Colima state | `colima status -p osl`, `DOCKER_HOST=unix://$HOME/.colima/osl/docker.sock docker ps` | `docker ps` |
| Keep the Mac awake for a long flow | `caffeinate -dims -w <pid>` or `caffeinate -i make flow-all DESIGN=<d>` | `systemd-inhibit make flow-all DESIGN=<d>` (not tested here) |

Tip: the Makefile uses `~/.colima/osl/docker.sock` automatically when `DOCKER_HOST` is unset. For raw `docker` commands in
your own shell, export it first: `export DOCKER_HOST=unix://$HOME/.colima/osl/docker.sock` (a plain `docker ps` on this Mac
otherwise tries the `default` Colima profile and fails with "Cannot connect to the Docker daemon", which was observed).

## 1. Where things are (logs and evidence)

### 1.1 A LibreLane run directory

`designs/<d>/runs/RUN_<date>_<time>/` (git-ignored). Verified layout (example `designs/kv_attn_n8/runs/RUN_2026-10-06_07-00-35`):

| Path | What it is |
|---|---|
| `flow.log` | the whole flow, in order; ends with `Flow complete.` on success |
| `error.log`, `warning.log` | only the ERROR / WARNING lines of the flow (read these first when a stage failed) |
| `resolved.json` | the final merged configuration (every key, including defaults) |
| `NN-tool-step/` | one directory per step, e.g. `06-yosys-synthesis`, `28-openroad-globalplacement`, `39-openroad-globalrouting`, `46-openroad-detailedrouting`, `66-magic-drc`, `67-klayout-drc`, `72-netgen-lvs`; the numbers differ between flows |
| `NN-.../<tool>-<step>.log` | that step's own log, e.g. `openroad-globalplacement.log`, `klayout-drc.log` |
| `NN-.../COMMANDS` | the exact command line the step ran |
| `NN-.../runtime.txt` | wall time of the step |
| `NN-.../state_in.json`, `state_out.json` | the design views going in and out of the step (paths to the ODB, DEF, netlist, ...) plus metrics |
| `NN-.../*.odb`, `*.def`, `*.nl.v`, `*.sdc` | intermediate design views after that step (placement, CTS, routing) |
| `NN-.../reports/` | step reports, e.g. `66-magic-drc/reports/drc.magic.rpt` and `drc.magic.lyrdb`, `67-klayout-drc/reports/drc.klayout.lyrdb` and `.json` |
| `final/` | `gds/`, `klayout_gds/`, `def/`, `odb/`, `lef/`, `lib/`, `nl/`, `pnl/`, `sdc/`, `sdf/`, `spef/`, `spice/`, `mag/`, `render/`, `metrics.json`, `metrics.csv` |

```bash
R=$(python3 scripts/flow/find_reusable_run.py kv_attn_n8)   # newest COMPLETE and CURRENT run (verified)
echo $R
ls $R | head                                     # step directories
cat $R/error.log $R/warning.log | less           # what went wrong or was noisy
cat $R/28-openroad-globalplacement/COMMANDS      # how a step was invoked
cat $R/*/runtime.txt | head                      # per-step seconds (use: for f in $R/*/runtime.txt; do echo "$f $(cat $f)"; done)
ls -d designs/<d>/runs/RUN_* | tail -1           # newest run dir even if incomplete or stale
```

`find_reusable_run.py` prints nothing and exits 1 (reason on stderr) when there is no complete, up-to-date run. The
"newest run dir" one-liner works for a failed or running flow too.

### 1.2 Committed evidence and generated build outputs

| Where | What |
|---|---|
| `designs/<d>/output/` | committed: `metrics.json`, `resources.json`, `flow.log`, `layout.png`, `<top>.lef`, `reports/` (`timing_summary.rpt`, `drc_magic.rpt`, `drc_klayout.json`, `lvs_netgen.rpt`, `routing_detailed.txt`, `cell_usage.rpt`, ...) |
| `build/flow/<d>/stages.txt` | one line per stage of the last `make flow-all`: `simulate PASS 1`, `gds PASS 0`, `check PASS 0`, ... (name, result, seconds or exit code) |
| `build/flow/<d>/stage_<name>.log` | the output of each stage: `stage_simulate.log`, `stage_gds.log`, `stage_check.log`, `stage_gl_synth.log`, `stage_gl_final.log`, `stage_collect.log`; `reuse.err` says why a run was not reused |
| `build/flow_<d>.log`, `build/flow_*.log` | whole-flow logs of batch runs (one file per design) |
| `build/results/<d>/` | what `make collect` keeps: GDS, LEF, `final/{nl,pnl}`, `layout.png`, `metrics.json`, `resources.json`, `flow.log`, `reports/` |
| `build/sim/<d>/sim.log` | RTL testbench log (`tb.vvp` is the compiled image) |
| `build/gl/<d>/` | gate-level sim: `result.txt`, `synth_checks.txt`, `sim/gl.log`, `sim/iverilog.log`, `sim/tb_gl.v` |
| `build/adapter_tests/`, `build/check/` | adapter test and `make check` logs |
| `build/agent/` | Hermes work: `eval_*.json`, `harness_eval_*.json`, `rag_eval_*.json`, `*_run.log`, `klayout_gui/*.png` snapshots, `renders/`, `venv/` |
| `precheck/results/` | ChipFoundry precheck: `summary.tsv`, `evidence.txt` |
| `caravel_sim/`, `build/caravel_rtl_*.log`, `build/caravel_gl_*.log` | full-Caravel sim scripts and their logs (see `docs/CARAVEL_SIM.md`) |
| `soc_sim/`, `firmware/build/` | PicoRV32 SoC sim sources; firmware ELF/hex |

### 1.3 Reading things quickly

```bash
cat build/flow/kv_attn_n8/stages.txt                                   # verified
tail -f build/flow_kv_attn_n8.log                                      # follow a running batch flow
tail -f "$(ls -d designs/<d>/runs/RUN_* | tail -1)/flow.log"            # follow the newest run of a design
grep -c . designs/<d>/runs/RUN_*/warning.log                           # warning counts per run

# metrics.json is a flat key -> value map (311 keys for kv_attn_n8)
jq 'keys[]' designs/kv_attn_n8/output/metrics.json | head              # list keys (verified)
jq -r 'to_entries[] | select(.key|test("drc__|lvs__|timing__setup__ws$|instance__count$")) | "\(.key) \(.value)"' \
   designs/kv_attn_n8/output/metrics.json                              # verified
python3 -c "import json;m=json.load(open('designs/kv_attn_n8/output/metrics.json'));print(m['design__instance__count'], m['timing__setup__ws'])"   # verified, no jq needed
make view DESIGN=kv_attn_n8 ARGS=--no-gui                              # text summary of collected results
make collect DESIGN=<d>                                                # (re)collect results; reuses the newest current run, may run a flow if none exists
```

`make flow-all` and `make gds` can start a physical flow; do not use them just to look at results. `make view`
and `make table` are read-only; `make collect` reuses a current run when there is one.

## 2. KLayout (GDS viewer)

### 2.1 Install and the macOS first-launch approval

Installed here as a Cask app: `/Applications/KLayout/klayout.app` (binary
`/Applications/KLayout/klayout.app/Contents/MacOS/klayout`; verified present). Install: `brew install --cask klayout`.
On Linux: `apt install klayout` or the vendor package (not tested here).

Gatekeeper: a freshly downloaded app carries the quarantine flag and is "rejected" by `spctl`. Launched from a script it
does not show a dialog: the process sits at 0 CPU forever, so the terminal appears to hang. Approve it once by hand:

1. In Finder open `/Applications/KLayout/`, right-click `klayout.app`, choose Open, confirm Open; or
2. Try launching it, then System Settings > Privacy & Security > scroll down > "Open Anyway".

After that every command below works. Do not strip the quarantine flag from a script; that bypasses a macOS security
check and is your decision. (At the time of writing this approval had not been given on this Mac, so none of the GUI
commands in this section were run.)

### 2.2 Open a GDS with the sky130 layer colours

The layer properties file ships with the PDK (verified): `~/.volare/sky130A/libs.tech/klayout/tech/sky130A.lyp`
(also `sky130A.lyt` technology and `sky130A.map`). Use a GDS from a run or from `build/results/`:

```bash
LYP=$HOME/.volare/sky130A/libs.tech/klayout/tech/sky130A.lyp
GDS=$(python3 scripts/flow/find_reusable_run.py kv_attn_n8)/final/gds/kv_attn_n8.gds   # verified path form
# macOS (verified 2026-10-06: the app opened kv_attn_n8 with the sky130 layer colours)
open -a /Applications/KLayout/klayout.app --args "$GDS" -l "$LYP"
#   or directly (keeps the terminal attached, shows errors):
/Applications/KLayout/klayout.app/Contents/MacOS/klayout "$GDS" -l "$LYP" &
# Linux (not tested here)
klayout "$GDS" -l "$LYP" &
```

Useful flags (standard KLayout): `-e` edit mode, `-r script.py` run a script, `-rm macro` run a macro at start, `-zz`
no GUI at all (batch), `-m file.lyrdb` load a marker database. If the flow's own render is enough, skip the GUI:
`open designs/<d>/output/layout.png` (verified command exists).

In the window: press `F` or View > Full Hierarchy; mouse wheel zooms; `Shift+F` shows all; Display > "Show All Hierarchy
Levels"; the layer panel on the right toggles layers (met1 = 68/20, met2 = 69/20, met4 = 71/20, li1 = 67/20).

### 2.3 DRC result databases (.lyrdb)

Each DRC step writes a KLayout report database next to its log (verified paths):

- `<run>/67-klayout-drc/reports/drc.klayout.lyrdb` (KLayout DRC; `.json` is the summary)
- `<run>/66-magic-drc/reports/drc.magic.lyrdb` (Magic DRC; `drc.magic.rpt` is the text form)

```bash
open -a /Applications/KLayout/klayout.app --args "$GDS" -l "$LYP" -m "$R/67-klayout-drc/reports/drc.klayout.lyrdb"   # not tested here
```

Or open the GDS first, then Tools > Marker Browser > File > Open, pick the `.lyrdb`; each category lists violations,
double-click one to zoom to it. These designs are DRC-clean, so the browser shows 0 markers; text check without a
GUI: `cat designs/<d>/output/reports/drc_magic.rpt` and `jq . designs/<d>/output/reports/drc_klayout.json` (the second
verified to exist).

### 2.4 Off-screen renderer and the Hermes-driven view

`examples/hermes_klayout_gui/` (read its `README.md` and `README_live.md`). Option A is offscreen: no window, so no
Gatekeeper problem; it renders PNGs under `build/agent/klayout_gui/` (verified: snapshots already there).

```bash
build/agent/venv/bin/python examples/hermes_klayout_gui/agent.py --dry-run          # scripted calls, no Ollama
build/agent/venv/bin/python examples/hermes_klayout_gui/agent.py "open kv_attn_n8, show li1 and met1, zoom to the lower-left 50x50 um, take a snapshot"   # needs Ollama + hermes3:8b
open build/agent/klayout_gui/                                                       # mac; Linux: xdg-open
```

Option B drives a real window: `bash examples/hermes_klayout_gui/start_live.sh [design]` starts KLayout with a bridge on
`127.0.0.1:8765`, then `agent.py --backend live "..."`. Verified 2026-10-06: 11 live tests pass (`KLAYOUT_LIVE=1 pytest -q tests/tools/test_klayout_live.py`) and Hermes drove
the window on three requests (`examples/hermes_klayout_gui/README_live.md`). KLayout ignores SIGTERM: close it with the
window button or `pkill -9 -f klayout.app`.

## 3. OpenROAD GUI

OpenROAD lives only inside the LibreLane container image `ghcr.io/librelane/librelane:3.0.2` (the host has no
`openroad`, `magic` or `klayout` on PATH; verified with `which`). The GUI is Qt/X11, so a display must reach the container.

LibreLane has viewer flows (verified from the image: `librelane --help` lists
`-f/--flow [optimizing|classic|vhdlclassic|chip|openinklayout|openinopenroad|openinmagic|synthesisexploration]`,
`--last-run`, `--run-tag`, `--design-dir`, `--dockerized`). `OpenInOpenROAD` and `OpenInKLayout` open the final state of a
finished run instead of running anything:

```bash
export DOCKER_HOST=unix://$HOME/.colima/osl/docker.sock          # mac only
docker run --rm ghcr.io/librelane/librelane:3.0.2 librelane --help   # verified, read-only
# Alternative sketch (not tested here; scripts/gui/open_gui.sh is the verified way): LibreLane's own viewer flow.
#   librelane designs/<d>/config.json --flow OpenInOpenROAD --last-run
# It must run inside the container (or --dockerized) with the repo mounted, the same PDK_ROOT as the Makefile, and DISPLAY set.
```

Read the `docker run` line in the Makefile (`grep -n 'docker run' Makefile`) and copy its mounts, PDK and user flags so
the run directory resolves to `designs/<d>/runs/`.

Verified 2026-10-06 on this Mac (XQuartz 2.8.6, Colima `osl`): both GUIs open from the container on XQuartz with one
command, `bash scripts/gui/open_gui.sh openroad|magic <design>` (windows seen: "OpenROAD - kv_attn_n8",
"OpenROAD - user_project_wrapper", Magic "kv_attn_n8" and "soc_kv_attn_n8").

One-time macOS setup:

```bash
defaults write org.xquartz.X11 nolisten_tcp -bool false   # XQuartz > Settings > Security > Allow connections from network clients
open -a XQuartz                                            # restart it after the setting; it listens on TCP 6000
DISPLAY=:0 /opt/X11/bin/xhost +localhost                  # Colima delivers the VM's connection as localhost
```

Then `bash scripts/gui/open_gui.sh openroad <d>` (the final ODB of the current run) or `bash scripts/gui/open_gui.sh magic <d>`
(the final GDS). Inside the container the Mac is `192.168.5.2` (Colima's `host.lima.internal`), so the script sets
`DISPLAY=192.168.5.2:0`; `host.docker.internal` does not resolve in Colima. Things learned:

- XQuartz needs network clients allowed (the `nolisten_tcp` setting) and `xhost +localhost`; it listens on all
  interfaces, so keep the xhost list minimal and run `DISPLAY=:0 /opt/X11/bin/xhost -localhost` when done.
- OpenROAD prints a `qt.glx ... FBConfig` warning over XQuartz and falls back to software drawing: harmless.
- Magic reads commands from stdin and quits at end-of-file: run it with a terminal (`docker run -it`, the script does
  this when you start it from a terminal) or keep stdin open (the script's `GUI_SECONDS` when there is no terminal).
- The PDK's `sky130A.magicrc` names the tech file by its build path (`/root/.ciel/...`); pass `-T` with the tech file
  under `$PDK_ROOT/sky130A/libs.tech/magic/` (the script does).
- Close the window to end the session; nothing is written back to the run.

Display, Linux (not tested here): `docker run ... -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix ...` plus
`xhost +local:docker` (undo with `xhost -local:docker`). Over ssh use `ssh -X host` first.

Engine heatmap views (placement density, congestion, IR drop pictures of the tiny engines): see
[examples/openroad_gui/README.md](../examples/openroad_gui/README.md) (off-screen engine views: layout, placement density, routing congestion, power density, IR drop, clock tree, worst setup path; `examples/openroad_gui/render_views.sh <design>`).
A scratch dir `build/agent/openroad_gui/` exists.

## 4. Other viewers and logs

### Magic (inside the container)

Magic is not on the host; it runs in the container: `bash scripts/gui/open_gui.sh magic <design>` (verified, section 3).
The LibreLane `OpenInMagic` flow is an alternative (not tested here). Magic's own outputs are readable as text without a GUI: `<run>/66-magic-drc/magic-drc.log`,
`<run>/59-magic-streamout/`, `<run>/final/mag/`.

### Waveforms (VCD)

Only one testbench dumps a waveform (verified with grep): `caravel_sim/tiny_ai_wb_tb.v` calls
`$dumpfile("tiny_ai_wb.vcd")`; the file lands in the working directory of that simulation (verified: `build/caravel/work/tiny_ai_wb.vcd`
exists; find others with `find build caravel_sim -name '*.vcd'`). The per-design testbenches are
self-checking and print PASS/FAIL, they do not dump. To get a waveform from one, add `$dumpfile`/`$dumpvars` to a
scratch copy of the testbench, never to the committed one if the design's evidence must stay current.

Viewers: the repo uses no GTK-based tool (GTKWave was removed by owner decision, 2026-10-06). The testbenches are
self-checking, so the PASS/FAIL logs below are the primary evidence; the VCD stays available for any viewer you choose.

### Simulation logs

`make simulate DESIGN=<d>` log: `build/sim/<d>/sim.log`. `make soc-sim` / `make soc-kv`: output on the terminal and
under `soc_sim/` and `firmware/build/`; the table is described in `firmware/README.md`. `make caravel-rtl` and
friends write `build/caravel_rtl_*.log` / `build/caravel_gl_*.log`.

### Hermes agent, Ollama, traces

```bash
curl -s localhost:11434/api/tags | head -c 200     # verified: lists hermes3:8b when Ollama is up
ollama ps                                          # loaded model and idle timeout (verified command works)
ollama list                                        # installed models
ollama serve &                                     # start it if the curl fails (mac app: open -a Ollama)
build/agent/venv/bin/python tools/hermes_agent.py "How many standard cells does vision_block have?"   # docs/HERMES_AGENT.md
ls build/agent/eval_*.json build/agent/harness_eval_*.json build/agent/rag_eval_*.json                 # scored runs
jq '.' build/agent/eval_20261006_143402.json | less           # per-question tool calls, answers, timings
```

Transcripts of the demos are plain text and replayable by reading: `examples/hermes_klayout_gui/transcript_live.txt`,
`transcript_dry_run.txt`, `examples/hermes_klayout_demo/transcript_*.txt`. To re-run a scripted one:
`build/agent/venv/bin/python examples/hermes_klayout_gui/demo.py --dry-run --save-img` (no Ollama).

### From the Hermes chat

The same logs and viewers are reachable by prompt (docs/HERMES_DESKTOP.md "Everything you can ask"): `/log kv_attn_n8 error`, `/log-errors kv_attn_n8` (errors and slowest steps),
`/open <file>`, `/open-gds kv_attn_n8 klayout-app` (the KLayout application with the sky130 layer file, as in section 2.2). Tools: `list_logs`, `read_log`, `log_digest`, `open_gds`, `open_file`
(`examples/hermes_desktop/tool_server/logs_tools.py`).

### Docker and Colima

```bash
colima status -p osl                                         # verified: running, aarch64, docker runtime
colima start -p osl                                          # if it is stopped (use your usual profile flags)
export DOCKER_HOST=unix://$HOME/.colima/osl/docker.sock
docker ps                                                    # check before starting a flow: only one at a time
docker logs -f <container>                                   # a running flow container
docker run --rm ghcr.io/librelane/librelane:3.0.2 librelane --version
make doctor                                                  # host tools, daemon, image, PDK
```

Linux: `docker ps` and `docker logs -f` work the same, with no `DOCKER_HOST` (not tested here).

### Keeping the Mac awake

A flow is capped at 10 minutes by `FLOW_TIMEOUT`, but batch targets (`make all-designs`) take hours and the Mac sleeping
stops Colima's VM progress. `caffeinate -i make all-designs` (verified `caffeinate` exists) blocks idle sleep while the
command runs; `caffeinate -dims -w <pid>` attaches to an existing process.

## 5. What was and was not verified

Verified on this Mac: every repo path and file name above (`ls`), `find_reusable_run.py`, the `jq` and python
one-liners on `designs/kv_attn_n8/output/metrics.json`, `librelane --help` and the flow list in the container,
`colima status -p osl`, Ollama `/api/tags` and `ollama ps`, the `.lyp` location and the KLayout app path.
Not tested at first writing: any GUI window, every Linux
command, the `OpenInOpenROAD` / `OpenInKLayout` invocations, `systemd-inhibit`. Verified later (2026-10-06): the KLayout
app after the Gatekeeper approval, and the OpenROAD GUI and Magic on XQuartz (section 3).
