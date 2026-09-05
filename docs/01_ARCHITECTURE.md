# 01 — Architecture (condensed reference)

The complete rationale is in the companion document *Credit Intelligence OS — Consolidated Technical
Architecture & Demo Execution Specification v3.0*. This file is the build-time reference.

## 1. Planes

| Plane | Contents | Where in repo |
|---|---|---|
| Experience | Officer Workbench, Collections Workbench, Management Cockpit + Policy Sandbox, Member Assistant, Ledger viewer | `apps/web` |
| Decision | Policy Engine (hard gates, Decision Factor Framework, Autonomy Dial), Committee Orchestrator, Synthesizer (deterministic), Execution Service, Decision Ledger | `services/policy`, `services/committee`, `services/decision`, `services/execution` |
| Agent runtime | Council agents, Longitudinal agents, Role copilots, Governance Agent, tool registry, guardrails, LLM gateway, RAG | `services/agent_runtime`, `services/llm_gateway`, `ai/`, `libs/cio_tools` |
| Intelligence services | Document AI, credit-risk, fraud/integrity, LMI (baselines, change-points, forecasts, state machine), explainability | `services/document`, `services/risk`, `services/fraud`, `services/lmi`, `services/governance`, `ml/` |
| Data | PostgreSQL (+pgvector), member event store, feature snapshots, MinIO, ledger, audit | `libs/cio_common`, per-service schemas |
| Integration backbone | gateway, adapters (core stub), outbox events, Temporal workflows | `services/gateway`, `services/core_stub`, `workflows/` |
| Systems of record | core stub (member, financing, payment, accounting, deductions, savings, shares, guarantors) | `services/core_stub` |
| Governance & security (cross-cutting) | auth stub, masking, hash-chained ledger/audit, policy registry, model registry, monitoring, kill switch | `libs/cio_common`, `services/governance`, `services/audit` |

## 2. Principles (enforced by tests)

1. Systems of record stay authoritative; the platform reads broadly and writes narrowly via `execution-service`.
2. Deterministic before generative: numbers come from tools/models with `calc_id`/`model_run_id`.
3. Snapshot in, record out; ledger append-only and hash-chained.
4. Evidence on every claim (`EvidenceRef`).
5. Least-privilege tools; agents hold no credentials.
6. Bounded autonomy via the Autonomy Dial; kill switch per product and platform.
7. Fail safe, not fail open.

## 3. Decision flow (origination)

```
submit → FREEZE CaseSnapshot → documents (classify/extract/forensics/reconcile) ∥ features
→ policy.evaluate (hard gates, affordability, exposure, authority)
→ [blockers?] yes → DecisionRecord(route by policy) → ledger → human
→ risk.score ∥ fraud.assess → tier select
→ Council: ASSESS (parallel, blind) → CHALLENGE → [REPAIR → REVISE]×≤2 → policy.synthesize
→ DecisionRecord (+ narratives) → ledger → policy.route (Autonomy Dial)
→ AUTONOMOUS: token → execution | else human decision (signal) → token → execution
→ outcomes → ledger
```

## 4. Service catalogue (ports on the compose network)

| Service | Port | Owns (schema) | Main endpoints |
|---|---|---|---|
| gateway | 8000 | — | reverse proxy `/api/*`, JWT check, trace ids |
| application | 8001 | `app_application` | `/applications`, `/applications/{id}/submit`, `/cases/{id}` |
| document | 8002 | `app_document` | `/cases/{id}/documents`, `/documents/{id}/extraction`, `/documents/{id}/review` |
| member_intelligence | 8003 | `app_member` (`member_event`) | `/members/{id}/profile`, `/timeline`, `/import` |
| policy | 8004 | `app_policy` | `/policy/evaluate`, `/policy/factors/score`, `/policy/synthesize`, `/policy/route`, `/policy/sandbox/replay`, `/policy/{product}/{version}` |
| feature | 8005 | `app_feature` | `/features/snapshot`, `/features/{id}`, `/features/nightly` |
| risk | 8006 | `app_risk` | `/risk/score`, `/version` |
| fraud | 8007 | `app_fraud` | `/fraud/assess`, `/fraud/signals/{case}`, `/fraud/graph/{case}` |
| lmi | 8008 | `app_lmi` | `/lmi/score`, `/lmi/state/{member}`, `/lmi/alerts`, `/lmi/nightly` |
| committee | 8009 | `app_committee` | `/committee/runs`, `/committee/runs/{id}`, `/committee/runs/{id}/opinions` |
| core_stub | 8010 | `core` | `/core/*` |
| agent_runtime | 8011 | `app_agent` | `/agents/invoke`, `/agents` |
| decision | 8012 | `ledger`, `app_decision` | `/recommendations`, `/human-decisions`, `/ledger`, `/ledger/verify`, `/tokens` |
| execution | 8013 | `app_execution` | `/actions/{id}/execute` |
| notification | 8014 | `app_notification` | `/messages`, `/inbox/{member}` |
| audit | 8015 | `audit` | `/audit`, `/audit/export` |
| governance | 8016 | `app_governance` | `/governance/*`, `/autonomy/{product}`, `/kill-switch/{product}`, `/metrics/*`, `/explain/factors` |
| llm_gateway | 8020 | — | `/llm/complete`, `/llm/vision`, `/llm/embed`, `/llm/rerank`, `/llm/health` |
| web | 5173 (dev) / 8080 | — | SPA |
| temporal | 7233 / UI 8233 | — | workflows |
| postgres | 5432 · redis 6379 · minio 9000/9001 · grafana 3000 · prometheus 9090 · loki 3100 · tempo 3200 | | |

## 5. Deployment profile: Compact (this build)

Single DGX Spark host, docker compose. Everything on one PostgreSQL. Local LLM on the GB10 via vLLM
(preferred) or Ollama; hosted provider optional behind the same gateway. See `02_DGX_SPARK_ENVIRONMENT.md`.

## 6. Architecture decisions in force

AD-1 systems of record authoritative · AD-2 snapshot/ledger · AD-3 synthesis hierarchy · AD-4 deterministic
tools · AD-5 bounded state machine, three tiers · AD-6 four agent families, one runtime · AD-7 LMI first-class ·
AD-8 Compact/Scale profiles · AD-9 PostgreSQL-centred · AD-10 private LLM gateway · AD-11 Autonomy Dial ·
AD-12 evaluation harness gate · AD-13 cooperative-specific elements are core. New decisions go in `docs/adr/`.
