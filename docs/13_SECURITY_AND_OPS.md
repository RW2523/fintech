# 13 — Security, privacy, operations

## 1. Identity and authorisation (demo)
- `cio_common.auth`: HS256 JWTs (`JWT_SECRET`), claims `{sub, role, branch?, member_id?, exp}`; `/api/auth/dev-token {role, ...}` (disabled unless `CIO_ENV=demo`).
- RBAC via `require_role()`; ABAC via `scope_for(user)` → `{branches[], member_ids[], assigned_cases[]}` applied in queries.
- Authority matrix enforced in `decision-service` at decision time (never trust the UI).
- Two-person approvals for policy versions, autonomy settings, kill-switch clear: two distinct principals with owner roles; recorded in ledger.
- Service-to-service: internal network only; `X-Internal-Key` shared secret for internal endpoints (mTLS is a pilot item).

## 2. Data protection
- PII masking in `llm_gateway` before any provider call: names, id numbers, phones, emails, account numbers, addresses → `«TYPE_n»` tokens; reversible per request; masked field count logged.
- Tools mask outputs by purpose (`permitted_uses`); member-facing tools derive identity from the token.
- Documents in MinIO with per-bucket policies; presigned URLs expire in 10 min; page renders cached 1 h.
- Prompt/tool logs: only ids, hashes and token counts by default; raw prompts only with `CIO_DEBUG_PROMPTS=1` and never in `demo`.
- Synthetic data only; the seed script refuses to run if `CIO_ENV=prod`.

## 3. Agent safety
- Data-not-instruction wrapping (06 §3); injection classifier (a small regex + LLM `fast` check for instruction-like content; on hit: strip and record `INJECTION_DETECTED` finding); schema-bound tools; output policy screen (06 §9).
- Tool grants per agent version; every call audited; budgets per run; no agent has write tools except `evidence.request` (no side effects) and proposals.
- L2/L3 actions require tokens; tokens single-use, scoped, expiring, HMAC-signed with `TOKEN_SECRET`.

## 4. Execution safety
Saga steps with idempotency keys; compensation on failure; case-state re-check; kill-switch check; before/after state in audit; duplicate execute → no-op with same result.

## 5. Observability
OTel SDK in every service (`init_otel`): traces (HTTP, DB, tool calls, LLM calls with route/provider/tokens as attributes), metrics (per-tier latency, tokens per run, LLM errors, queue age, nightly duration), logs (JSON with trace_id/case_id/run_id). Grafana dashboards in `infra/observability/dashboards/`: *Committee run*, *Tier latency*, *LLM usage*, *LMI nightly*, *Ledger and audit growth*, *Service health*.

## 6. Compose operations
`make up/down/logs/ps`; healthchecks; restart policies `unless-stopped`; volumes `pg_data, minio_data, hf_cache, grafana_data`; backups: `scripts/backup.sh` (pg_dump + MinIO mirror) and `scripts/restore.sh`.
Runbooks (`docs/OPERATIONS.md`, written in T-085): start/stop, reset, model rollback (change `LLM_*_MODEL`/artifact version + restart), policy rollback (re-activate prior version), kill switch, adapter outage, event replay (`scripts/replay_events.sh --from`), ledger verification.

## 7. Fail-safe matrix (tested by `pytest -m failsafe`)
| Failure | Behaviour |
|---|---|
| LLM gateway down / provider 5xx | policy, models, workflow continue; committee returns partial record with `narrative.status=DEGRADED`; route ≥ OFFICER_REVIEW; never AUTONOMOUS |
| risk-service down | `MODEL_UNAVAILABLE` flag; tier STANDARD; route ≥ OFFICER_REVIEW; Credit Risk agent stance REVIEW |
| document extraction low confidence | verification task; DOC-04 → MORE_INFORMATION_REQUIRED |
| agent timeout / schema failure | DEGRADED opinion; disagreement computed without it; route ≥ OFFICER_REVIEW |
| core write fails | workflow pending; retry with same idempotency key; no duplicate activation |
| kill switch | all routes → OFFICER_REVIEW with reason; pending autonomous tokens revoked |
| Temporal down | API returns 503 for submit; queued submissions retried by client; no data loss |
