# Plan: Run ChipIgnite `user_proj_example` Locally

> **Status:** Legacy baseline reference only. This plan reproduces the older Efabless `caravel_user_project` example. For the private tiny-AI project using the current ChipFoundry flow, follow [SPEC.md](SPEC.md); it supersedes this document where the two differ.

> **Outcome (2026-10-05): this plan was not executed as written.** The `user_proj_example` baseline was instead
> reproduced with the LibreLane 3.0.2 Docker flow ported from `../open-ai-silicon` (exercise 1), on the existing arm64
> Colima VM `osl`, not the x86-64 `chipignite` profile of Phase A. Against this plan's completion levels:
> 1. *RTL proof (`verify-io_ports-rtl`)*: **not run.** The Caravel management-core test needs the full template;
>    instead the macro's own self-checking unit testbench passes (28 checks, `make simulate`).
> 2. *Complete RTL proof (official Cocotb list)*: **not run.**
> 3. *Physical-design proof*: **partly.** The `user_proj_example` macro is hardened clean on a 200 x 200 um die (DRC,
>    LVS, XOR, antenna 0; non-negative slack at all corners; `designs/user_proj_example/output/`), and the unit
>    testbench passes on the synthesised and routed netlists. `user_project_wrapper`, Caravel gate-level tests and the
>    precheck were **not run**.
>
> Evidence and commands: `README.md`, `designs/user_proj_example/NOTES.md`. The tiny-AI work that followed is tracked
> in `SPEC.md` (Status section).
>
> **Update 2026-10-06:** the "not run" items above were later covered by the project's own flow, not by this plan:
> `user_project_wrapper` is hardened clean, and the Caravel RTL, hybrid gate-level and full-chip gate-level simulations and the
> local precheck (14 of 14 PASS) ran for it (`docs/CARAVEL_SIM.md`, `docs/PRECHECK.md`). The repository now holds 25 hardened
> designs (`README.md`). This plan stays a legacy reference.

## 1. Goal and definition of done

Use the official Efabless `caravel_user_project` template to reproduce the bundled `user_proj_example` counter on the local machine.

There are three useful completion levels:

1. **Required — RTL proof:** the repository and pinned dependencies are installed, and `verify-io_ports-rtl` exits successfully. This is the smallest valid meaning of “run the example.”
2. **Recommended — complete RTL proof:** the official Cocotb RTL test list exits successfully.
3. **Optional — physical-design proof:** OpenLane hardens `user_proj_example`, then `user_project_wrapper`; gate-level tests pass and the expected GDS/LEF/DEF/netlist artifacts exist.

Do not treat cloning the repository, compiling firmware, or merely starting a container as success.

## 2. Scope and assumptions

- Source: <https://github.com/efabless/caravel_user_project>
- Documentation: <https://caravel-user-project.readthedocs.io/en/latest/>
- Design: `verilog/rtl/user_proj_example.v`, instantiated by `verilog/rtl/user_project_wrapper.v`
- Default process: `sky130A`, matching the current top-level Makefile
- Execution model: Efabless-provided Docker images; do not install the EDA stack directly on macOS
- Repository policy: use the versions pinned by the cloned repository. Do not independently upgrade Caravel, OpenLane, the PDK, management core, or Docker images.
- This plan reproduces the example. It does not modify the RTL and does not prepare a new shuttle submission.

The example is a counter connected to Caravel's GPIO, logic-analyzer, and Wishbone interfaces. It is RTL rather than an ordinary program, so “run” means simulate it or process it through the RTL-to-GDS flow.

## 3. Facts already observed on this machine

The preflight performed while writing this plan found:

- Apple-silicon (`arm64`) macOS 26.7.1
- about 110 GiB free on the host volume
- Git 2.54.0 and Python 3.9.6
- Apple GNU Make 3.81 at `/usr/bin/make`
- GNU Make 4.4.1 available as `gmake`
- Docker CLI 28.5.1 installed
- Colima 0.10.3 installed, but no working Docker daemon
- Docker currently points at a stopped/missing default Colima socket
- an existing `osl` Colima profile is marked `Broken`; it is out of scope and must not be deleted or repaired by this task
- macOS BSD `realpath` is installed, but it does not support `--relative-to`
- GNU `coreutils`/`grealpath` is not currently installed

The last two items matter because the upstream Makefiles invoke GNU `realpath --relative-to`. Also, the official `efabless/dv:latest` and `efabless/dv:cocotb` verification images are `linux/amd64`, so the plan uses an isolated x86-64 Colima profile.

## 4. Rules for simple coding agents

Every agent must follow these rules:

1. Work on only one phase at a time and run phases in order. Do not parallelize setup, simulation, and hardening.
2. Run every `gmake` command from the cloned project root. In particular, do **not** `cd openlane` before hardening.
3. Stop on the first nonzero exit code from a setup, build, or test command. Do not mask those failures with `|| true`.
4. Record the exact command, exit code, and last relevant error in the handoff.
5. Do not edit RTL, Makefiles, OpenLane configuration, include lists, or pinned version variables just to make a failure disappear.
6. Do not run `gmake setup` repeatedly without diagnosis. That target removes and reinstalls some dependency directories.
7. Do not delete or prune Docker images, Colima profiles, or project directories automatically. Disk cleanup needs explicit user approval.
8. Ignore unrelated warnings only when the command exits zero and the acceptance checks pass.
9. Save logs under `open-ai-chip/run-logs/`, outside the cloned upstream repository, so `git status` remains meaningful.
10. At every handoff, state one of: `PASS`, `FAIL`, or `NOT RUN`. Never infer a pass from partial output.

## 5. Execution sequence

### Phase A — Prepare a compatible container runtime

**Owner:** host/bootstrap agent

**Purpose:** make an amd64 Linux Docker daemon available without touching the broken `osl` profile.

1. Confirm that at least 60 GiB of disk and 12 GiB of assignable RAM are available. If not, stop and report the resource shortage.
2. Create a dedicated x86-64 Colima profile:

```sh
colima start chipignite \
  --arch x86_64 \
  --vm-type qemu \
  --cpus 6 \
  --memory 12 \
  --disk 60
```

3. Confirm that Colima activated the expected Docker context:

```sh
docker context show
docker info --format 'server={{.ServerVersion}} arch={{.Architecture}} cpus={{.NCPU}} memory={{.MemTotal}}'
```

Expected context: `colima-chipignite`. Expected server architecture: `x86_64` or `amd64`.

4. Prove container execution:

```sh
docker run --rm hello-world
```

**Acceptance gate A**

- `colima status chipignite` reports running.
- `docker info` reaches a server and reports amd64/x86-64.
- `docker run --rm hello-world` exits 0.

If profile creation fails, preserve the error and stop. Do not alter or delete the existing `osl` profile.

### Phase B — Install the missing GNU host utility

**Owner:** host/bootstrap agent

**Dependency:** Phase A passed.

1. Install GNU coreutils:

```sh
brew install coreutils
```

2. Put the GNU utilities ahead of macOS BSD utilities for this shell:

```sh
export PATH="/opt/homebrew/opt/coreutils/libexec/gnubin:$PATH"
```

3. Verify the exact feature required by the upstream Makefiles:

```sh
realpath --relative-to=. .
gmake --version | head -n 1
python3 --version
git --version
```

**Acceptance gate B**

- `realpath --relative-to=. .` exits 0 and prints `.`.
- `gmake` is GNU Make 4.x.
- Python is 3.8 or newer.

The PATH export must be repeated in every later agent shell.

### Phase C — Clone and fingerprint the upstream project

**Owner:** repository agent

**Dependencies:** Phases A and B passed.

1. Start at the workspace root:

```sh
cd <repo>
mkdir -p run-logs
test ! -e caravel_user_project
git clone --depth 1 https://github.com/efabless/caravel_user_project.git caravel_user_project
cd caravel_user_project
```

If `caravel_user_project` already exists, stop and inspect it. Do not overwrite it or clone into it.

2. Record the upstream version and initial state:

```sh
git rev-parse HEAD | tee ../run-logs/upstream-commit.txt
git status --short | tee ../run-logs/initial-git-status.txt
```

The initial status file must be empty.

3. Confirm that the expected example exists:

```sh
test -f Makefile
test -f verilog/rtl/user_proj_example.v
test -f verilog/rtl/user_project_wrapper.v
test -f openlane/user_proj_example/config.json
grep -n 'user_proj_example mprj' verilog/rtl/user_project_wrapper.v
grep -n '"DESIGN_NAME": "user_proj_example"' openlane/user_proj_example/config.json
```

4. Capture the versions selected by the repository:

```sh
grep -nE 'PDK\?=|OPENLANE_TAG|MPW_TAG|OPEN_PDKS_COMMIT' Makefile \
  | tee ../run-logs/pinned-versions.txt
```

**Acceptance gate C**

- Clone exits 0.
- Initial `git status --short` is empty.
- All four required files exist.
- The wrapper instantiates `user_proj_example`.
- The OpenLane config names `user_proj_example`.
- The commit hash and pinned-version report are saved.

### Phase D — Export a repeatable environment

**Owner:** setup agent

**Dependency:** Phase C passed.

From the project root, run this block at the start of every new shell used in Phases D–I:

```sh
cd <repo>/caravel_user_project

export PATH="/opt/homebrew/opt/coreutils/libexec/gnubin:$PATH"
export CHIPIGNITE_PROJECT="$PWD"
export PDK="sky130A"
export PDK_ROOT="$CHIPIGNITE_PROJECT/dependencies/pdks"
export OPENLANE_ROOT="$CHIPIGNITE_PROJECT/dependencies/openlane_src"
export PRECHECK_ROOT="$CHIPIGNITE_PROJECT/dependencies/mpw_precheck"
export CARAVEL_ROOT="$CHIPIGNITE_PROJECT/caravel"
export MCW_ROOT="$CHIPIGNITE_PROJECT/mgmt_core_wrapper"
export THREADS="6"
```

Then verify:

```sh
printf '%s\n' "$CHIPIGNITE_PROJECT" "$PDK_ROOT" "$OPENLANE_ROOT" "$PRECHECK_ROOT"
docker context show
realpath --relative-to=. .
```

Do not change `HOME`. OpenLane/IPM may use its normal `~/.ipm` cache.

**Acceptance gate D**

- All printed paths are absolute and under the cloned project except the normal user cache.
- Docker context is `colima-chipignite`.
- GNU `realpath` is active.

### Phase E — Install the pinned project dependencies

**Owner:** setup agent

**Dependency:** Phase D passed.

1. Create the log directory if needed:

```sh
mkdir -p ../run-logs
```

2. Run the official aggregate setup once, with pipe failure propagation:

```sh
set -o pipefail
gmake setup 2>&1 | tee ../run-logs/setup.log
```

This target installs Caravel Lite, the management core, OpenLane, the PDK, timing scripts, Cocotb support, and local precheck dependencies. Large downloads and long quiet periods are normal; a nonzero exit is not.

3. Verify the installation:

```sh
test -d "$CARAVEL_ROOT"
test -d "$MCW_ROOT"
test -d "$OPENLANE_ROOT"
test -d "$PDK_ROOT/$PDK"
test -x venv/bin/volare
test -x venv-cocotb/bin/caravel_cocotb
test -e openlane/Makefile
test -e openlane/user_project_wrapper/pin_order.cfg
docker image ls --format '{{.Repository}}:{{.Tag}}' \
  | grep -E 'efabless/(openlane|dv|mpw_precheck)'
```

4. Capture the post-setup state:

```sh
git status --short | tee ../run-logs/post-setup-git-status.txt
```

Generated and ignored files are acceptable. Tracked-file modifications are not; if any appear, stop and report them.

**Acceptance gate E**

- `gmake setup` exits 0.
- Every directory, executable, and symlink check exits 0.
- Required Efabless images are present.
- No tracked source or configuration file changed.

### Phase F — Run the minimal RTL smoke test

**Owner:** RTL verification agent

**Dependency:** Phase E passed.

1. Confirm the Docker daemon and environment again.
2. Run the GPIO test from the project root:

```sh
set -o pipefail
gmake verify-io_ports-rtl 2>&1 | tee ../run-logs/verify-io_ports-rtl.log
```

This test compiles management firmware, boots the Caravel management core in simulation, configures user GPIO, and checks that the counter appears on the expected output pads.

3. Scan for explicit failures without using the scan as a substitute for the command exit code:

```sh
if rg -ni '(^|[^a-z])(error|fatal|failed|timeout)([^a-z]|$)' \
  ../run-logs/verify-io_ports-rtl.log; then
  echo 'Review the matches above before accepting the test.'
else
  echo 'No explicit failure markers found.'
fi
```

**Acceptance gate F — minimum project success**

- `gmake verify-io_ports-rtl` exits 0.
- The log contains the test's success/end indication.
- The log contains no timeout, fatal compile error, or simulator crash.

Once this gate passes, `user_proj_example` has been locally run at RTL level.

### Phase G — Run the complete current RTL test list

**Owner:** RTL verification agent

**Dependency:** Phase F passed.

Run the repository's current Cocotb test list:

```sh
set -o pipefail
gmake cocotb-verify-all-rtl 2>&1 | tee ../run-logs/cocotb-all-rtl.log
```

The example exercises GPIO, logic-analyzer behavior, and Wishbone access. If the aggregate test fails, rerun only the failing named test with:

```sh
gmake cocotb-verify-TEST_NAME-rtl
```

Replace `TEST_NAME` with the exact failing test name printed by the aggregate runner. Do not guess or rename tests.

**Acceptance gate G**

- Aggregate command exits 0.
- The Cocotb summary reports zero failed tests.
- Failure reruns, if any, are diagnostic only; the aggregate must eventually pass to mark this phase `PASS`.

### Phase H — Harden the example macro and wrapper

**Owner:** physical-design agent

**Dependency:** Phase G passed.

**Required only for completion level 3.**

1. Harden the child macro first:

```sh
set -o pipefail
gmake user_proj_example 2>&1 | tee ../run-logs/harden-user_proj_example.log
```

2. Verify nonempty child-macro deliverables:

```sh
test -s gds/user_proj_example.gds
test -s lef/user_proj_example.lef
test -s def/user_proj_example.def
test -s verilog/gl/user_proj_example.v
test -d signoff/user_proj_example
```

3. Review the final OpenLane summary CSV copied into `signoff/user_proj_example/`. The flow must not report unresolved DRC, LVS, routing, or timing failures.

4. Only after the child macro passes, harden the wrapper:

```sh
set -o pipefail
gmake user_project_wrapper 2>&1 | tee ../run-logs/harden-user_project_wrapper.log
```

5. Verify nonempty wrapper deliverables:

```sh
test -s gds/user_project_wrapper.gds
test -s lef/user_project_wrapper.lef
test -s def/user_project_wrapper.def
test -s verilog/gl/user_project_wrapper.v
test -d signoff/user_project_wrapper
```

**Acceptance gate H**

- Both hardening commands exit 0, in the stated order.
- All checked deliverables exist and are nonempty.
- Both signoff directories contain final reports.
- The final reports do not contain unresolved flow errors or violations that the flow treats as fatal.

Never harden the wrapper first: it consumes the child macro's LEF, GDS, and gate-level model.

### Phase I — Gate-level verification and optional signoff

**Owner:** signoff agent

**Dependency:** Phase H passed.

**Required only for completion level 3.**

1. Run the same GPIO behavior against the generated gate-level netlist:

```sh
set -o pipefail
gmake verify-io_ports-gl 2>&1 | tee ../run-logs/verify-io_ports-gl.log
```

2. Run the complete gate-level Cocotb list:

```sh
set -o pipefail
gmake cocotb-verify-all-gl 2>&1 | tee ../run-logs/cocotb-all-gl.log
```

3. If timing-annotated simulation is required, run it only after ordinary GL passes:

```sh
set -o pipefail
gmake verify-io_ports-gl-sdf 2>&1 | tee ../run-logs/verify-io_ports-gl-sdf.log
```

4. For a shuttle-style local validation, run these sequentially:

```sh
gmake lvs-user_project_wrapper
gmake drc-user_proj_example
gmake drc-user_project_wrapper
gmake xor-wrapper
gmake run-precheck
```

The repository currently supplies a standalone LVS configuration for `user_project_wrapper`, not for `user_proj_example`. Use the child macro's OpenLane LVS/signoff report; do not invent a new standalone LVS configuration just to extend this reproduction.

5. Run full-chip timing only if submission-level timing evidence is needed:

```sh
gmake extract-parasitics
gmake create-spef-mapping
gmake caravel-sta
```

**Acceptance gate I**

- RTL and ordinary GL behavior match for the tested interfaces.
- All requested simulation commands exit 0.
- Any requested LVS, DRC, XOR, timing, and precheck stages exit 0.
- Precheck logs contain no failed required check.

SDF, full-chip timing, and precheck are not required merely to demonstrate the bundled counter locally, but they are required evidence for a serious physical-design reproduction.

## 6. Failure routing

Use the first matching row. Apply one controlled correction, then rerun only the failed phase.

| Symptom | Likely cause | Agent action |
|---|---|---|
| `Cannot connect to the Docker daemon` | Colima is stopped or the wrong context is active | Run `colima status chipignite`, then select `colima-chipignite`; do not touch `osl`. |
| `no matching manifest` or `exec format error` | An arm64 Docker server is trying to run amd64-only DV images without emulation | Stop and recreate/use the dedicated x86-64 `chipignite` profile; do not retag images. |
| `realpath: illegal option -- -` | BSD `realpath` is ahead of GNU coreutils | Restore `/opt/homebrew/opt/coreutils/libexec/gnubin` at the front of `PATH`. |
| `gmake: command not found` | Homebrew GNU Make is unavailable | Install `make` with Homebrew, then use `gmake`; do not rewrite upstream Makefiles for BSD make. |
| Failure during Git/PIP/Docker download | Network, proxy, registry, or certificate issue | Record the failing URL and tool. Retry once after connectivity is restored; do not change pinned versions. |
| `No space left on device` | Colima disk or host disk is full | Record `df -h` and `docker system df`; request approval before pruning or resizing. |
| Permission errors on bind-mounted outputs | UID/mount behavior differs under the VM | Record `id`, `docker info`, and ownership of the failing path. Do not recursively `chown` the workspace without approval. |
| Missing PDK directory | Setup did not finish or the wrong `PDK_ROOT` is exported | Re-export Phase D exactly, inspect `setup.log`, and rerun the specific failed setup target rather than blindly rerunning all setup. |
| Cocotb path/config error | `setup-cocotb-env` did not complete | Run `gmake setup-cocotb-env`, inspect `verilog/dv/cocotb/design_info.yaml`, and ensure it names the current absolute paths. Do not hand-edit it unless the generator is demonstrably wrong. |
| RTL compile cannot find user files | Include lists or generated setup are incomplete | Compare `verilog/includes/includes.rtl.caravel_user_project` with the checked-out version; restore tracked changes instead of adding ad hoc includes. |
| OpenLane fails after a version change | Tool/PDK/Caravel mismatch | Return to the versions captured in `pinned-versions.txt`; never mix a newer OpenLane with the template's older PDK/Caravel pins. |
| Hardening command exits 0 but artifacts are missing | Save/copy stage did not complete correctly | Treat the phase as failed and inspect the end of the hardening log and `openlane/*/runs/*`. |

## 7. Agent handoff template

Every phase owner returns this compact report:

```text
Phase: <A-I>
Status: PASS | FAIL | NOT RUN
Project commit: <hash, once cloned>
Docker context/server arch: <context> / <arch>
Commands run: <exact commands>
Exit code: <code for each decisive command>
Evidence: <log and artifact paths>
Tracked changes: <none, or exact git status>
Blocker: <none, or first actionable error>
Next phase allowed: <yes/no and phase letter>
```

## 8. Final evidence bundle

For the minimum RTL result, retain:

- `run-logs/upstream-commit.txt`
- `run-logs/pinned-versions.txt`
- `run-logs/setup.log`
- `run-logs/verify-io_ports-rtl.log`
- the final `git status --short`

For the physical-design result, also retain:

- `run-logs/cocotb-all-rtl.log`
- both hardening logs
- `run-logs/verify-io_ports-gl.log`
- `run-logs/cocotb-all-gl.log`
- `signoff/user_proj_example/`
- `signoff/user_project_wrapper/`
- precheck, LVS, DRC, XOR, and timing reports that were requested
- hashes and sizes of `gds/user_proj_example.gds` and `gds/user_project_wrapper.gds`

A final report should clearly say which of the three completion levels passed. It must not call the physical flow complete if only RTL simulation passed.

## 9. Source notes

- The official project documentation identifies the design as a counter using GPIO, logic-analyzer probes, and Wishbone; it documents `make setup`, RTL/GL simulation, the child-then-wrapper hardening order, timing, and precheck: <https://caravel-user-project.readthedocs.io/en/latest/>.
- The current top-level Makefile defines `sky130A` as the default, pins Caravel/OpenLane/PDK versions, and exposes the classic and Cocotb verification targets: <https://github.com/efabless/caravel_user_project/blob/main/Makefile>.
- The macro's current OpenLane configuration names `user_proj_example` and lists its RTL inputs: <https://github.com/efabless/caravel_user_project/blob/main/openlane/user_proj_example/config.json>.
- The official DV image tags used by this repository are currently published for `linux/amd64`: <https://hub.docker.com/r/efabless/dv/tags>.
