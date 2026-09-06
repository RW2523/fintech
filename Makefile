# Credit Intelligence OS — developer entry points (CLAUDE.md §6)
# Every target listed in CLAUDE.md §6 exists here. Targets not yet implemented
# name the build task that will implement them.

SHELL       := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV          ?= uv
ENV_FILE    ?= docker/.env
COMPOSE     ?= docker compose --env-file $(ENV_FILE) -f docker/compose.yaml
COMPOSE_SPARK ?= docker compose --env-file $(ENV_FILE) -f docker/compose.yaml -f docker/compose.spark.yaml
CORE_PROFILES := --profile core --profile services --profile observability
WAIT_TIMEOUT ?= 300
PHASE       ?=
SERVICES    := $(notdir $(wildcard services/*))
LIBS        := $(notdir $(wildcard libs/*))

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
.PHONY: install
install:  ## install the workspace with every optional stack
	@# --all-extras matters: the data, ml and docai stacks are optional, and a
	@# plain `uv sync` removes them, which breaks the model and document code.
	$(UV) sync --all-extras

.PHONY: env
env:  ## create .env from the example and check the DGX Spark environment
	@if [ ! -f docker/.env ]; then \
	  if [ -f docker/.env.example ]; then cp docker/.env.example docker/.env; echo "created docker/.env"; \
	  else echo "docker/.env.example missing — T-003"; fi; \
	fi
	@if [ -x scripts/env_check.sh ]; then scripts/env_check.sh; else echo "  scripts/env_check.sh — T-002"; fi

.PHONY: up
up: $(ENV_FILE)  ## start core + observability + services (no AI)
	@$(COMPOSE) $(CORE_PROFILES) up -d --build --remove-orphans
	@scripts/wait_healthy.sh $(WAIT_TIMEOUT)

.PHONY: up-ai-local
up-ai-local: $(ENV_FILE)  ## start with vLLM/ollama on the GB10
	@$(COMPOSE_SPARK) $(CORE_PROFILES) --profile ai-local up -d --build --remove-orphans
	@scripts/wait_healthy.sh 900

.PHONY: up-ai-remote
up-ai-remote: $(ENV_FILE)  ## start with a hosted provider behind the gateway
	@$(COMPOSE) $(CORE_PROFILES) --profile ai-remote up -d --build --remove-orphans
	@scripts/wait_healthy.sh $(WAIT_TIMEOUT)

.PHONY: down
down: $(ENV_FILE)  ## stop the stack (keeps volumes; use 'make down-hard' to wipe)
	@$(COMPOSE) --profile core --profile services --profile observability --profile web --profile ai-local --profile ai-remote down --remove-orphans

.PHONY: down-hard
down-hard: $(ENV_FILE)  ## stop the stack and delete its data volumes
	@$(COMPOSE) --profile core --profile services --profile observability --profile web --profile ai-local --profile ai-remote down --remove-orphans -v

.PHONY: logs
logs: $(ENV_FILE)  ## tail stack logs
	@$(COMPOSE) $(CORE_PROFILES) logs -f --tail=100 $(S)

.PHONY: ps
ps: $(ENV_FILE)  ## show container health
	@$(COMPOSE) $(CORE_PROFILES) ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'

.PHONY: migrate
migrate: $(ENV_FILE)  ## alembic upgrade head for every service
	@scripts/migrate.sh $(S)

# ---------------------------------------------------------------------------
# contracts and data
# ---------------------------------------------------------------------------
.PHONY: codegen
codegen:  ## contracts/schemas -> pydantic + typescript types
	@$(UV) run python contracts/codegen/gen_python.py
	@$(UV) run python contracts/codegen/gen_ts.py
	@if [ -d apps/web/node_modules ]; then \
	  ( cd apps/web && npx --no-install tsc --noEmit ) && echo "  apps/web typechecks"; \
	else echo "  apps/web: run 'npm install' there to typecheck the generated contracts"; fi

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
	@# each library and service is its own import root (they share module names
	@# such as `tests` and `app`), so mypy runs once per root
	@for l in $(LIBS); do $(UV) run mypy "libs/$$l/$$l" "libs/$$l/tests" || exit 1; done
	@$(UV) run mypy workflows ai ml synthetic tests
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
	    ( cd "services/$$s" && $(UV) run pytest -c "$(CURDIR)/pyproject.toml" --rootdir=. \
	        -o testpaths=tests tests ) || exit 1; \
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
	@scripts/verify_phase.sh "$(PHASE)"

.PHONY: demo
demo:  ## reset -> harness -> print the run-book URLs
	@$(MAKE) todo TASK=T-082

.PHONY: ci
ci: lint typecheck test  ## what CI runs

# ---------------------------------------------------------------------------
# generated files
# ---------------------------------------------------------------------------
$(ENV_FILE): docker/.env.example
	@cp -n docker/.env.example $(ENV_FILE) && echo "created $(ENV_FILE) from the example — review it" || true
	@touch $(ENV_FILE)
