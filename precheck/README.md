# precheck/

Local ChipFoundry `cf-precheck 1.3.7` run for the Caravel user project (user_project_wrapper + tiny_ai_core).
Everything runs on this Mac in the aarch64 Colima VM. No account, no `cf login/init/push/submit`, no uploads.

    precheck/run_precheck.sh            # stage + run all 14 checks, one container per check, 45 min cap each
    precheck/run_precheck.sh lvs oeb    # only some checks
    PDK_ROOT=$HOME/.volare precheck/run_precheck.sh   # use the LibreLane-pinned PDK 8afc8346 instead

Files: `stage_project.sh` (template-shaped copy of our files into `build/precheck/project/`, golden Caravel CC2509 into
`build/precheck/caravel_golden/`), `fetch_pdk.sh` (ChipFoundry's template-pinned open_pdks 3e0e31dc from the public ciel
mirror), `docker/Dockerfile.nixklayout` (image used: magic/netgen from the LibreLane 3.0.2 image, klayout 0.30.7, cvc_rv
built from source, cf-precheck 1.3.7), `docker/Dockerfile` (variant that builds klayout 0.29.12 from source),
`results/` (summary.tsv, evidence.txt). Full reports stay in git-ignored `build/precheck/results_*`.
Experiment mode: `GPIO_MODE=GPIO_MODE_MGMT_STD_INPUT_NOPULL PRECHECK_PROJECT=<dir> run_precheck.sh gpio_defines oeb`
rewrites `GPIO_MODE_INVALID in the STAGED user_defines.v only; it is not release evidence.
Final result: 14 of 14 checks PASS in 61 s (`results/summary.tsv`, run `build/precheck/results_20261006_090206/`), in our own
container, not ChipFoundry's image. `make precheck` runs it. See docs/PRECHECK.md.
