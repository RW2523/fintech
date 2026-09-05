# 00 — Build Plan (phases, tasks, acceptance tests)

This is the executable plan. Tasks are ordered by dependency. Each task is sized for one focused
Claude Code session (roughly 1–4 hours of work). **Do the acceptance test before marking a task done.**
Status is tracked in `docs/PROGRESS.md` (create it in T-001 from the template at the end of this file).

Conventions: `→` = produces; `deps` = tasks that must be complete; `spec` = documents to read first.
Acceptance commands assume the repo root and a running stack where stated.

---

## Phase P0 — Foundation (repo, environment, contracts, common libraries, core stub)

### T-001 Repository skeleton and tooling
- deps: none · spec: `CLAUDE.md §3–7`
- scope: create the directory tree from CLAUDE.md §3; `pyproject.toml` (uv workspace with `libs/*`,
  `services/*`, `workflows`, `ai`, `ml`, `synthetic` as members); `.gitignore`; `.editorconfig`;
  `ruff` + `mypy` + `pytest` config; `Makefile` with every target in CLAUDE.md §6 (targets may be stubs
  that print "not implemented" but must exist); `README.md`; `docs/PROGRESS.md` from the template below.
- → repo compiles: `uv sync` succeeds; `make lint` runs ruff over an empty tree.
- acceptance: `uv sync && make lint && make test` (test may report "no tests ran" but exit 0 via `pytest -q || true`
  is **not** acceptable — configure `pytest` with `--co -q` fallback so empty suites exit 0 legitimately, or add one trivial test).

### T-002 DGX Spark environment check script
- deps: T-001 · spec: `docs/02_DGX_SPARK_ENVIRONMENT.md`
- scope: `scripts/env_check.sh` verifies `uname -m == aarch64`, Docker ≥ 24, `nvidia-smi` works,
  NVIDIA container runtime available (`docker run --rm --gpus all nvidia/cuda:13.0.1-base-ubuntu24.04 nvidia-smi`),
  free memory ≥ 96 GB, disk ≥ 200 GB free, ports 5432/6379/9000/7233/8080/3000/8000 free. Prints a table.
  `make env` copies `docker/.env.example` → `.env` if missing and runs the check.
- acceptance: `make env` exits 0 on the Spark and prints all checks PASS (or WARN for optional GPU when running off-Spark).

### T-003 Compose stack (core profile)
- deps: T-002 · spec: `docs/02_DGX_SPARK_ENVIRONMENT.md §5`, `docs/13_SECURITY_AND_OPS.md §6`
- scope: `docker/compose.yaml` with profiles `core` (postgres+pgvector, redis, minio, minio-init, temporal,
  temporal-ui), `observability` (otel-collector, prometheus, loki, tempo, grafana), `web`, `services`
  (every service from CLAUDE.md §3, initially building from a placeholder Dockerfile that runs a
  "hello" FastAPI), `ai-local`, `ai-remote`. `docker/compose.spark.yaml` adds GPU reservations for
  `llm_gateway`, `vllm`, `ollama`. Healthchecks on every infra container. Named volumes.
- acceptance: `make up` brings up `core` + `observability` with all healthchecks green (`make ps` shows healthy);
  `psql` can connect; MinIO console reachable; Temporal UI reachable on :8233; Grafana on :3000.

### T-004 Contracts: JSON Schemas and codegen
- deps: T-001 · spec: `docs/03_CONTRACTS.md`
- scope: `contracts/schemas/*.json` for CaseSnapshot 1.0, EvidenceRef 1.0, AgentOpinion 1.3,
  DecisionRecord 1.0, HumanDecision 1.0, ActionProposal 1.0, ApprovalToken 1.0, MemberEvent 1.0,
  PolicyResult 1.0, FactorScore 1.0, CommitteeRun 1.0; `contracts/reason_codes.yaml`; `contracts/events.yaml`.
  `contracts/codegen/gen_python.py` (datamodel-code-generator → `libs/cio_contracts/models.py`) and
  `gen_ts.py` (json-schema-to-typescript or zod codegen → `apps/web/src/contracts/`). `make codegen`.
  Round-trip tests with `hypothesis-jsonschema`.
- acceptance: `make codegen && uv run pytest libs/cio_contracts -q` passes; generated TS compiles (`npx tsc --noEmit` in apps/web).

### T-005 Common library: settings, ids, db, outbox, hashing, otel, auth stub
- deps: T-004 · spec: `docs/04_DATA_MODEL.md §1, §7`, `docs/13_SECURITY_AND_OPS.md §1–3`
- scope: `libs/cio_common/{settings,ids,db,outbox,hashing,otel,auth,errors,http}.py`. Outbox: table
  `events.outbox(event_id, name, version, key, payload, trace_id, created_at, dispatched_at)`; dispatcher
  process using `LISTEN/NOTIFY` with at-least-once delivery and `events.consumer_offsets`. Hash chain
  helper `chain_hash(prev_hash, canonical_json)`. Auth: `issue_token(sub, role, claims)`, FastAPI dependency
  `require_role(...)`, ABAC helper `scope_for(user)`. OTel bootstrap `init_otel(service_name)`.
- acceptance: `uv run pytest libs/cio_common -q` (outbox at-least-once + idempotent consumer test, hash chain test, auth tests) passes.

### T-006 Tool registry library
- deps: T-005 · spec: `docs/06_AGENT_RUNTIME.md §4`
- scope: `libs/cio_tools/{registry,spec,grants,masking,evidence}.py`: `ToolSpec`, `@tool` decorator,
  grant check (`agent_id`, `principal`, `case_scope`, `purpose`), field masking by policy, automatic
  `EvidenceRef` attachment on outputs, invocation audit record, per-run call budget.
- acceptance: `uv run pytest libs/cio_tools -q` — denied grant raises `ToolDenied`; outputs carry evidence refs; budget exceeded raises.

### T-007 Core-system stub
- deps: T-005 · spec: `docs/04_DATA_MODEL.md §2`, `docs/08_API_AND_EVENTS.md §9`
- scope: `services/core_stub`: PostgreSQL schema `core` (member, employer, account, schedule, payment,
  deduction, savings, share_capital, guarantor, application_ext) + REST façade (`/core/members/{id}`,
  `/core/accounts`, `/core/payments`, `/core/deductions`, `/core/write/activate`, `/core/write/status`)
  + change feed (`/core/changes?since=`), + write endpoints that require header `X-Approval-Token`
  (validated later by execution-service; stub only logs). Alembic migrations.
- acceptance: `make migrate` succeeds; `uv run pytest services/core_stub -q`; `curl :8010/core/health` ok.

### T-008 Service scaffolding for all domain services
- deps: T-005 · spec: `docs/01_ARCHITECTURE.md §4`, `docs/08_API_AND_EVENTS.md`
- scope: for each service in CLAUDE.md §3 create `app/main.py` (FastAPI, OTel, health, version),
  `app/settings.py`, `app/db.py`, `alembic/`, `tests/test_health.py`, `Dockerfile`, and gateway routes in
  `services/gateway` (reverse proxy with JWT check and `X-Trace-Id`). Compose `services` profile builds them.
- acceptance: `make up` (profiles core+services) → every service `/health` returns `{"status":"ok","version":...}` through the gateway; `make test` green.

---

## Phase P1 — Policy, application, ledger, workflow skeleton

### T-010 Policy packs (PF-STD, PF-SHARIAH) as files
- deps: T-004 · spec: `docs/05_POLICY_PACKS.md`
- scope: `policy_packs/PF-STD/2026.09.1/{policy.yaml,dff.yaml,autonomy.yaml}` and PF-SHARIAH; a
  `policy_packs/schema/` JSON Schema for each file; loader with validation and semantic checks
  (weights sum to 1, thresholds ordered, authority bands ascending).
- acceptance: `uv run pytest services/policy/tests/test_packs.py -q` validates both packs and rejects a broken fixture.

### T-011 policy-service: hard gates, affordability, exposure, authority
- deps: T-010, T-008 · spec: `docs/05_POLICY_PACKS.md §2–3`
- scope: `POST /policy/evaluate` (snapshot + inputs → `PolicyResult` with per-rule results, reason codes,
  `evidence_refs`, `blockers`, `required_authority`, `evidence_coverage`); deterministic `affordability.compute`
  tool (DSR, headroom, stress cases, `capacity_score`, `calc_id`); rule expressions evaluated with a
  safe expression evaluator (no `eval`), versioned by pack.
- acceptance: `uv run pytest services/policy -q` — golden fixtures for ELG/DOC/AFF/EXP/RT rules incl. boundary cases; property test: DSR monotonic in commitments.

### T-012 policy-service: Decision Factor scoring and Synthesizer
- deps: T-011 · spec: `docs/05_POLICY_PACKS.md §4–6`
- scope: `POST /policy/factors/score` (family scoring functions from `dff.yaml`, returns `FactorScore[]` with `calc_id`);
  `POST /policy/synthesize` implementing the exact hierarchy (gates → evidence validity → authority →
  weighted score → confidence → disagreement → route) and returning a `DecisionRecord` skeleton
  (without narratives); `POST /policy/route` (Autonomy Dial evaluation with all conditions, sampling draw, kill-switch check).
- acceptance: `uv run pytest services/policy/tests/test_synthesize.py -q` — table-driven tests for every branch of the hierarchy; disagreement metric tests; autonomy condition matrix (each condition individually failing blocks AUTONOMOUS).

### T-013 policy-service: Policy Sandbox replay
- deps: T-012 · spec: `docs/05_POLICY_PACKS.md §7`
- scope: `POST /policy/sandbox/replay` — takes a candidate pack (or weight overrides) and a snapshot
  range; replays gates + factor scoring + synthesis over stored snapshots (no LLM); returns approval
  rate, exposure, projected delinquency (from stored outcomes/forecasts), affected segments, and per-case diffs.
- acceptance: integration test replays 50 seeded snapshots in < 10 s and reports diffs vs baseline.

### T-014 decision-service: Decision Ledger and human decisions
- deps: T-005, T-008 · spec: `docs/04_DATA_MODEL.md §5`, `docs/03_CONTRACTS.md`
- scope: append-only `ledger.entry(entry_id, kind, payload, hash, prev_hash, created_at)` with a DB
  trigger that rejects UPDATE/DELETE; `POST /recommendations` (append DecisionRecord); `POST /human-decisions`
  (validate role against `required_authority` and authority matrix at call time; mandatory override reason);
  `GET /ledger?case_id=` (full chain); `POST /tokens` (ApprovalToken issuance, HMAC-signed, single-use, TTL);
  `GET /ledger/verify` recomputes the chain.
- acceptance: `uv run pytest services/decision -q` — chain verification detects tampering; UPDATE on ledger raises; unauthorised role rejected; override without reason rejected.

### T-015 application-service and CaseSnapshot freeze
- deps: T-007, T-014 · spec: `docs/08_API_AND_EVENTS.md §1`, `docs/03_CONTRACTS.md §1`
- scope: `POST /applications`, `POST /applications/{id}/submit`, `GET /applications/{id}`; `submit`
  assembles the CaseSnapshot (member projection version from core stub, document bundle version,
  policy/dff/autonomy versions from policy-service, model versions from model services' `/version`),
  stores it immutably, emits `application.submitted` via outbox.
- acceptance: integration test: submit → snapshot persisted with all version fields; second submit creates a new snapshot version; event observed in outbox.

### T-016 Temporal underwriting workflow skeleton
- deps: T-015, T-011 · spec: `docs/08_API_AND_EVENTS.md §8`
- scope: `workflows/underwriting.py` (`UnderwriteCase`) with activities `freeze`, `evaluate_policy`,
  `record_decision` and signal `human_decision`; worker entrypoint; committee/model steps stubbed to
  return deterministic placeholders (to be replaced in P3/P4). Idempotent start on `snapshot_id`.
- acceptance: `uv run pytest workflows -q` using Temporal's test environment; a blocked application (ELG-02 fail) ends with a ledger DecisionRecord and route per policy.

---

## Phase P2 — Synthetic data and document intelligence

### T-020 Synthetic population and payment history generator
- deps: T-007 · spec: `docs/10_SYNTHETIC_DATA.md §1–4`
- scope: `synthetic/population/` — members, employers, accounts, 24-month schedules, payment events,
  salary-deduction receipts with employer interruptions, savings and share capital, archetype dynamics,
  outage windows, arrangements; deterministic with `--seed`; writes to core stub via its API and to
  `member_event` (later) via `member-intelligence-service` import endpoint; `synthetic/cli.py population`.
- acceptance: `uv run python -m synthetic.cli population --n 5000 --months 24 --seed 42` completes < 5 min; summary stats within the ranges in `docs/10 §4.6` (delinquency 6–8 %, archetype mix); re-run with same seed is byte-identical.

### T-021 Outcome labels
- deps: T-020 · spec: `docs/10_SYNTHETIC_DATA.md §5`
- scope: derive per account-month labels (late7/30/60/90, cure, restructure, charge-off) from events; write `core.outcome`.
- acceptance: label unit tests on hand-built event sequences; label counts logged.

### T-022 Synthetic document generator
- deps: T-020 · spec: `docs/10_SYNTHETIC_DATA.md §6`
- scope: `synthetic/documents/` — HTML templates (12 employer payslip templates, bank statement, ID card,
  employment letter, provident-fund statement) rendered via Playwright/Chromium (arm64 OK) to PDF and PNG,
  with scan noise/skew (Pillow), ground-truth JSON per document, and controlled anomalies (edited total,
  font swap, reused image, metadata mismatch). Upload to MinIO with ground truth in DB.
- acceptance: `uv run python -m synthetic.cli documents --apps 600` produces ~2,400 documents; ground truth present for all; anomaly manifest lists injected cases.

### T-023 document-service: ingest, classify, extract
- deps: T-008, T-022 · spec: `docs/07_INTELLIGENCE_SERVICES.md §1`
- scope: `POST /cases/{id}/documents` (presigned upload → MinIO), virus/MIME/size checks, page render,
  perceptual hash; classification (VLM route with a constrained label set; Tesseract text as a hint);
  extraction: Tesseract words+boxes, VLM key-value extraction with bbox anchoring; confidence per field;
  `EvidenceRef`s; `GET /documents/{id}/extraction`; `POST /documents/{id}/review` (human correction becomes evidence).
- acceptance: on the golden document set (`synthetic/golden/documents`), critical fields ≥ 95 % exact match (target 97 % by P8); classification ≥ 98 %; each field has bbox and confidence.

### T-024 document-service: forensics and reconciliation
- deps: T-023 · spec: `docs/07_INTELLIGENCE_SERVICES.md §1.4–1.5`
- scope: metadata consistency, font/kerning heuristics, copy-move (OpenCV ORB), image-hash reuse across
  submissions, template mismatch; reconciliation rules across payslip/bank/deduction/core with variance thresholds → findings `INT-xx` with severity; entity resolution (rapidfuzz Jaro-Winkler + bge-m3 embeddings).
- acceptance: all injected anomalies in the manifest detected with severity ≥ MEDIUM; false-positive rate on clean documents ≤ 3 %.

### T-025 member-intelligence-service: profile projection and timeline import
- deps: T-007, T-020 · spec: `docs/04_DATA_MODEL.md §3`, `docs/07 §4.1`
- scope: `member_event` partitioned table; import from core stub change feed; profile projection
  (`GET /members/{id}/profile`), `GET /members/{id}/timeline`; permitted-use tags; data-quality fields.
- acceptance: import of the synthetic population yields ~180k events; projection matches core stub for 100 random members; timeline paginated.

---

## Phase P3 — Feature store and models

### T-030 feature-service: origination features and snapshots
- deps: T-025 · spec: `docs/07_INTELLIGENCE_SERVICES.md §2.1`
- scope: feature definitions registry (name, window, source, permitted uses, version); `POST /features/snapshot`
  computes and stores an immutable `feature_snapshot`; `GET /features/{snapshot_id}`.
- acceptance: snapshot reproducible (same inputs → same values); feature registry test; permitted-use filter test.

### T-031 Credit-risk model training (scorecard champion, LightGBM challenger)
- deps: T-021, T-030 · spec: `docs/07 §2.2`, `docs/12_EVALS_AND_TESTING.md §5`
- scope: `ml/credit_risk/train.py` — time-based split, binned monotonic logistic scorecard, LightGBM with
  monotone constraints, isotonic calibration, SHAP → reason-code mapping, OOD score (isolation forest on features),
  model card, artifacts with version; `ml/common/registry.py` for artifact loading.
- acceptance: `uv run python -m ml.credit_risk.train` writes artifacts + card; hold-out AUC ≥ 0.72, calibration slope 0.9–1.1 (synthetic).

### T-032 risk-service
- deps: T-031, T-008 · spec: `docs/07 §2.3`
- scope: `POST /risk/score` returns champion/challenger PD, grade, `conduct_score`, reason codes, drivers, `model_run_id`, evidence refs; `GET /version`.
- acceptance: contract tests; latency < 200 ms per call; reproducible run ids.

### T-033 fraud-service: rules, entity resolution, anomaly, guarantor graph
- deps: T-024, T-030 · spec: `docs/07 §3`
- scope: rule engine (velocity, duplicates, contact change, reused images), entity resolution graph
  (networkx), cycle/centrality findings, Isolation Forest advisory score, severity grading, `integrity_score`;
  `POST /fraud/assess`, `GET /fraud/signals/{case}`, `GET /fraud/graph/{case}` (subgraph JSON).
- acceptance: guarantor ring fixture (7 members) found as a cycle; duplicate applicant found; clean cases ≤ 3 % false findings.

### T-034 Explainability service (in governance-service)
- deps: T-032 · spec: `docs/07 §6`
- scope: `POST /explain/factors` maps SHAP + reason codes + policy results to the three-level narrative
  inputs (structured only, no LLM); `GET /governance/models` model inventory from cards.
- acceptance: unit tests; narrative inputs contain only ids present in the run.

---

## Phase P4 — LLM gateway, agent runtime, Council v1, Officer Workbench v1

### T-040 llm-gateway: providers, routes, masking, schema enforcement
- deps: T-008 · spec: `docs/06_AGENT_RUNTIME.md §6`, `docs/02 §4`
- scope: `POST /llm/complete` (route, messages, json_schema, max_tokens, budget), `POST /llm/vision`,
  `POST /llm/embed`, `POST /llm/rerank`; providers `vllm`, `ollama`, `anthropic`, `openai_compatible`
  selected per route via env; PII masking/unmasking (deterministic token map per request); structured
  output (JSON schema) with validation + one retry; token accounting; timeouts; circuit breaker → `503 LLM_UNAVAILABLE`.
- acceptance: `uv run pytest services/llm_gateway -q` with a fake provider; on the Spark: `make up-ai-local && make warmup` succeeds for all routes; `ai-remote` profile passes the same smoke with a hosted provider key.

### T-041 RAG index over policy corpus
- deps: T-040, T-010 · spec: `docs/06 §7`
- scope: `ai/rag/` chunking with clause ids/versions, pgvector index, hybrid retrieval (tsvector + vector), rerank; CLI to index `synthetic/policy_corpus/`.
- acceptance: retrieval test: clause-id questions return correct clause in top-3 ≥ 90 %.

### T-042 agent-runtime: registry, prompts, tools, guardrails
- deps: T-006, T-040 · spec: `docs/06 §1–5`
- scope: agent bundle loader (`ai/agents/<id>/`), `agent_version` = hash(prompt, tools, schema, model route);
  context assembler (snapshot summary, tool results, retrieved clauses, prior opinions in REVISE only);
  invocation loop with tool calling; output validation to `AgentOpinion`; guardrails (data-not-instruction
  wrapping, injection classifier, output policy screen); DEGRADED path; `POST /agents/invoke`.
- acceptance: unit tests with fake gateway: schema failure → retry → DEGRADED; injection fixture neutralised; claims without evidence rejected.

### T-043 Council agents v1 (six) and tools
- deps: T-042, T-011, T-032, T-033, T-025 · spec: `docs/06 §2–3` (full prompts), `docs/06 §4` (tool list)
- scope: prompts and tool grants for Document & Evidence, Policy & Affordability, Credit Risk, Fraud & Integrity,
  Member Relationship, Challenger; tools implemented in `ai/tools/` over service APIs; Portfolio Intelligence as a service call (Conditions score).
- acceptance: each agent produces a valid AgentOpinion on 5 golden snapshots with a fake and with the real gateway; factor scores equal tool outputs.

### T-044 committee-orchestrator: Tier 1 state machine and synthesis
- deps: T-043, T-012, T-014 · spec: `docs/06 §5`
- scope: `POST /committee/runs` (idempotent on snapshot+tier), tier selection, parallel ASSESS, one Challenger
  pass, call `policy/synthesize`, narratives via `reasoning` route (member/officer/auditor) generated from the
  structured record, persist run + opinions + DecisionRecord (ledger), budgets and timeouts → partial record + human route.
- acceptance: golden S1 and S4 produce expected routes; timeout test yields partial record; run reproducible from snapshot (same opinions with fake gateway).

### T-045 Wire workflow: real committee and model activities
- deps: T-044, T-016 · spec: `docs/08 §8`
- scope: replace P1 stubs with document, feature, risk, fraud, committee, route and human-decision activities; token issuance on AUTONOMOUS; execution activity (stub until T-052).
- acceptance: end-to-end integration test: submit S1 → ledger has snapshot, run, record; Temporal UI shows history.

### T-046 Web app foundation and Officer Workbench v1
- deps: T-045, T-004 · spec: `docs/09_UI_SPEC.md §1–3`
- scope: `apps/web` (Vite, React, TS, Tailwind, TanStack Query, router, auth stub login as role);
  Officer queue, case page: header, signal cards, decision card (recommendation, confidence, disagreement,
  factor bars, decisive factor, unresolved, would-change), evidence panel with document viewer + bbox highlight,
  Agent Discussion drawer (structured positions), actions (request info, escalate, approve/decline per authority, override with reason). SYNTHETIC badge.
- acceptance: Playwright smoke: login as officer → open S1 → decision card renders values equal to the DecisionRecord JSON; click a claim → evidence panel highlights bbox.

---

## Phase P5 — Governance: Challenger loop, Autonomy Dial, execution, audit

### T-050 Tier 2: Repair and Revise loops, disagreement handling
- deps: T-044 · spec: `docs/06 §5.3`
- scope: Challenger `unresolved[].requested_evidence` → Orchestrator evidence repair (tool calls / model reruns / evidence revision), REVISE round with `changed_from_prior`, bounded to two loops; disagreement thresholds route; Tier-2 triggers.
- acceptance: golden S2 yields Challenger reservation + route OFFICER_REVIEW + "would change" entries; loop bound test.

### T-051 Autonomy Dial administration, sampling, kill switch
- deps: T-012, T-014 · spec: `docs/05 §6`, `docs/08 §6`
- scope: `POST /autonomy/{product}` (two-approver flow), `POST /kill-switch/{product}`, sampling queue
  (`ledger.sample_review`), `autonomy.setting_changed`/`kill_switch.activated` events; governance-service endpoints and UI hooks.
- acceptance: after setting AUTONOMOUS_WITHIN_LIMITS, a clean small case routes AUTONOMOUS with a token and lands in the sampling queue; after kill switch, same case routes OFFICER_REVIEW with reason KILL_SWITCH.

### T-052 execution-service and core write-back
- deps: T-051, T-007 · spec: `docs/08 §7`, `docs/13 §4`
- scope: `POST /actions/{id}/execute` validates ApprovalToken (signature, scope, expiry, single use), idempotency key, case state, kill switch; saga with compensation for `activate_financing`; before/after state to audit; failure leaves workflow pending.
- acceptance: replayed token rejected; duplicate execute is a no-op; forced core failure leaves case pending and retry succeeds once.

### T-053 audit-service and Ledger viewer
- deps: T-014, T-046 · spec: `docs/04 §6`, `docs/09 §6`
- scope: append-only audit table (hash chain) fed by all services via `cio_common.audit`; `GET /audit?case_id=`; daily WORM export to MinIO; web `/ledger` route: case reconstruction timeline (snapshot → opinions → record → human decision → token → execution), chain verification badge.
- acceptance: reconstruction of S2 renders in < 2 min including document evidence; `GET /ledger/verify` green; tamper test red.

### T-054 Human decision UI and override analytics
- deps: T-046, T-014 · spec: `docs/09 §3.4`, `docs/07 §6`
- scope: approve/decline/request-info/escalate with authority enforcement in decision-service; override reason codes; governance-service `GET /governance/overrides` series; compliance view list of high-disagreement and override cases.
- acceptance: officer over authority limit cannot approve (UI hides, API rejects); override reason mandatory; overrides appear in governance series.

---

## Phase P6 — Longitudinal Member Intelligence, collections, notifications

### T-060 Temporal features, personal baselines
- deps: T-025, T-030 · spec: `docs/07 §4.2`
- scope: windows 7/30/90/180/365; families payment timing, deduction integrity, savings/shares, capacity, interaction, recovery; robust median/MAD baselines; STL seasonal adjustment for seasonal signals; nightly and event-triggered materialisation; `lmi-service` reads.
- acceptance: feature tests on constructed series; nightly job for 5,000 members × 24 months < 10 min on the Spark.

### T-061 Change-point and anomaly detection
- deps: T-060 · spec: `docs/07 §4.3`
- scope: one-sided CUSUM per signal with parameters from config; PELT confirmation (`ruptures`); Isolation Forest advisory; `behaviour.change_point_detected` events.
- acceptance: injected drift cases (archetype `slow-drift`) detected with median lead ≥ 21 days before first late event; steady payers false alarms ≤ 1.5 % per year.

### T-062 Early-warning and survival models
- deps: T-021, T-060 · spec: `docs/07 §4.4`
- scope: `ml/lmi/train.py` — LightGBM per horizon (7/30/60/90) on lagged/rolling features, time-based split, isotonic calibration, conformal intervals; Cox PH (`lifelines`) time-to-first-late and time-to-cure; SHAP drivers; cards; served by `lmi-service` `POST /lmi/score`.
- acceptance: AUC ≥ 0.75 at 30 days on synthetic hold-out; calibration slope 0.9–1.1; intervals cover ≥ 88 % at 90 % nominal.

### T-063 Member state machine, hysteresis, corroboration, alert hygiene
- deps: T-061, T-062 · spec: `docs/07 §4.5–4.7`
- scope: transitions exactly as specified; corroboration rules (outage window, arrangement, second family); alert dedupe, ranking by expected value, per-officer cap, "why now"; `member.state_changed`, `early_warning.case_created/closed`.
- acceptance: S8 member reaches ELEVATED with change-point ≈ 42 days before, S9 outage produces zero escalations, recovery path returns to RECOVERY then STABLE; oscillation test (noisy series) never flips more than once per 30 days.

### T-064 Longitudinal Council and early-warning workflow
- deps: T-063, T-050 · spec: `docs/06 §2.2, §5.4`, `docs/08 §8.2`
- scope: agents Behaviour Trend, Cross-Data Investigator, Forecast & Scenario, Intervention Planner (prompts + tools); temporal pre-round; `EarlyWarningCase` Temporal workflow; DecisionRecord `case_type=EARLY_WARNING` with intervention actions (L1/L2) and no L3.
- acceptance: S8 record: state ELEVATED, p_late_30d in [0.25, 0.40] with interval, action = verification + officer outreach, no adverse action; harness golden passes.

### T-065 notification-service and Collections Workbench
- deps: T-064, T-046 · spec: `docs/08 §5`, `docs/09 §4`
- scope: templated messages (reminder cadence from policy), channel stubs (email/SMS/app → log + UI inbox), member-language field; collections priority list, member timeline with "why now", drafted outreach for approval, restructure options via affordability tool, outcome logging.
- acceptance: S8 outreach draft approved → `outreach.sent` event → member inbox shows message; reminder cadence fires for a schedule fixture.

---

## Phase P7 — Copilots, Member Assistant, Management Cockpit, Sandbox UI

### T-070 Officer Copilot "ask the file"
- deps: T-042, T-046 · spec: `docs/06 §2.3`, `docs/09 §3.5`
- scope: grounded Q&A over the current case (tools: case.get, evidence.search, timeline.get, policy.lookup); answers cite evidence ids; UI panel.
- acceptance: 20 golden questions ≥ 95 % grounded; a question outside scope returns a refusal with reason.

### T-071 Member Assistant
- deps: T-040, T-025 · spec: `docs/06 §2.3`, `docs/09 §5`
- scope: identity from token; tools `get_my_balance`, `get_my_next_payment`, `get_my_application`, `get_missing_documents`, `request_callback`; hardship/complaint/bereavement classifier → handoff task + `member.hardship_signal` event; never states a decision; conversation log.
- acceptance: S10 transcript: balance and payment answered from tools; job-loss message triggers handoff; attempt to ask "will I be approved" yields policy-safe answer.

### T-072 Manager Copilot and Management Cockpit
- deps: T-054, T-063 · spec: `docs/09 §7`
- scope: governed metrics API (`governance-service /metrics/*`), cockpit tiles (flow, risk, performance), natural-language question → metrics tool calls → narrated answer with the numbers from tools; model health and fairness panels.
- acceptance: "Why did approvals fall at Branch B?" answered using metrics tool outputs only; tile values equal API values.

### T-073 Policy Sandbox UI
- deps: T-013, T-072 · spec: `docs/09 §7.3`
- scope: choose product, edit weights/thresholds in a form, replay range, results (approval rate, exposure, projected delinquency, segments, case diffs), "adopt as new version" (two-approver stub).
- acceptance: S6 flow: raise Commitment 0.20→0.30 → replay → S1 score changes as expected; adoption creates version 2026.09.2 and S1 re-synthesis uses it.

---

## Phase P8 — Hardening, evaluation harness, observability, demo readiness

### T-080 Evaluation harness (golden + adversarial) and CI gate
- deps: T-050, T-064 · spec: `docs/12_EVALS_AND_TESTING.md`
- scope: `ai/evals/harness.py` replays golden snapshots through the committee (fake or real gateway), scores routing, factuality (claims ↔ evidence), policy citation accuracy, disagreement handling, latency, tokens; adversarial corpus (injection in payslip text, tampered images, contradictory docs, missing evidence, OOD applicant, drift with benign explanation); thresholds; `make harness` writes `ai/evals/reports/<ts>.md`; GitHub Actions/CI script runs it with the fake gateway.
- acceptance: `make harness` passes all 10 scenarios + adversarial thresholds with the fake gateway; with the real local model on the Spark, unsupported-claim rate ≤ 1 %.

### T-081 Observability dashboards and traces
- deps: T-045 · spec: `docs/13 §5`
- scope: OTel in all services; Grafana dashboards: committee run trace, tier latency, tokens per run, LLM errors, LMI nightly, ledger growth; Loki logs correlated by case_id.
- acceptance: open a committee run trace from a DecisionRecord id in Grafana; dashboards render with data after `make demo`.

### T-082 Demo scripts: reset, seed, warmup, demo_check
- deps: T-080 · spec: `docs/11_DEMO_SCENARIOS.md §3`
- scope: `scripts/reset_demo.sh` (drop → migrate → seed → index → warmup < 2 min excluding model load), `scripts/warmup_llm.sh`, `scripts/demo_check.sh` (reset → harness → print URLs and scenario ids), `make demo`.
- acceptance: `make demo` green twice consecutively on the Spark.

### T-083 LLM outage drill and fail-safe tests
- deps: T-045, T-052 · spec: `docs/13 §7`
- scope: integration test that stops the gateway (or sets provider `down`), submits S1 → deterministic path completes, route OFFICER_REVIEW with `narrative.status=DEGRADED`; risk-service down → manual review; document confidence low → verification task.
- acceptance: `uv run pytest -m failsafe` green.

### T-084 Security checks
- deps: T-042, T-052 · spec: `docs/13 §1–4`
- scope: tool-denial test; injection corpus; token replay; role escalation attempts via API; secrets scan (`gitleaks`), dependency audit (`pip-audit`, `npm audit`).
- acceptance: `make security` green.

### T-085 Documentation and run-book rehearsal package
- deps: T-082 · spec: `docs/11 §4`
- scope: `docs/DEMO_RUNBOOK.md` generated with exact URLs/ids; `docs/OPERATIONS.md` (start/stop/reset/rollback); recordings folder placeholder; final PROGRESS.md.
- acceptance: another engineer can run the 45-minute run-book from the doc alone (dry run recorded in PROGRESS.md).

---

## PROGRESS.md template

```
# Progress

| Task | Status | Date | Notes / deviations |
|---|---|---|---|
| T-001 | todo | | |
...

## Blocked
(none)

## ADRs written
(none)
```

Statuses: `todo` · `in_progress` · `done` · `blocked` · `skipped (reason)`.
