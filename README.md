# Credit Intelligence OS

A credit decisioning platform for a cooperative credit institution, built on one
NVIDIA DGX Spark. Eighteen FastAPI services, a React workbench, a deliberating
Council of agents over a local model, and an append-only record of everything
decided.

The principle everything else follows from: **a model never computes a number
and never makes a decision.** Models argue; policy decides. Every figure carries
the id of the calculation that produced it, and every claim an agent makes cites
evidence a person can open.

## Getting started

```bash
make env        # docker/.env from the example, then check the machine
make install    # sync the workspace with every optional stack
make up         # core, services and observability
make up-ai-local  # add the model on the GB10 (slow first time)
make demo       # reset, run every acceptance, print the run-book
```

Then http://localhost:8080 for the workbench and http://localhost:8000/docs for
the API.

- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — start, stop, reset, roll back,
  and what to do when something is wrong.
- **[docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md)** — the forty-five minute
  demonstration, scenario by scenario.
- **[docs/PROGRESS.md](docs/PROGRESS.md)** — what was built, what was measured,
  and every acceptance number that is not met.

Everything in this build is synthetic. There is no real member data in it and
the seed refuses to run against a production environment.

## What is here

| Path | What it is |
|---|---|
| `services/` | Eighteen services: gateway, application, document, policy, committee, decision, execution, agent runtime, LMI, governance and the rest |
| `apps/web/` | The workbench: officer queue, case file, collections, compliance, ledger, manager cockpit, policy sandbox, member assistant |
| `ai/` | Agent bundles and prompts, the tool catalogue, guardrails, retrieval, and the evaluation harness |
| `ml/` | Credit risk, fraud and early-warning models, with their cards and their limitations |
| `libs/` | What the services share: contracts, auth, errors, ids, the outbox, tools, metrics, tracing |
| `policy_packs/` | The rules, as files. Versioned, write-once, and what every decision cites |
| `synthetic/` | The generated population, its documents, and the golden cases |
| `workflows/` | Temporal workflows and activities |
| `infra/observability/` | Grafana dashboards and the collector configuration |
| `tests/` | Integration, fail-safe and security suites |
| `scripts/` | Reset, demo check, the drills, and the phase verifications |

## The specification

The platform was built from these, one task at a time, and they remain the
normative description of what it is meant to do.

## Contents

| Path | Purpose |
|---|---|
| `CLAUDE.md` | Project instructions Claude Code loads automatically: rules, layout, stack, commands, conventions, definition of done |
| `docs/00_BUILD_PLAN.md` | Phases P0–P8 and tasks T-001…T-085 with scope, spec references and acceptance tests |
| `docs/01_ARCHITECTURE.md` | Planes, principles, decision flow, service catalogue, ports |
| `docs/02_DGX_SPARK_ENVIRONMENT.md` | Hardware facts, images, local LLM strategy (vLLM/Ollama), memory budget, `.env`, arm64 checklist, performance targets |
| `docs/03_CONTRACTS.md` | The backbone contracts (normative field tables), reason codes, event catalogue |
| `docs/04_DATA_MODEL.md` | PostgreSQL schemas (DDL-level), ledger and audit hash chains, retention |
| `docs/05_POLICY_PACKS.md` | Full policy.yaml / dff.yaml / autonomy.yaml, rule language, factor scoring, Synthesizer, routing, Sandbox |
| `docs/06_AGENT_RUNTIME.md` | Agent registry, full prompts, tool catalogue, context assembly, orchestrator state machine, LLM gateway, RAG, guardrails |
| `docs/07_INTELLIGENCE_SERVICES.md` | Document AI, features, credit-risk and fraud models, LMI (baselines, CUSUM/PELT, forecasts, state machine, alerts), notifications, explainability |
| `docs/08_API_AND_EVENTS.md` | Endpoints per service, Temporal workflows, core stub API, roles, event consumers |
| `docs/09_UI_SPEC.md` | Screens and components for officer, collections, member, ledger, manager |
| `docs/10_SYNTHETIC_DATA.md` | Population, archetypes, documents, anomalies, applications, scenarios, golden cases, policy corpus |
| `docs/11_DEMO_SCENARIOS.md` | The ten scenarios with expected outputs, the run-book, scripts, pre-flight |
| `docs/12_EVALS_AND_TESTING.md` | Test layers, fake gateway, harness metrics and thresholds, adversarial corpus, CI |
| `docs/13_SECURITY_AND_OPS.md` | Auth, masking, agent safety, execution safety, observability, runbooks, fail-safe matrix |
| `docs/14_DEFINITION_OF_DONE.md` | Per task, per phase, whole build |
| `docs/PROGRESS.md` | Task tracker (Claude Code updates it) |
| `docs/adr/` | Architecture decision records |
| `.claude/settings.json` | Permission allow-list for the commands the build needs |
| `.claude/commands/` | `/next-task`, `/verify-phase Px`, `/demo-reset`, `/adr <title>` |
| `.claude/agents/` | `reviewer` and `harness-runner` subagents |
| `FULL_BUILD_SPEC.md` | All of the above concatenated into one file (for reading or for tools that want a single document) |

## What the machine needs

```bash
uname -m            # aarch64
nvidia-smi          # the GB10, with 128 GB shared
docker --version    # >= 24, NVIDIA container runtime available
curl -LsSf https://astral.sh/uv/install.sh | sh
# Node 22 for the workbench
```

`scripts/env_check.sh` reports what it finds against what it expects, including
any port already taken by something else on the host. `make env` runs it.

## Checking it works

```bash
make ci          # lint, typecheck, unit and contract tests
make harness     # the golden and adversarial sets
make failsafe    # stop things and check nothing bad happens
make security    # tool denial, injection, token replay, role escalation
make verify PHASE=P7
```

## Assumptions baked into the kit (change via ADR)

- One DGX Spark; docker compose; PostgreSQL-centred Compact profile; Temporal dev server; local vLLM (Qwen3-30B-A3B for agents, Qwen2.5-VL-7B for documents) with a hosted-provider fallback behind the same gateway.
- Python 3.12 + FastAPI services; one React/TypeScript web app; synthetic data only.
- Demo autonomy setting starts at ASSIST; autonomous execution is shown only in scenario S7.
