# open-ai-chip Makefile -- RTL to GDSII with LibreLane 3.0.2 in Docker on sky130A, one design at a time.
# Designs: every designs/<name>/config.json. user_proj_example is the ChipIgnite template's counter; vision_all_lit,
# vision_block and text_sentiment are the tiny AI engines (model in model/tiny_ai/). Flow and checks ported from
# ../open-ai-silicon (exercise 1).
#
#   make flow-all [DESIGN=<name>]   simulate -> gds -> check -> gl (synthesised) -> gl-final (routed) -> collect
#   make tiny                       flow-all for the three tiny AI engines, then a table
#   make help                       every target

SHELL        := /bin/bash
DESIGN       ?= user_proj_example
DESIGNS      := $(patsubst designs/%/config.json,%,$(wildcard designs/*/config.json))
TINY         := vision_all_lit vision_block text_sentiment
DDIR         := designs/$(DESIGN)
PDK_ROOT     ?= $(HOME)/.volare
DOCKER_IMAGE := ghcr.io/librelane/librelane:3.0.2
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
SIM_RTL  := $(shell python3 scripts/flow/design_info.py $(DESIGN) files)
SIM_TB   := $(DDIR)/tb/$(DESIGN)_tb.v
# generated vectors (tiny AI engines): passed as +VEC=; their testbench body is shared/tb/stream_tb.vh
SIM_VEC  := $(wildcard $(DDIR)/tb/vectors.hex)
SIM_PLUS := $(if $(SIM_VEC),+VEC=$(abspath $(SIM_VEC)))
GL_DESC  := $(if $(SIM_VEC),every case of tb/vectors.hex,committed tb)

.PHONY: help doctor test simulate gds flow check gl gl-final collect view flow-all tiny generate check-generated model-check clean
.DEFAULT_GOAL := help

help:
	@echo "open-ai-chip: sky130A, LibreLane 3.0.2 in Docker. DESIGN=$(DESIGN) (designs: $(DESIGNS))"
	@echo ""
	@echo "  generate         re-fit the tiny AI model, regenerate ROMs and vectors (model/tiny_ai/)"
	@echo "  check-generated  fail if regenerating changes any generated file"
	@echo "  model-check      the model matches every truth-table label (16, 512, 256 cases)"
	@echo "  doctor     host tools, Docker daemon, LibreLane image, PDK"
	@echo "  test       fast repository checks (structure, configs, RTL lint), no Docker"
	@echo "  simulate   RTL simulation, self-checking testbench (iverilog)"
	@echo "  gds        RTL to GDSII under PROFILE (default tight); reuses the newest complete, current run"
	@echo "  check      signoff: DRC, LVS, XOR, antenna, slack at all corners, no logic lost"
	@echo "  gl         gate-level simulation of the synthesised netlist (NETLIST=final: routed netlist)"
	@echo "  gl-final   gate-level simulation of the routed netlist"
	@echo "  collect    GDS, LEF, netlists, reports, layout.png -> build/results/$(DESIGN)/; evidence -> $(DDIR)/output/"
	@echo "  view       summary of collected results (ARGS=--no-gui for text only)"
	@echo "  flow-all   the one command: simulate, gds, check, gl, gl-final, collect; prints a summary"
	@echo "  tiny       flow-all for $(TINY), then a table"
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
	iverilog -g2012 -Wall -Wno-timescale -I shared/tb -o $@ $(SIM_RTL) $(SIM_TB)

simulate: $(SIM_DIR)/tb.vvp
	cd $(SIM_DIR) && set -o pipefail && vvp -n tb.vvp $(SIM_PLUS) | tee sim.log
	@grep -Eq '^PASS' $(SIM_DIR)/sim.log

# ---- RTL to GDSII ----
# flow: one LibreLane run, no reuse (scripts/flow/run_capped.sh calls it with the container named)
flow:
	$(call run_librelane,$(DDIR),)

gds:
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
gl:
ifeq ($(NETLIST),final)
	@echo "=== gl: post-route netlist ==="
else
	@echo "=== gl: synthesis-only run -> build/gl/$(DESIGN)/ ==="
	@bash scripts/flow/gl_sim.sh prepare $(DESIGN)
	$(call run_librelane,build/gl/$(DESIGN),-T Yosys.Synthesis --run-tag gl --condensed --hide-progress-bar)
	@echo "synthesis__check_error__count = $$(bash scripts/flow/gl_sim.sh checks $(DESIGN))" | tee build/gl/$(DESIGN)/synth_checks.txt
endif
	@bash scripts/flow/gl_sim.sh run $(DESIGN) --source $(NETLIST) --name $(DESIGN) --tb $(SIM_TB) --top $(DESIGN)_tb \
	  -I shared/tb $(if $(SIM_PLUS),--plus $(SIM_PLUS)) --desc "$(GL_DESC)" --timeout $(GL_TIMEOUT)

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

# ---- tiny AI model and generated files ----
generate:
	cd model/tiny_ai && python3 train.py && python3 gen_rom.py

model-check:
	cd model/tiny_ai && python3 golden.py --check

check-generated:
	@bash scripts/check_generated.sh

clean:
	rm -rf $(DDIR)/runs build
