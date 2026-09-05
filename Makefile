# Credit Intelligence OS — developer entry points (CLAUDE.md §6)
# Every target listed in CLAUDE.md §6 exists here. Targets not yet implemented
# name the build task that will implement them.

SHELL       := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV          ?= uv
COMPOSE     ?= docker compose -f docker/compose.yaml
COMPOSE_SPARK ?= $(COMPOSE) -f docker/compose.spark.yaml
PHASE       ?=
SERVICES    := $(notdir $(wildcard services/*))

# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
.PHONY: help
help:  ## show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
	 | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: todo
todo:
	@echo "  not implemented yet — $(TASK)"; exit 0

# ---------------------------------------------------------------------------
# environment and stack
# ---------------------------------------------------------------------------
.PHONY: env
env:  ## create .env from the example and check the DGX Spark environment
	@if [ ! -f docker/.env ]; then \
	  if [ -f docker/.env.example ]; then cp docker/.env.example docker/.env; echo "created docker/.env"; \
	  else echo "docker/.env.example missing — T-003"; fi; \
	fi
	@if [ -x scripts/env_check.sh ]; then scripts/env_check.sh; else echo "  scripts/env_check.sh — T-002"; fi

.PHONY: up
up:  ## start core + observability + services (no AI)
	@$(MAKE) todo TASK=T-003

.PHONY: up-ai-local
up-ai-local:  ## start with vLLM/ollama on the GB10
	@$(MAKE) todo TASK=T-003

.PHONY: up-ai-remote
up-ai-remote:  ## start with a hosted provider behind the gateway
	@$(MAKE) todo TASK=T-003

.PHONY: down
down:  ## stop the stack
	@$(MAKE) todo TASK=T-003

.PHONY: logs
logs:  ## tail stack logs
	@$(MAKE) todo TASK=T-003

.PHONY: ps
ps:  ## show container health
	@$(MAKE) todo TASK=T-003

.PHONY: migrate
migrate:  ## alembic upgrade head for every service
	@$(MAKE) todo TASK=T-007

# ---------------------------------------------------------------------------
# contracts and data
# ---------------------------------------------------------------------------
.PHONY: codegen
codegen:  ## contracts/schemas -> pydantic + typescript types
	@$(MAKE) todo TASK=T-004

.PHONY: seed
seed:  ## generate and load the synthetic population, documents and scenarios
	@$(MAKE) todo TASK=T-020

.PHONY: reset
reset:  ## wipe and re-seed to the golden demo state
	@$(MAKE) todo TASK=T-082

.PHONY: warmup
warmup:  ## one call per LLM route so JIT/compile is done before a demo
	@$(MAKE) todo TASK=T-082

# ---------------------------------------------------------------------------
# quality
# ---------------------------------------------------------------------------
.PHONY: lint
lint:  ## ruff check
	@$(UV) run ruff check .
	@$(UV) run ruff format --check .

.PHONY: fmt
fmt:  ## ruff format + import fixes
	@$(UV) run ruff check --fix .
	@$(UV) run ruff format .

.PHONY: typecheck
typecheck:  ## mypy over libs, services, workflows, ai, ml, synthetic
	@$(UV) run mypy libs workflows ai ml synthetic tests
	@for s in $(SERVICES); do \
	  if compgen -G "services/$$s/app/*.py" > /dev/null; then \
	    $(UV) run mypy "services/$$s/app" || exit 1; \
	  fi; \
	done

.PHONY: test
test:  ## unit + contract tests (no docker)
	@$(UV) run pytest
	@for s in $(SERVICES); do \
	  if compgen -G "services/$$s/tests/test_*.py" > /dev/null; then \
	    echo "--- services/$$s"; \
	    ( cd "services/$$s" && $(UV) run pytest -c pyproject.toml --rootdir=. tests ) || exit 1; \
	  fi; \
	done

.PHONY: test-int
test-int:  ## integration tests against the running stack
	@$(UV) run pytest -m integration tests/integration

.PHONY: harness
harness:  ## evaluation harness over golden + adversarial sets
	@$(MAKE) todo TASK=T-080

.PHONY: security
security:  ## tool denial, injection, token replay, secret and dependency scans
	@$(MAKE) todo TASK=T-084

.PHONY: verify
verify:  ## run a phase's acceptance suite, e.g. make verify PHASE=P0
	@if [ -z "$(PHASE)" ]; then echo "usage: make verify PHASE=P0"; exit 2; fi
	@if [ -x scripts/verify_phase.sh ]; then scripts/verify_phase.sh "$(PHASE)"; \
	 else echo "  scripts/verify_phase.sh — T-003"; exit 1; fi

.PHONY: demo
demo:  ## reset -> harness -> print the run-book URLs
	@$(MAKE) todo TASK=T-082

.PHONY: ci
ci: lint typecheck test  ## what CI runs
