# 08 — APIs, events and workflows

All APIs are JSON over HTTP behind `services/gateway` at `/api/<service>/…`. Auth: `Authorization: Bearer <jwt>`
(roles in §10). Every response includes `X-Trace-Id`. Errors: `{error:{code, message, details}}` with codes
`VALIDATION`, `NOT_FOUND`, `FORBIDDEN`, `CONFLICT`, `LLM_UNAVAILABLE`, `POLICY_BLOCKED`, `TOKEN_INVALID`, `KILL_SWITCH`.
OpenAPI is generated per service and aggregated at `/api/openapi.json`.

## 1. Applications and cases (`application`)
| Method | Path | Body → Response | Roles |
|---|---|---|---|
| POST | /applications | `{member_id, product_code, amount, tenor_months, purpose}` → `{application_id, case_id, status}` | officer, member (own) |
| POST | /applications/{id}/submit | — → `{snapshot_id, workflow_id}` (freezes CaseSnapshot, starts `UnderwriteCase`) | officer, member (own) |
| GET | /applications/{id} | → application + case + current snapshot summary + decision status | officer, member (own) |
| GET | /cases | `?state&route&assigned_to&branch` → queue rows `{case_id, member_token, product, amount, tier, route, recommendation, confidence, sla_due}` | officer, senior_officer, compliance |
| GET | /cases/{id} | → full case view: snapshot, policy result, decision record, human decisions, actions, documents, findings | officer+, compliance |
| POST | /cases/{id}/assign | `{assignee}` | senior_officer |

## 2. Documents (`document`)
POST `/cases/{id}/documents` `{type?, filename, mime}` → `{document_id, upload_url}` · POST `/documents/{id}/complete` → processing started ·
GET `/documents/{id}` → metadata, pages, classification, status · GET `/documents/{id}/extraction` → fields with bbox/evidence ·
GET `/documents/{id}/page/{n}.png` → rendered page · POST `/documents/{id}/review` `{field, value, note}` → creates HUMAN_INPUT evidence, re-runs reconciliation · GET `/cases/{id}/findings`.

## 3. Members (`member_intelligence`)
GET `/members/{id}/profile` · GET `/members/{id}/timeline?from&to&types&cursor` · GET `/members/{id}/state` · POST `/members/import` (seed/CDC) ·
GET `/members/{id}/history` (the `history.get` tool payload) · GET `/members/{id}/savings`, `/shares`, `/interactions`, `/hardship`.
ABAC: officers see members in their branch or assigned cases; members see only themselves (`member_id` from token).

## 4. Policy (`policy`)
POST `/policy/evaluate {snapshot_id}` → PolicyResult · POST `/policy/affordability {snapshot_id, overrides?}` · POST `/policy/factors/score {snapshot_id, family, inputs}` → FactorScore ·
POST `/policy/synthesize {snapshot_id, policy_result, factor_scores[], opinions[], model_health}` → DecisionRecord (no narratives) ·
POST `/policy/route {decision_record}` → `{route, route_reasons, sampled}` · GET `/policy/{product}/{version}` · GET `/policy/{product}/versions` ·
POST `/policy/{product}/versions` (two approvers) · POST `/policy/sandbox/replay` (05 §7) · GET `/policy/sandbox/{id}` · GET `/policy/actions/allowed?state&product&case_type`.

## 5. Models (`risk`, `fraud`, `lmi`, `feature`) and notifications
POST `/risk/score {snapshot_id}` · POST `/fraud/assess {snapshot_id}` · GET `/fraud/signals/{case_id}` · GET `/fraud/graph/{case_id}` ·
POST `/features/snapshot {member_id, account_id?, as_of?}` · GET `/features/{snapshot_id}` · POST `/features/nightly` ·
POST `/lmi/score {member_id, account_id}` · GET `/lmi/state/{member_id}` · GET `/lmi/alerts?officer&state` · POST `/lmi/nightly` ·
POST `/messages {member_id, template_id, channel, variables, approval_ref?}` · GET `/inbox/{member_id}` · GET `/messages?case_id`.
Each model service: GET `/version` → `{model, version, trained_at, card_url}`.

## 6. Committee, decisions, governance
POST `/committee/runs {snapshot_id, tier?}` → `{run_id}` (202; idempotent on snapshot+tier) · GET `/committee/runs/{id}` → CommitteeRun ·
GET `/committee/runs/{id}/opinions` · GET `/committee/runs/{id}/trace` (span ids) ·
POST `/recommendations` (internal) · GET `/decision-records/{id}` · POST `/human-decisions` HumanDecision (validates authority; 409 if record superseded) ·
GET `/ledger?case_id` · GET `/ledger/verify?from&to` · POST `/tokens` (internal) ·
GET `/governance/models` · GET `/governance/fairness` · GET `/governance/overrides?range` · GET `/metrics/{name}?dims&range` ·
POST `/autonomy/{product}` `{setting, bands?, conditions?, approvers:[a,b]}` (two distinct roles in `HEAD_OF_CREDIT|HEAD_OF_RISK`) · GET `/autonomy/{product}` ·
POST `/kill-switch/{product}` `{reason}` (owner role) · DELETE `/kill-switch/{product}` · GET `/samples?role` · POST `/samples/{id}/review`.

## 7. Actions and execution
POST `/action-proposals` (internal from committee/agents) · POST `/actions/{id}/approve {human_decision_id?}` → issues ApprovalToken (decision-service) ·
POST `/actions/{id}/execute {token_id}` (execution-service): validates signature/scope/expiry/single-use/idempotency/case-state/kill-switch → runs saga:
`APPROVE_FINANCING`: core `/core/write/activate` (creates account + schedule) then `/core/write/status(APPROVED)`; compensation: `/core/write/status(ROLLBACK)`.
`REQUEST_DOCUMENT`/`SEND_REMINDER`/`CREATE_TASK`: notification/task side effects (L1). Returns `{action_id, state, core_refs[]}`; audit before/after.

## 8. Workflows (Temporal, `workflows/`)

### 8.1 `UnderwriteCase(case_id)` — task queue `cio-underwriting`
```
freeze = act.freeze_snapshot(case_id)                                   # idempotent on case version
docs, feats = parallel(act.process_documents(snap), act.feature_snapshot(snap))    # retries 3, backoff; low-confidence → child task wait (human review signal, 48h)
pr = act.policy_evaluate(snap)
if pr.blockers: rec = act.record_policy_stop(snap, pr); route = act.route(rec); goto human_or_done
risk, fraud = parallel(act.risk_score(snap), act.fraud_assess(snap))    # failure of either → pr.flag MODEL_UNAVAILABLE, tier forced STANDARD, route ≠ AUTONOMOUS
tier = act.select_tier(snap, pr, risk, fraud)
run = child(CommitteeRun(snap, tier))                                   # timeout = tier budget + 30 s; on timeout → partial record
rec = act.decision_record(run)
route = act.route(rec)
if route == AUTONOMOUS: token = act.issue_token("autonomy_dial", rec)
else: hd = wait_signal("human_decision", timeout=sla(route))             # on timeout → escalate task, keep waiting
      token = act.issue_token(hd, rec) if hd.final_action in (APPROVE, APPROVE_WITH_CONDITIONS) else None
if token: act.execute(rec.actions, token)                               # saga
act.ledger_append_all(); act.emit_outcome_hooks()
```

### 8.2 `EarlyWarningCase(member_id, account_id, alert_id)` — task queue `cio-lmi`
freeze snapshot (case_type EARLY_WARNING, temporal_context) → TEMPORAL round (behaviour_trend, cross_data_investigator, forecast_scenario in parallel) → Council ASSESS (credit_risk, fraud_integrity, policy_affordability with actions.allowed, member_relationship) → CHALLENGE → intervention_planner → synthesize (recommendation ∈ INTERVENE/MONITOR/DE_ESCALATE) → route (L3 prohibited) → human approval of L2 actions (signal) / auto L1 if setting ≠ ADVISE → notifications → re-run on `member.state_changed` or after 7 days (continue-as-new) → close on RECOVERY→STABLE.

### 8.3 `LmiNightly(date)` — features nightly → change-points → forecasts → state machine → alerts → open EarlyWarningCase per new ELEVATED. Chunked by member id ranges; idempotent per date.

### 8.4 `ReminderSchedule(account_id)` — timers at cadence offsets; cancel on payment; DEDUCTION_MISSED handler.

## 9. Core stub API (`core_stub`)
GET `/core/members/{id}`, `/core/members/{id}/accounts`, `/core/accounts/{id}/schedule`, `/core/accounts/{id}/payments`, `/core/members/{id}/deductions`, `/core/members/{id}/savings`, `/core/members/{id}/shares`, `/core/members/{id}/guarantors`, `/core/bureau/{id}` (synthetic bureau), `/core/employers/{id}`, `/core/outages`, `/core/arrangements?member_id` ·
GET `/core/changes?since=<seq>` → change feed · POST `/core/write/activate` `{member_id, product_code, amount, tenor, instalment, idempotency_key}` (header `X-Approval-Token`) · POST `/core/write/status` · POST `/core/admin/reset`, `/core/admin/bulk` (seed only).

## 10. Roles and authority
`officer` (CREDIT_OFFICER) · `senior_officer` (SENIOR_OFFICER) · `committee` (CREDIT_COMMITTEE) · `collections` · `manager` · `compliance` ·
`head_of_credit`, `head_of_risk` (kill switch / autonomy owners) · `member` · `system`. Authority matrix lives in `policy.yaml authority.bands`; decision-service enforces at call time.

## 11. Events → consumers (summary)
application.submitted → workflow start · document.* → case view refresh · committee.recommendation_created → queue, notifications (officer) · human.decision_recorded → action approval, ledger ·
action.executed → core.financing_activated → ReminderSchedule, member_event append · payment.* / deduction.* → feature refresh (event-triggered), ReminderSchedule cancel ·
behaviour.change_point_detected / member.state_changed → alerts, EarlyWarningCase · outreach.* → interactions features · outcome.recorded → labels, governance monitoring ·
autonomy.setting_changed / kill_switch.* → policy cache invalidation, audit · model.monitor_alert → model_health for routing.
