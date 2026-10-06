# open-ai-chip Makefile -- RTL to GDSII with LibreLane 3.0.2 in Docker on sky130A, one design at a time.
# Designs: every designs/<name>/config.json. user_proj_example is the ChipIgnite template's counter; vision_all_lit,
# vision_block and text_sentiment are the tiny AI engines (model in model/tiny_ai/). Flow and checks ported from
# ../open-ai-silicon (exercise 1).
#
#   make flow-all [DESIGN=<name>]   simulate -> gds -> check -> gl (synthesised) -> gl-final (routed) -> collect
#   make tiny                       flow-all for the three tiny AI engines, then a table
#   make all-designs                flow-all for every design in a fixed order (hours; not part of any check)
#   make table                      regenerate the results tables of docs/RESULTS.md from designs/*/output (no Docker)
#   make views [DESIGN=<macro>]     export a hardened macro's views (gds lef nl pnl spef lib) to build/macros/<macro>/
#   make wrapper                    views of every macro of user_project_wrapper, then flow-all DESIGN=user_project_wrapper
#   make help                       every target
# Docs: README.md, docs/RUN_ON_MAC.md

SHELL        := /bin/bash
DESIGN       ?= user_proj_example
DESIGNS      := $(patsubst designs/%/config.json,%,$(wildcard designs/*/config.json))
TINY         := vision_all_lit vision_block text_sentiment
# every design, in the order make all-designs hardens them (macros before the wrapper that instantiates them)
ALL_DESIGNS  := user_proj_example vision_all_lit vision_block text_sentiment tiny_ai_core user_project_wrapper \
                audio_pitch audio_onset image_text_match prec_bin prec_tern prec_int4 prec_int8 prec_fp8 prec_fp16 prec_bf16 soc_image_text_match \
                user_project_wrapper_soc_itm kv_attn_n4 kv_attn_n8 kv_attn_n16 kv_attn_n8_int4 kv_attn_n8_ring \
                soc_kv_attn_n8 user_project_wrapper_soc_kv
# model directories (model/<dir>/); model/examples/ holds standalone teaching scripts, not generated files
MODELS       := tiny_ai audio_pitch audio_onset image_text_match precision_hw kv_attention
DDIR         := designs/$(DESIGN)
PDK_ROOT     ?= $(HOME)/.volare
# the LibreLane image pin lives in versions.lock (LIBRELANE_IMAGE)
DOCKER_IMAGE := $(strip $(shell sed -n 's/^LIBRELANE_IMAGE=//p' versions.lock))
PROFILE      ?= tight
CPUSET       ?= 0-1
NETLIST      ?= synth
GL_TIMEOUT   ?= 900
SIM_DIR      := build/sim/$(DESIGN)
TMP          := build/flow/$(DESIGN)

# The Colima VM "osl" when DOCKER_HOST is not set and its socket exists; otherwise Docker's default.
ifeq ($(origin DOCKER_HOST),undefined)
  ifneq ($(wildcard $(HOME)/.colima/osl/docker.sock),)
    export DOCKER_HOST := unix://$(HOME)/.colima/osl/docker.sock
  endif
endif
export PDK_ROOT DOCKER_IMAGE CPUSET

# Resource profiles: tight = 2 CPUs / 8 GB, actions = 4 CPUs / 16 GB (container caps and tool thread counts).
ifeq ($(PROFILE),tight)
  PROF_CPUS := 2
  PROF_MEM  := 8g
else ifeq ($(PROFILE),actions)
  PROF_CPUS := 4
  PROF_MEM  := 16g
else
  $(error PROFILE must be 'tight' or 'actions' (got '$(PROFILE)'))
endif
PROF_DOCKER_ARGS := --cpus=$(PROF_CPUS) --memory=$(PROF_MEM) --memory-swap=$(PROF_MEM) --cpuset-cpus=$(CPUSET)
PROF_LL_ARGS     := -c DRT_THREADS=$(PROF_CPUS) -c KLAYOUT_DRC_THREADS=$(PROF_CPUS) -c KLAYOUT_XOR_THREADS=$(PROF_CPUS)
DOCKER_EXTRA ?=

# $(call run_librelane,<design dir>,<extra librelane args>)
define run_librelane
	docker run --rm -i $(PROF_DOCKER_ARGS) $(DOCKER_EXTRA) \
		-v $(HOME):$(HOME) -v $(CURDIR):$(CURDIR) \
		-e PDK_ROOT=$(PDK_ROOT) -e PDK=sky130A -w $(CURDIR) \
		$(DOCKER_IMAGE) \
		python3 -m librelane --manual-pdk --pdk-root $(PDK_ROOT) \
			--design-dir $(CURDIR)/$(1) $(PROF_LL_ARGS) $(2) $(CURDIR)/$(1)/config.json
endef

ifeq ($(wildcard $(DDIR)/config.json),)
  $(error unknown DESIGN '$(DESIGN)'; designs: $(DESIGNS))
endif
# MACROS: the designs named in the design's config.json MACROS (an elaborate-only wrapper). Their exported views live in
# build/macros/<macro>/ (make views); simulation compiles the macro RTL too, gate-level simulation adds the macro netlist.
MACROS    := $(shell python3 scripts/flow/find_reusable_run.py --macros $(DESIGN))
# duplicates removed, order kept (a wrapper's config may already list some macro files)
SIM_RTL  := $(shell { python3 scripts/flow/design_info.py $(DESIGN) files; $(foreach m,$(MACROS),python3 scripts/flow/design_info.py $(m) files;) } | awk '!s[$$0]++')
SIM_INCS := $(addprefix -I,$(shell python3 scripts/flow/design_info.py $(DESIGN) incs))
SIM_TB   := $(DDIR)/tb/$(DESIGN)_tb.v
# generated vectors (tiny AI engines): passed as +VEC=; their testbench body is shared/tb/stream_tb.vh. Override: SIM_VEC=<file>;
# default: the design's tb/vectors.hex, else the first macro's (the wrapper drives the macro with the macro's vectors)
SIM_VEC  ?= $(firstword $(wildcard $(DDIR)/tb/vectors.hex $(foreach m,$(MACROS),designs/$(m)/tb/vectors.hex)))
MACRO_VIEWS_DIRS := $(foreach m,$(MACROS),build/macros/$(m))
GL_NLX   := $(foreach m,$(MACROS),--netlist-extra build/macros/$(m)/nl/$(m).nl.v)
# macro-definition files of the RTL (e.g. the wrapper's defines.v: MPRJ_IO_PADS) are compiled first at gate level too
GL_PRE   := $(foreach f,$(filter %defines.v,$(SIM_RTL)),--pre $(f))
# `make views` takes the macro as DESIGN=<macro>; default tiny_ai_core
VIEWS_OF := $(if $(filter file,$(origin DESIGN)),tiny_ai_core,$(DESIGN))
SIM_PLUS := $(if $(SIM_VEC),+VEC=$(abspath $(SIM_VEC)))
GL_DESC  := $(if $(SIM_VEC),every case of tb/vectors.hex,committed tb)

.PHONY: help doctor test test-full views macro-views wrapper simulate gds flow check gl gl-final collect view flow-all tiny all-designs designs table results generate check-generated model-check clean soc-sim adapter-test caravel-rtl caravel-gl precheck caravel-fullgl caravel-sdf-wrapper soc-kv code-map
.DEFAULT_GOAL := help

help:
	@echo "open-ai-chip: sky130A, LibreLane 3.0.2 in Docker. DESIGN=$(DESIGN) (designs: $(DESIGNS))"
	@echo ""
	@echo "  generate         re-fit and regenerate ROMs, vectors, weights.json of every model dir: $(MODELS)"
	@echo "  check-generated  fail if regenerating changes any generated file of any model dir"
	@echo "  model-check      golden.py --check of tiny_ai, image_text_match, precision_hw, kv_attention (audio_* have no --check; the testbench is the check)"
	@echo "  code-map   regenerate docs/CODE_MAP.md (file -> purpose -> parent doc) from the Docs: header lines"
	@echo "  doctor     host tools, Docker daemon, LibreLane image, PDK"
	@echo "  test       fast repository checks (structure, configs, RTL lint), no Docker"
	@echo "  test-full  heavy local checks, no physical flow: simulate/check/gl-final of all designs, soc, caravel (tests/test_full.sh; FLAGS=--precheck --synth-gl --fullgl --sdf --quick)"
	@echo "  simulate   RTL simulation, self-checking testbench (iverilog)"
	@echo "  views      export the hardened macro's views to build/macros/<macro>/ (DESIGN=<macro>, default tiny_ai_core)"
	@echo "  wrapper    views, then flow-all DESIGN=user_project_wrapper"
	@echo "  gds        RTL to GDSII under PROFILE (default tight); reuses the newest complete, current run"
	@echo "  check      signoff: DRC, LVS, XOR, antenna, slack at all corners, no logic lost"
	@echo "  gl         gate-level simulation of the synthesised netlist (NETLIST=final: routed netlist)"
	@echo "  gl-final   gate-level simulation of the routed netlist"
	@echo "  collect    GDS, LEF, netlists, reports, layout.png -> build/results/$(DESIGN)/; evidence -> $(DDIR)/output/"
	@echo "  view       summary of collected results (ARGS=--no-gui for text only)"
	@echo "  flow-all   the one command: simulate, gds, check, gl, gl-final, collect; prints a summary"
	@echo "  tiny       flow-all for $(TINY), then a table"
	@echo "  all-designs  flow-all for all $(words $(ALL_DESIGNS)) designs in order (alias: designs), then the results table"
	@echo "  soc-sim    PicoRV32 SoC sim: RISC-V firmware vs user_project_wrapper RTL, cycle table (make -C firmware sim, ~25 s)"
	@echo "  soc-kv     KV-cache attention firmware on the PicoRV32 SoC (make -C firmware/kv sim, ~14 s)"
	@echo "  adapter-test  Wishbone-to-stream adapter with all 14 stream engines (incl. kv_attn_n8) (tests/adapter/run.sh, ~9 s)"
	@echo "  caravel-rtl   full-Caravel RTL sim, VexRiscv firmware (needs build/caravel downloads, ~53 s; docs/CARAVEL_SIM.md)"
	@echo "  caravel-gl    hybrid gate-level Caravel sim (needs build/caravel, ~58 s)"
	@echo "  precheck   local ChipFoundry cf-precheck, all 14 checks, own container (precheck/run_precheck.sh, ~1 min; docs/PRECHECK.md)"
	@echo "  caravel-fullgl  full-chip gate-level Caravel sim, firmware, iverilog (needs build/caravel; ~14 min; docs/CARAVEL_SIM.md)"
	@echo "  caravel-sdf-wrapper  wrapper+macro gate-level + SDF, CVC in an amd64 container (needs build/caravel + CVC image; CORNER=..., ~5 s)"
	@echo "  table      regenerate the results tables in docs/RESULTS.md (scripts/docs/tables.py)"
	@echo "  results    build build/site/index.html (tables, layout gallery, agent results) and open it (GitHub shows docs/RESULTS.md directly)"
	@echo "  clean      remove $(DDIR)/runs/ and build/"
	@echo ""
	@echo "  Options: PROFILE=tight|actions  CPUSET=0-1  DOCKER_HOST=unix://...  PDK_ROOT=$(PDK_ROOT)"

doctor:
	@bash scripts/doctor.sh

test:
	@bash tests/run_tests.sh

# ---- RTL simulation ----
# -Wno-timescale: the upstream RTL has no `timescale (kept byte-identical) and no delays.
$(SIM_DIR)/tb.vvp: $(SIM_RTL) $(SIM_TB) $(wildcard shared/tb/*.vh)
	@mkdir -p $(SIM_DIR)
	iverilog -g2012 -Wall -Wno-timescale -I shared/tb $(SIM_INCS) -o $@ $(SIM_RTL) $(SIM_TB)

test-full:
	@bash tests/test_full.sh $(FLAGS)

simulate: $(SIM_DIR)/tb.vvp
	cd $(SIM_DIR) && set -o pipefail && vvp -n tb.vvp $(SIM_PLUS) | tee sim.log
	@grep -Eq '^PASS' $(SIM_DIR)/sim.log

# ---- RTL to GDSII ----
# flow: one LibreLane run, no reuse (scripts/flow/run_capped.sh calls it with the container named)
flow:
	$(call run_librelane,$(DDIR),)

# ---- macro views (build/macros/<macro>/) ----
# views: copy final/{gds,lef,nl,pnl,spef,lib} of the macro's CURRENT run (find_reusable_run.py); fails with a hint when there is none
views:
	@python3 scripts/flow/find_reusable_run.py --export-views $(VIEWS_OF)

# macro-views: every macro of $(DESIGN), re-exported only when its current run changed (SOURCE.txt names the run + hashes)
macro-views:
	@for m in $(MACROS); do python3 scripts/flow/find_reusable_run.py --export-views --if-needed $$m || exit 1; done

wrapper:
	@for m in $$(python3 scripts/flow/find_reusable_run.py --macros user_project_wrapper); do $(MAKE) --no-print-directory views DESIGN=$$m || exit 1; done
	@$(MAKE) --no-print-directory flow-all DESIGN=user_project_wrapper

gds: $(if $(MACROS),macro-views)
	@mkdir -p $(DDIR)/output $(TMP)
	@if run=$$(python3 scripts/flow/find_reusable_run.py $(DESIGN) 2>$(TMP)/reuse.err); then \
	  echo "gds: REUSED $$run (complete, inputs unchanged since it started); no flow run"; \
	  python3 scripts/flow/find_reusable_run.py --resources $(DESIGN) $$run $(DDIR)/output/resources.json; \
	else \
	  echo "gds: no reusable run ($$(tail -1 $(TMP)/reuse.err)); running the flow under PROFILE=$(PROFILE)"; \
	  bash scripts/flow/run_capped.sh --design $(DESIGN) --profile $(PROFILE) --cpuset $(CPUSET) --out $(DDIR)/output/resources.json; \
	fi

# ---- signoff ----
check:
	@python3 scripts/flow/check_signoff.py $(DESIGN)

# ---- gate-level simulation ----
# NETLIST=synth: a synthesis-only LibreLane run on a copy of the config (build/gl/<design>/); NETLIST=final: the routed netlist.
gl: $(if $(MACROS),macro-views)
ifeq ($(NETLIST),final)
	@echo "=== gl: post-route netlist ==="
else
	@echo "=== gl: synthesis-only run -> build/gl/$(DESIGN)/ ==="
	@bash scripts/flow/gl_sim.sh prepare $(DESIGN)
	$(call run_librelane,build/gl/$(DESIGN),-T Yosys.Synthesis --run-tag gl --condensed --hide-progress-bar)
	@echo "synthesis__check_error__count = $$(bash scripts/flow/gl_sim.sh checks $(DESIGN))" | tee build/gl/$(DESIGN)/synth_checks.txt
endif
	@bash scripts/flow/gl_sim.sh run $(DESIGN) --source $(NETLIST) --name $(DESIGN) --tb $(SIM_TB) --top $(DESIGN)_tb \
	  -I shared/tb $(GL_PRE) $(GL_NLX) $(if $(SIM_PLUS),--plus $(SIM_PLUS)) --desc "$(GL_DESC)" --timeout $(GL_TIMEOUT)

gl-final:
	@$(MAKE) --no-print-directory gl NETLIST=final

# ---- results ----
collect:
	@bash scripts/flow/collect.sh --collect $(DESIGN) --profile $(PROFILE) --cpuset $(CPUSET)
	@mkdir -p $(DDIR)/output
	@cp build/results/$(DESIGN)/metrics.json $(DDIR)/output/
	@# committed log: home directory shown as ~ (no absolute paths in the repository)
	@sed 's#$(HOME)#~#g' build/results/$(DESIGN)/flow.log > $(DDIR)/output/flow.log
	@cp build/results/$(DESIGN)/$(DESIGN).lef $(DDIR)/output/ 2>/dev/null || true
	@cp build/results/$(DESIGN)/resources.json $(DDIR)/output/resources.json 2>/dev/null || true
	@# reports and the GDSII render (KLayout, 2400 px), committed as evidence
	@rm -rf $(DDIR)/output/reports; cp -R build/results/$(DESIGN)/reports $(DDIR)/output/reports
	@cp build/results/$(DESIGN)/layout.png $(DDIR)/output/layout.png

view:
	@bash scripts/flow/collect.sh $(ARGS) $(DESIGN)

# ---- the one command ----
flow-all:
	@mkdir -p $(TMP); rm -f $(TMP)/stages.txt; S=$$(date +%s); fail=0; \
	stage() { n=$$1; shift; echo "=== flow-all: $$n ==="; a=$$(date +%s); \
	  if "$$@" > $(TMP)/stage_$$n.log 2>&1; then r=PASS; else r=FAIL; fail=1; fi; \
	  b=$$(date +%s); echo "$$n $$r $$((b-a))" >> $(TMP)/stages.txt; echo "$$n: $$r in $$((b-a)) s (log: $(TMP)/stage_$$n.log)"; \
	  [ $$r = PASS ] || { tail -15 $(TMP)/stage_$$n.log; return 1; }; }; \
	stage simulate $(MAKE) --no-print-directory simulate && \
	stage gds      $(MAKE) --no-print-directory gds && \
	stage check    $(MAKE) --no-print-directory check && \
	stage gl_synth $(MAKE) --no-print-directory gl && \
	stage gl_final $(MAKE) --no-print-directory gl NETLIST=final && \
	stage collect  $(MAKE) --no-print-directory collect; \
	E=$$(date +%s); \
	python3 scripts/flow/summary.py $(DESIGN) $(TMP)/stages.txt $$((E-S)); \
	[ $$fail = 0 ]

# ---- the tiny AI engines, one after another ----
tiny:
	@fail=""; for d in $(TINY); do \
	  echo "##### $$d #####"; $(MAKE) --no-print-directory flow-all DESIGN=$$d || fail="$$fail $$d"; \
	done; \
	python3 scripts/flow/tiny_table.py $(TINY); \
	if [ -n "$$fail" ]; then echo "tiny: FAILED:$$fail"; exit 1; fi; echo "tiny: all passed"

# ---- model fitting and generated files (every model dir) ----
# generators: tiny_ai train.py + gen_rom.py; audio_pitch, audio_onset, image_text_match train.py + gen_rom.py;
# precision_hw gen.py (no fitting step). golden.py --check exists in tiny_ai, image_text_match, precision_hw;
# audio_pitch and audio_onset have none (their testbenches compare against golden.py instead).
generate:
	cd model/tiny_ai && python3 train.py && python3 gen_rom.py
	cd model/audio_pitch && python3 train.py && python3 gen_rom.py
	cd model/audio_onset && python3 train.py && python3 gen_rom.py
	cd model/image_text_match && python3 train.py && python3 gen_rom.py
	cd model/precision_hw && python3 gen.py
	cd model/kv_attention && python3 gen.py

model-check:
	cd model/tiny_ai && python3 golden.py --check
	cd model/image_text_match && python3 golden.py --check
	cd model/precision_hw && python3 golden.py --check
	cd model/kv_attention && python3 golden.py --check

# ---- every design, one after another; then the results table ----
all-designs:
	@fail=""; for d in $(ALL_DESIGNS); do \
	  echo "##### $$d #####"; $(MAKE) --no-print-directory flow-all DESIGN=$$d || fail="$$fail $$d"; \
	done; \
	python3 scripts/docs/tables.py; \
	if [ -n "$$fail" ]; then echo "all-designs: FAILED:$$fail"; exit 1; fi; echo "all-designs: all passed"

designs: all-designs

# ---- SoC-level simulation (no Docker) ----
soc-sim:
	$(MAKE) -C firmware sim

# KV-cache attention firmware (second program, soc_sim/kv), ~14 s
soc-kv:
	$(MAKE) -C firmware/kv sim

adapter-test:
	bash tests/adapter/run.sh

caravel-rtl caravel-gl:
	@[ -d build/caravel/caravel ] && [ -d build/caravel/mgmt_core_wrapper ] || { \
	  echo "$@: build/caravel/{caravel,mgmt_core_wrapper} missing: download them first (953 MB + 4.1 GB), see caravel_sim/README.md and caravel_sim/VERSIONS.txt"; exit 1; }
	bash caravel_sim/run_$(if $(filter caravel-rtl,$@),rtl,gl).sh

precheck:
	bash precheck/run_precheck.sh

caravel-fullgl:
	@[ -d build/caravel/caravel ] && [ -d build/caravel/mgmt_core_wrapper ] || { \
	  echo "$@: build/caravel/{caravel,mgmt_core_wrapper} missing: download them first (see caravel_sim/README.md)"; exit 1; }
	bash caravel_sim/run_fullgl.sh

caravel-sdf-wrapper:
	@[ -d build/caravel/caravel ] && [ -x build/caravel/cvc_src/build64/cvc64 ] || { \
	  echo "$@: build/caravel or the CVC binary build/caravel/cvc_src/build64/cvc64 missing: build it (docs/CARAVEL_SIM.md, 'Setup used')"; exit 1; }
	@docker image inspect openchip-cvc64-base >/dev/null 2>&1 || { \
	  echo "$@: docker image openchip-cvc64-base (amd64 CVC base) missing: build it (docs/CARAVEL_SIM.md, 'Setup used')"; exit 1; }
	bash caravel_sim/run_sdf_wrapper.sh

# code-map: docs/CODE_MAP.md from the 'Docs:' header of every code file (scripts/docs/code_map.py; checker: tests/check_traceability.py)
code-map:
	@python3 scripts/docs/code_map.py

table:
	@bash scripts/docs/make_thumbs.sh
	@python3 scripts/docs/tables.py

results:
	@python3 scripts/docs/results_site.py --open

check-generated:
	@bash scripts/check_generated.sh

clean:
	rm -rf $(DDIR)/runs build
