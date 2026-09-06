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
seed:  ## load the generated population, index the corpus and decide the golden cases
	@$(UV) run python -m synthetic load --token "$$(scripts/dev_token.sh system)"
	@$(UV) run python -m ai.rag build
	@$(UV) run python -m ai.rag index
	@$(UV) run python scripts/seed_demo_case.py

.PHONY: generate
generate:  ## build the synthetic population and its documents from scratch (slow)
	@$(UV) run python -m synthetic population
	@$(UV) run python -m synthetic rings
	@$(UV) run python -m synthetic documents

.PHONY: reset
reset:  ## wipe the data volumes and rebuild the demo state (needs docker)
	@scripts/reset_demo.sh

.PHONY: warmup
warmup: $(ENV_FILE)  ## one call per LLM route so JIT/compile is done before a demo
	@scripts/warmup_llm.sh

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

.PHONY: seed-demo
seed-demo:  ## put decided golden cases in front of the workbench
	@$(UV) run python scripts/seed_demo_case.py

.PHONY: autonomy-drill
autonomy-drill:  ## turn the dial up, act alone, then stop (needs 'make up')
	@$(UV) run python scripts/autonomy_drill.py

.PHONY: execution-drill
execution-drill:  ## turn an approval into a facility, then replay it (needs 'make up')
	@$(UV) run python scripts/execution_drill.py

.PHONY: train-lmi
train-lmi:  ## train the early-warning and survival models (needs 'make up')
	@$(UV) run python -m ml.lmi

.PHONY: outreach-drill
outreach-drill:  ## schedule reminders, cancel on payment, send an outreach (needs 'make up')
	@$(UV) run python scripts/outreach_drill.py

.PHONY: state-eval
state-eval:  ## walk every member through the state machine and measure it (needs 'make up')
	@$(UV) run python scripts/state_machine_eval.py

.PHONY: changepoint-eval
changepoint-eval:  ## measure detection lead time and false alarms (needs 'make up')
	@$(UV) run python scripts/changepoint_eval.py

.PHONY: member-drill
member-drill:  ## walk a member through S10 and grade what they were told (needs 'make up')
	@$(UV) run python scripts/member_assistant_eval.py

.PHONY: sandbox-drill
sandbox-drill:  ## S6: change the weights, replay, adopt (needs 'make up'; writes a pack version)
	@$(UV) run python scripts/sandbox_drill.py

.PHONY: outage-drill
outage-drill:  ## take the model away and check the platform still decides (needs 'make up')
	@scripts/drill_llm_outage.sh

.PHONY: cockpit-eval
cockpit-eval:  ## grade the cockpit and the manager copilot (needs 'make up')
	@$(UV) run python scripts/cockpit_eval.py

.PHONY: copilot-eval
copilot-eval:  ## ask the officer copilot 25 questions and grade them (needs 'make up')
	@$(UV) run python scripts/copilot_eval.py

.PHONY: override-drill
override-drill:  ## decide a case, override one, check the series (needs 'make up')
	@$(UV) run python scripts/override_drill.py

.PHONY: tamper-drill
tamper-drill:  ## alter a row in each chain and prove both go red (needs 'make up')
	@$(UV) run python scripts/tamper_drill.py

.PHONY: test-e2e
test-e2e:  ## Playwright smoke over the officer workbench (needs 'make up')
	@if [ ! -d apps/web/node_modules ]; then \
	  echo "  apps/web: run 'npm install' there first"; exit 2; fi
	@cd apps/web && npx playwright test

.PHONY: harness
harness:  ## evaluation harness over golden + adversarial sets (needs 'make up')
	@$(UV) run python -m ai.evals.harness --set all --provider real

.PHONY: harness-fast
harness-fast:  ## the harness without the Council: deterministic path and adversarial only
	@$(UV) run python -m ai.evals.harness --set all --provider fake --no-agents

.PHONY: failsafe
failsafe:  ## the fail-safe matrix: stop things and check nothing bad happens (needs 'make up')
	@$(UV) run pytest -m failsafe

.PHONY: security
security:  ## tool denial, injection, token replay, role escalation, secret scan (needs 'make up')
	@$(UV) run pytest -m security
	@echo ""
	@echo "  dependency audit"
	@$(UV) run pip-audit --progress-spinner off 2>/dev/null \
	  || echo "    pip-audit is not installed: uv tool install pip-audit"
	@if [ -d apps/web/node_modules ]; then \
	  ( cd apps/web && npm audit --omit=dev ) || true; \
	 else echo "    apps/web: run 'npm install' there to audit the workbench"; fi

.PHONY: verify
verify:  ## run a phase's acceptance suite, e.g. make verify PHASE=P0
	@if [ -z "$(PHASE)" ]; then echo "usage: make verify PHASE=P0"; exit 2; fi
	@scripts/verify_phase.sh "$(PHASE)"

.PHONY: demo
demo:  ## reset, run every acceptance, print the run-book
	@scripts/demo_check.sh

.PHONY: demo-quick
demo-quick:  ## the same without the model-bound checks
	@scripts/demo_check.sh --quick

.PHONY: dashboards
dashboards:  ## check every Grafana panel draws something (needs 'make up')
	@$(UV) run python scripts/check_dashboards.py

.PHONY: ci
ci: lint typecheck test  ## what CI runs

# ---------------------------------------------------------------------------
# generated files
# ---------------------------------------------------------------------------
$(ENV_FILE): docker/.env.example
	@cp -n docker/.env.example $(ENV_FILE) && echo "created $(ENV_FILE) from the example — review it" || true
	@touch $(ENV_FILE)
