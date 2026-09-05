# Credit Intelligence OS — Claude Code project instructions

You are building **Credit Intelligence OS**, the engine of the *Cooperative Intelligence Platform*: a
governed, multi-agent credit-decisioning and early-warning system for a cooperative credit institution.
The full design lives in `docs/`. This file is the contract between you and the humans who own the
project. Read it fully before doing anything; re-read `docs/00_BUILD_PLAN.md` before every task.

## 1. What we are building (one paragraph)

A system of intelligence that sits beside a cooperative's systems of record. Applications are frozen
into an immutable **CaseSnapshot**; deterministic **policy-as-code** applies hard gates and computes
affordability and **Decision Factor** scores; an **AI Credit Council** of specialist LLM agents assesses,
is challenged, and is synthesised by deterministic code into a **DecisionRecord** with confidence,
disagreement and a route chosen by the Board-controlled **Autonomy Dial**; humans decide; a separate
**Execution Service** writes to the core with an approval token; everything lands in an append-only,
hash-chained **Decision Ledger**. A **Longitudinal Member Intelligence (LMI)** engine watches every active
account for behavioural change (personal baselines, CUSUM/PELT change-points, calibrated forecasts, a
member state machine) and convenes the Council early. The first deliverable is a fully working demo on
one **NVIDIA DGX Spark**, on synthetic data, with ten scripted scenarios verified by an evaluation harness.

## 2. Non-negotiable rules

1. **Deterministic before generative.** Every number (ratio, limit, score, probability, route) is
   produced by a typed tool or model service and carries a `calc_id`/`model_run_id`. An LLM never
   computes, never decides, never invents a value. If you find yourself asking a model for a number, stop.
2. **Contracts first.** The seven contracts in `docs/03_CONTRACTS.md` are the spine. Implement them as
   JSON Schemas in `contracts/schemas/` and generate Pydantic + TypeScript types from them. Do not change
   a contract without writing an ADR in `docs/adr/` and bumping the schema version.
3. **Snapshot in, record out.** No committee run without a `snapshot_id`. No decision without a
   `DecisionRecord` appended to the ledger. Ledger rows are never updated or deleted.
4. **Evidence on every claim.** Agent outputs are schema-validated `AgentOpinion`s; every `claims[]`
   entry must reference `evidence_refs` that were actually returned by tools during that run.
   Reject and retry once, then mark the agent `DEGRADED` and route to a human.
5. **Least privilege for agents.** Agents have no DB access. They call tools from the registry; the
   registry checks the grant, scopes by case and identity, masks fields, and attaches `EvidenceRef`s.
6. **Execution is separate.** Only `execution-service` writes to the core stub, and only with a valid
   `ApprovalToken`, an idempotency key, a fresh case-state check and an armed kill switch check.
7. **Fail safe, never fail open.** If the LLM gateway, a model, or OCR is unavailable, the deterministic
   path continues and the case routes to a human. Nothing is ever approved because something failed.
8. **Compact profile only.** No Kafka, Kubernetes, Feast, KServe or OpenSearch in this build.
   PostgreSQL (+pgvector), Redis, MinIO, Temporal dev server, one model server, one LLM gateway.
9. **arm64 first.** Everything runs on DGX Spark (aarch64, GB10, CUDA 13). Do not add a dependency
   without checking it has aarch64 wheels or a multi-arch image (see `docs/02_DGX_SPARK_ENVIRONMENT.md`).
   **No PaddleOCR.** Use Tesseract + the vision-language model route for documents.
10. **Synthetic data only.** No real member data ever enters this repository or its databases.
    Every UI screen shows a "SYNTHETIC DATA" badge.

## 3. Repository layout (create exactly this; keep it)

```
credit-intelligence-os/
├── CLAUDE.md                     ← this file
├── README.md
├── Makefile                      ← all developer entry points (see §6)
├── pyproject.toml                ← uv workspace root (Python 3.12)
├── docker/
│   ├── compose.yaml              ← Compact profile; profiles: core, ai-local, ai-remote, observability, web
│   ├── compose.spark.yaml        ← DGX Spark overrides (GPU reservations, vLLM/ollama services)
│   ├── .env.example
│   └── images/                   ← Dockerfiles (python-base, web, ocr, model-server)
├── contracts/
│   ├── schemas/                  ← JSON Schema, one file per contract, versioned (e.g. agent_opinion.1.3.json)
│   ├── reason_codes.yaml         ← approved reason-code vocabulary
│   ├── events.yaml               ← event catalogue with payload schemas
│   └── codegen/                  ← scripts: schemas → pydantic (python) and zod/ts types (web)
├── libs/
│   ├── cio_contracts/            ← generated pydantic models + validators (do not hand-edit)
│   ├── cio_common/               ← db session, outbox, ids, hashing, otel, auth stub, settings
│   └── cio_tools/                ← tool registry, grants, masking, EvidenceRef attachment
├── services/                     ← one FastAPI service per folder, each with app/, tests/, Dockerfile
│   ├── gateway/  application/  document/  member_intelligence/  policy/  feature/
│   ├── risk/  fraud/  lmi/  committee/  agent_runtime/  decision/  execution/
│   ├── notification/  audit/  governance/  llm_gateway/  core_stub/
├── workflows/                    ← Temporal workflows + activities (underwriting, early_warning, lmi_nightly)
├── ai/
│   ├── agents/<agent_id>/        ← prompt.md, tools.yaml, output_schema (ref), version.yaml
│   ├── tools/                    ← tool implementations (thin adapters over service APIs)
│   ├── guardrails/               ← injection screen, masking, output policy
│   ├── rag/                      ← chunking, indexing, hybrid retrieval, rerank
│   └── evals/                    ← golden/, adversarial/, harness.py, reports/
├── ml/
│   ├── features/  credit_risk/  fraud/  lmi/  common/   ← training code, model cards, artifacts/
├── policy_packs/
│   ├── PF-STD/2026.09.1/{policy.yaml, dff.yaml, autonomy.yaml}
│   └── PF-SHARIAH/2026.09.1/{...}
├── synthetic/
│   ├── population/  documents/  scenarios/  golden/  cli.py
├── apps/web/                     ← single React+TS app; routes /officer /collections /manager /member /ledger
├── infra/observability/          ← otel-collector, grafana dashboards, prometheus, loki config
├── scripts/                      ← reset_demo.sh, seed.sh, warmup_llm.sh, verify_phase.sh
└── docs/                         ← the design (00–14), adr/, PROGRESS.md
```

## 4. Technology decisions (do not re-litigate; write an ADR if you must deviate)

| Concern | Decision |
|---|---|
| Language / runtime | Python 3.12 with `uv`; FastAPI + Pydantic v2 + SQLAlchemy 2 (async) + Alembic; TypeScript 5 + React 18 + Vite + Tailwind + TanStack Query for the web app |
| Database | PostgreSQL 16 with `pgvector` (image `pgvector/pgvector:pg16`, multi-arch). One database, one schema per service (`app_application`, `app_policy`, …) plus shared `ledger`, `audit`, `events` schemas |
| Events | Transactional outbox table + `LISTEN/NOTIFY` dispatcher in `cio_common.outbox`; consumers idempotent; replayable |
| Workflow | Temporal dev server (`temporalio/auto-setup` + `temporalio/ui`, multi-arch) with the Python SDK `temporalio`. Workflows in `workflows/` |
| Cache / locks | Redis 7 |
| Objects | MinIO (S3 API) |
| Model serving | One FastAPI "model-server" inside `services/risk`, `services/fraud`, `services/lmi` loading versioned artifacts from `ml/*/artifacts/` (pickle/joblib + model card) |
| Document AI | Tesseract (`tesseract-ocr` apt) for text/tables + VLM structured extraction via the LLM gateway (`vision` route). Forensics with Pillow/OpenCV/imagehash |
| LLM access | `services/llm_gateway` exposes one internal API with routes `agent`, `reasoning`, `fast`, `vision`, `embed`, `rerank`; providers: `vllm` (OpenAI-compatible, local on Spark), `ollama`, `anthropic`, `openai_compatible`. Provider per route is config. PII masking and schema enforcement live in the gateway |
| Retrieval | pgvector + PostgreSQL full-text; embeddings `BAAI/bge-m3`; reranker `BAAI/bge-reranker-v2-m3` (both run on GPU via sentence-transformers / FlagEmbedding in `llm_gateway`) |
| Auth (demo) | `cio_common.auth` issues signed JWTs with `sub`, `role`, `branch`, `member_id?`; roles: `officer`, `senior_officer`, `collections`, `manager`, `compliance`, `member`, `system`. Keycloak is a pilot concern, not demo |
| Observability | OpenTelemetry SDK in every service → otel-collector → Prometheus, Loki, Tempo, Grafana (all multi-arch) |
| Testing | `pytest` + `pytest-asyncio` + `hypothesis` (contract fuzzing) + `respx`; Playwright for web smoke; `ai/evals/harness.py` for golden + adversarial |
| Packaging | Each service has a Dockerfile based on `docker/images/python-base` (`python:3.12-slim-bookworm`, arm64). GPU services use `nvcr.io/nvidia/pytorch:25.10-py3` (or later) as base |

## 5. How to work the plan

- `docs/00_BUILD_PLAN.md` lists phases P0–P8 and tasks `T-xxx` in dependency order, each with scope,
  spec references and an **acceptance test command**. `docs/PROGRESS.md` tracks status.
- Work **one task at a time**. For each task: (1) read its spec references; (2) write or update tests
  first when the task has testable behaviour; (3) implement; (4) run the acceptance command; (5) update
  `docs/PROGRESS.md` (status, date, notes, deviations); (6) commit as `T-xxx: <title>`.
- Never skip an acceptance test. If it cannot pass because of an environment issue, record it in
  PROGRESS.md under "Blocked" with the exact error and move to the next unblocked task.
- Use `/next-task` to pick up the next task, `/verify-phase P3` to run a phase's acceptance suite,
  `/demo-reset` to return the environment to the golden state.
- When a spec is ambiguous, choose the simplest option consistent with §2, note it in PROGRESS.md,
  and continue. Do not stop to ask unless the choice changes a contract or a policy semantic.

## 6. Commands (implement these Makefile targets in P0 and keep them working)

```
make env            # create .env from example, check docker/nvidia runtime, uname -m == aarch64
make up             # docker compose up (profiles: core web observability) — no AI
make up-ai-local    # + vLLM/ollama profile on the Spark GPU
make up-ai-remote   # + anthropic/openai-compatible provider (no local GPU model)
make down / logs / ps
make migrate        # alembic upgrade head for every service
make codegen        # contracts/schemas → libs/cio_contracts + apps/web/src/contracts
make seed           # synthetic population + documents + scenarios + golden cases → core stub, MinIO, DB
make reset          # wipe and re-seed to golden state (< 2 min)
make warmup         # one call per LLM route so JIT/compile is done before a demo
make test           # unit + contract tests for all libs/services
make test-int       # integration tests against the running compose stack
make harness        # ai/evals/harness.py over golden + adversarial; writes reports/
make verify PHASE=P3
make demo           # runs scripts/demo_check.sh: reset → harness → open UI URLs
make lint / fmt / typecheck
```

## 7. Coding conventions

- Small services, explicit models: every endpoint has a request/response Pydantic model; no `dict[str, Any]`
  across service boundaries.
- IDs are ULIDs (`cio_common.ids.new_id("snap")` → `snap_01J…`); timestamps are UTC ISO-8601.
- All DB writes that emit events go through `cio_common.outbox.transactional(session)`.
- Correlation: every request/log/span carries `trace_id`, `case_id`, `run_id` when known.
- Money is `Decimal` in Python, `NUMERIC(18,2)` in SQL, string in JSON. Never float for money.
- Config via environment (`pydantic-settings`), documented in `docker/.env.example`.
- Tools: implement in `ai/tools/<name>.py` with a `ToolSpec` (name, version, input schema, output
  schema, required grant, purpose tags). Register in `cio_tools.registry`. Tools attach `EvidenceRef`s.
- Prompts: `ai/agents/<agent_id>/prompt.md` is the single source; loaded and hashed into `agent_version`.
- Web: one component per screen section; data via TanStack Query hooks in `apps/web/src/api/`; no
  business logic in the UI — the UI renders DecisionRecords, it never recomputes them.
- Tests live next to code (`services/x/tests`); golden cases live in `ai/evals/golden/`.
- Commit messages: `T-xxx: imperative summary`. One task per commit where practical.

## 8. Things you must not do

- Do not call an LLM from anywhere except `services/llm_gateway` (services call the gateway).
- Do not let the web app compute scores, routes or ratios.
- Do not store agent prose as truth; store opinions with evidence ids and regenerate narratives.
- Do not add message brokers, Kubernetes manifests, Terraform, PaddleOCR, or x86-only images.
- Do not put secrets in the repo; `.env` is git-ignored; `.env.example` has placeholders only.
- Do not weaken a guardrail to make a scenario pass; fix the scenario data or the prompt instead.
- Do not "simplify" the hierarchy in the Synthesizer: hard gates → evidence validity → authority →
  weighted score → confidence/disagreement → Autonomy Dial route. In that order. Always.

## 9. Environment pointers

- Hardware/software facts, container images, model choices, vLLM/ollama flags and arm64 gotchas:
  `docs/02_DGX_SPARK_ENVIRONMENT.md`.
- The demo host is the DGX Spark itself. Web UIs are served on the Spark's LAN address; the LLM runs
  locally on the GB10 by default (`ai-local` profile). The `ai-remote` profile is for a hosted model
  behind the gateway with masking; both must work.

## 10. Definition of done for the whole build

All of `docs/14_DEFINITION_OF_DONE.md` — in short: ten scenarios pass the harness from `make reset`
twice in a row on the Spark; every on-screen number links to a `calc_id`/`model_run_id`; any decision
reconstructs from the ledger in under two minutes; Autonomy Dial, kill switch and override are exercised
and audited; the LLM-outage rehearsal passes; `make demo` is green.
