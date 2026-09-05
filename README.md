# Credit Intelligence OS — build kit for Claude Code on DGX Spark

This folder is the complete specification and working agreement for building the Cooperative Intelligence
Platform demo (engine: Credit Intelligence OS) on a single NVIDIA DGX Spark using Claude Code. It contains
**no application code yet**: it is the input from which Claude Code builds the code, one task at a time.

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

## How to start on the DGX Spark

```bash
# 1. put the kit where the repo will live
mkdir -p ~/work && cp -r credit-intelligence-os ~/work/ && cd ~/work/credit-intelligence-os
git init && git add -A && git commit -m "kit: specification and build plan"

# 2. environment prerequisites (once)
uname -m            # aarch64
nvidia-smi          # GPU visible
docker --version    # >= 24, NVIDIA container runtime available
curl -LsSf https://astral.sh/uv/install.sh | sh
# Node 20 LTS for the web app; Claude Code installed and authenticated

# 3. start Claude Code in the repo and let it work the plan
claude
> /next-task            # repeat; or ask it to "work through phase P0 task by task using /next-task"
> /verify-phase P0
```

Recommended cadence: run `/next-task` in a loop for one phase, then `/verify-phase`, then use the `reviewer`
subagent on the phase diff before moving on. From P4 onward run `make up-ai-local` first so the harness uses the
real local model, and record measured tokens/s in `docs/PROGRESS.md`.

## Assumptions baked into the kit (change via ADR)

- One DGX Spark; docker compose; PostgreSQL-centred Compact profile; Temporal dev server; local vLLM (Qwen3-30B-A3B for agents, Qwen2.5-VL-7B for documents) with a hosted-provider fallback behind the same gateway.
- Python 3.12 + FastAPI services; one React/TypeScript web app; synthetic data only.
- Demo autonomy setting starts at ASSIST; autonomous execution is shown only in scenario S7.
