# 03 — Backbone contracts (normative)

All contracts are JSON Schema (draft 2020-12) files in `contracts/schemas/<name>.<major>.<minor>.json`.
`$id` = `https://cio.local/schemas/<name>/<major.minor>`. Every payload carries `"schema": "<name>/<major.minor>"`.
Generated types: `libs/cio_contracts` (Pydantic v2, `extra="forbid"`) and `apps/web/src/contracts` (zod).
Money = string decimal ("12500.00"). Timestamps = RFC 3339 UTC. IDs = ULID with prefix (`snap_`, `ev_`, `op_`, `dr_`, `hd_`, `act_`, `tok_`, `run_`, `case_`, `app_`, `doc_`, `calc_`, `mr_`).

## 1. CaseSnapshot 1.0

| Field | Type | Req | Notes |
|---|---|---|---|
| snapshot_id | id(snap) | ✓ | immutable |
| case_id | id(case) | ✓ | |
| case_type | enum ORIGINATION, SERVICING, EARLY_WARNING, COLLECTIONS | ✓ | |
| member_id | string | ✓ | core member id `M-######` |
| product_code | string | ✓ | `PF-STD`, `PF-SHARIAH` |
| requested_amount | money | ✓ (origination) | |
| tenor_months | int | ✓ (origination) | 6–120 |
| purpose | string | | |
| member_snapshot_ver | string | ✓ | projection version hash |
| document_bundle_ver | string | ✓ | hash of document ids+versions |
| feature_snapshot_id | id(fs) | ✓ | |
| policy_version | string | ✓ | `policy/PF-STD/2026.09.1` |
| dff_version | string | ✓ | |
| autonomy_version | string | ✓ | |
| model_versions | object {risk, fraud, delinquency, document_ai, embed} | ✓ | strings |
| evidence_index_ver | string | ✓ | |
| temporal_context_id | id(tc) | | EARLY_WARNING/SERVICING only |
| created_at | timestamp | ✓ | |
| created_by | ActorRef {actor_id, kind: USER|SYSTEM|WORKFLOW, role} | ✓ | |
| hash | string | ✓ | sha256 of canonical JSON excluding `hash` |

## 2. EvidenceRef 1.0

| Field | Type | Req | Notes |
|---|---|---|---|
| evidence_id | id(ev) | ✓ | |
| type | enum DOCUMENT_FIELD, CORE_FIELD, POLICY_RULE, MODEL_OUTPUT, ANALYTIC_RESULT, TIMELINE_EVENT, HUMAN_INPUT, RETRIEVED_CLAUSE | ✓ | |
| source_system | string | ✓ | e.g. `document-service`, `core.financing`, `policy-service` |
| source_record_id | string | ✓ | |
| locator | object {document_id?, page?, bbox?[x0,y0,x1,y1 normalised 0–1], field_path?, event_id?, rule_id?, clause_id?, model_run_id?, calc_id?} | ✓ | at least one key |
| value | any | | the value as observed |
| display | string | | human-readable rendering |
| confidence | number 0–1 | ✓ | |
| captured_at | timestamp | ✓ | |
| version | string | ✓ | |
| permitted_uses | array of enum UNDERWRITING, SERVICING, COLLECTIONS, FRAUD, ANALYTICS | ✓ | |

## 3. AgentOpinion 1.3

| Field | Type | Req | Notes |
|---|---|---|---|
| opinion_id | id(op) | ✓ | |
| committee_run_id | id(run) | ✓ | |
| snapshot_id | id(snap) | ✓ | |
| agent_id | string | ✓ | see `06_AGENT_RUNTIME.md §2` |
| agent_version | string | ✓ | hash of prompt+tools+schema+route |
| round | enum ASSESS, REVISE, CHALLENGE, TEMPORAL | ✓ | |
| stance | enum SUPPORT, LEAN_SUPPORT, REVIEW, LEAN_OPPOSE, OPPOSE, BLOCK, NEED_MORE_EVIDENCE | ✓ | Challenger uses REVIEW/NEED_MORE_EVIDENCE only |
| confidence | number 0–1 | ✓ | |
| factor_scores | map family → FactorScore | | only the owning agent's family; must equal tool output |
| reason_codes | array string | ✓ | from `contracts/reason_codes.yaml` |
| claims | array {text ≤ 400 chars, evidence_refs: array id(ev) minItems 1} | ✓ | |
| contradictions | array {text, evidence_refs} | ✓ (may be empty) | |
| unresolved | array {question, blocking: bool, requested_evidence?: string, requested_tool?: string} | ✓ | |
| proposed_actions | array ActionProposal | ✓ | |
| changed_from_prior | string | | REVISE only |
| tool_calls | array {tool, version, call_id, ok} | ✓ | recorded by runtime, not by model |
| signature | string | ✓ | HMAC-SHA256 over canonical JSON without `signature` |
| created_at | timestamp | ✓ | |

Validation rules (runtime, beyond schema): every `evidence_refs` id must be in the set returned by tools during this invocation; `factor_scores[family].score` must equal the tool's value; text fields must pass the output policy screen.

## 4. FactorScore 1.0

`{ family: enum CAPACITY|CONDUCT|COMMITMENT|CONDITIONS|INTEGRITY, score: int 0–100, calc_id: id(calc), tool: string, inputs_digest: string, evidence_refs: [id(ev)] }`

## 5. PolicyResult 1.0

```
{ policy_version, product_code, evaluated_at,
  rules: [ { rule_id, category: ELIGIBILITY|DOCUMENTS|AFFORDABILITY|EXPOSURE|AUTHORITY|ROUTING|SHARIAH,
             result: PASS|FAIL|EXCEPTION|NOT_APPLICABLE, on_fail?: string, reason_code?, evidence_refs[] } ],
  blockers: [ rule_id ],                       # FAIL with on_fail in {INELIGIBLE, BLOCK_NORMAL_PATH, MORE_INFORMATION_REQUIRED, POLICY_EXCEPTION_OR_DECLINE, COMPLIANCE_REVIEW}
  flags: [ string ],                           # e.g. THIN_HEADROOM
  affordability: { dsr, dsr_limit, headroom, stress: [{case, dsr, pass}], instalment, calc_id, evidence_refs[] },
  exposure: { current, requested, resulting, limit, calc_id },
  required_authority: role,
  routing_hint: null | COMPLIANCE_REVIEW | ENHANCED_ASSESSMENT | SENIOR_REVIEW,
  evidence_coverage: number 0–1 }
```

## 6. DecisionRecord 1.0

| Field | Type | Req | Notes |
|---|---|---|---|
| decision_record_id | id(dr) | ✓ | |
| committee_run_id | id(run) | ✓ | null for pure policy stops (still recorded) |
| snapshot_id | id(snap) | ✓ | |
| case_type | enum as CaseSnapshot | ✓ | |
| tier | enum FAST, STANDARD, EXTENDED, POLICY_ONLY | ✓ | |
| hard_gates | array {rule_id, result, evidence_refs} | ✓ | |
| evidence_coverage | number | ✓ | |
| factor_scores | map family → {score, weight, weighted, decisive: bool, calc_id} | ✓ (may be empty when gated) | |
| weighted_score | number 0–100 or null | ✓ | |
| recommendation | enum APPROVE, DECLINE, REVIEW, MORE_INFORMATION_REQUIRED, ENHANCED_ASSESSMENT, COMPLIANCE_REVIEW, INTERVENE, MONITOR, DE_ESCALATE | ✓ | last three for EARLY_WARNING |
| confidence | number 0–1 | ✓ | |
| disagreement | number 0–1 | ✓ | |
| challenger_open | bool | ✓ | |
| route | enum AUTONOMOUS, OFFICER_REVIEW, SENIOR_REVIEW, ENHANCED_ASSESSMENT, COMMITTEE, COMPLIANCE, MANUAL_FALLBACK | ✓ | |
| route_reasons | array string | ✓ | e.g. `KILL_SWITCH`, `CHALLENGER_OPEN`, `AMOUNT_BAND`, `MODEL_HEALTH_AMBER` |
| required_authority | role | ✓ | |
| narrative | {member: {text, status}, officer: {...}, auditor: {...}} status ∈ OK, DEGRADED, NONE | ✓ | generated last; regenerable |
| would_change_outcome | array {condition, new_recommendation} | ✓ | |
| proposed_actions | array ActionProposal | ✓ | |
| opinions | array id(op) | ✓ | ids; full opinions in committee run |
| policy_version, dff_version, autonomy_version | strings | ✓ | |
| model_versions | object | ✓ | |
| budgets | {tokens_used, seconds_used, tier_budget_tokens, tier_budget_seconds, exceeded: bool} | ✓ | |
| created_at | timestamp | ✓ | |
| hash, prev_hash | string | ✓ | ledger chain |

## 7. HumanDecision 1.0

`{ human_decision_id, decision_record_id, case_id, actor_id, authority_role, final_action: APPROVE|DECLINE|REQUEST_INFO|ESCALATE|DEFER|APPROVE_WITH_CONDITIONS, conditions[], override: bool, override_reason: {code: OVR-01..OVR-12, text ≥ 20 chars} (required iff override), evidence_acknowledged[], decided_at, hash, prev_hash }`

Override reason codes (`contracts/reason_codes.yaml`, group OVR): OVR-01 additional evidence obtained · OVR-02 policy exception approved · OVR-03 known member circumstances · OVR-04 data error suspected · OVR-05 model reservation · OVR-06 hardship consideration · OVR-07 fraud concern · OVR-08 documentation concern · OVR-09 relationship/commercial · OVR-10 regulatory/compliance · OVR-11 committee direction · OVR-12 other (text mandatory ≥ 60 chars).

## 8. ActionProposal 1.0 and ApprovalToken 1.0

```
ActionProposal { action_id, level: L0|L1|L2|L3,
  type: REQUEST_DOCUMENT|SEND_REMINDER|CREATE_NOTE|CREATE_TASK|ESCALATE|ASSIGN_REVIEWER|PROPOSE_VERIFICATION|
        OFFICER_OUTREACH|HARDSHIP_REVIEW|APPROVE_FINANCING|DECLINE_FINANCING|RESTRUCTURE|LIMIT_CHANGE|MONITOR,
  parameters: object (typed per type), rationale: {text, evidence_refs[]},
  requires: AUTO|AUTO_IF_POLICY|OFFICER|SENIOR|COMMITTEE|PROHIBITED, proposed_by: agent_id|"policy", state: PROPOSED|APPROVED|EXECUTED|REJECTED|EXPIRED }

ApprovalToken { token_id, action_id, decision_record_id, human_decision_id|null,
  issued_to: ActorRef | {kind:"AUTONOMY_DIAL", autonomy_version},
  scope: { product_code, member_id, case_id, max_amount },
  idempotency_key, issued_at, expires_at (≤ 24h), used_at|null, signature }
```

## 9. MemberEvent 1.0

`{ event_id, member_id, account_id|null, occurred_at, ingested_at, event_type: <enum below>, source_system, source_record_id, payload: object (typed per event_type), data_quality: {freshness_s, confidence, validation: PASS|WARN|FAIL}, permitted_uses[], evidence_refs[] }`

event_type: PAYMENT_DUE, PAYMENT_RECEIVED, PAYMENT_LATE, PAYMENT_PARTIAL, PAYMENT_REVERSED, DEDUCTION_RECEIVED,
DEDUCTION_MISSED, SAVINGS_BALANCE, SHARE_CAPITAL, RESTRUCTURE, LIMIT_CHANGE, APPLICATION, DOCUMENT_VERIFIED,
CONTACT_DELIVERED, CONTACTED, PROMISE_TO_PAY, HARDSHIP_REQUEST, ARRANGEMENT_ACTIVE, ARRANGEMENT_ENDED,
OUTAGE_WINDOW, STATE_CHANGED, FORECAST_CREATED, HUMAN_DECISION, EMPLOYMENT_CHANGE.

Payload shapes (minimum): PAYMENT_* `{schedule_id, due_date, amount_due, amount_paid?, paid_at?, days_late?}`; DEDUCTION_* `{employer_id, cycle, expected_amount, received_amount?}`; SAVINGS_BALANCE `{balance}`; SHARE_CAPITAL `{units, value}`; OUTAGE_WINDOW `{system, from, to}`; ARRANGEMENT_* `{arrangement_id, type, from, to}`; CONTACT*/PROMISE `{channel, template_id?, outcome?, promise_date?, amount?}`.

## 10. CommitteeRun 1.0

`{ run_id, snapshot_id, case_type, tier, state: CREATED|ASSESS|CHALLENGE|REPAIR|REVISE|SYNTHESIZE|DONE|TIMED_OUT|FAILED, rounds: [{round, started_at, ended_at, opinions: [op ids], tokens, seconds}], repair_loops: int, budgets, decision_record_id|null, started_at, ended_at }`

## 11. Reason-code vocabulary (`contracts/reason_codes.yaml`)

Groups and codes (text is the approved officer wording; member wording is a separate field):

- ELG: ELG-01 membership inactive · ELG-02 tenure below minimum · ELG-03 identity not verified · ELG-04 age/eligibility rule · ELG-05 product not available to member class
- DOC: DOC-01 required document missing · DOC-02 document unreadable · DOC-03 document expired · DOC-04 low-confidence critical field
- CAP: CAP-01 commitment ratio within policy · CAP-02 commitment ratio above limit · CAP-03 thin headroom under stress · CAP-04 income not verified · CAP-05 income stable · CAP-06 income variable
- CON: CON-01 strong on-time history · CON-02 recent arrears · CON-03 prior restructure · CON-04 recent new facility · CON-05 limited history · CON-06 bureau adverse
- COM: COM-01 long tenure · COM-02 consistent savings · COM-03 share capital above minimum · COM-04 savings paused · COM-05 prior hardship arrangement · COM-06 new member
- INT: INT-01 document metadata inconsistency · INT-02 image reused · INT-03 income variance across sources · INT-04 duplicate application · INT-05 guarantor network finding · INT-06 contact change before application · INT-07 template mismatch · INT-08 identity mismatch (critical)
- CND: CND-01 employer concentration high · CND-02 sector stress · CND-03 portfolio within tolerance
- EXP: EXP-01 exposure within limit · EXP-02 exposure above limit
- AUT: AUT-01 within officer authority · AUT-02 senior authority required · AUT-03 committee authority required
- SHR: SHR-01 purpose permitted · SHR-02 purpose not permitted · SHR-03 structure compliant
- LMI: LMI-01 payment timing drift · LMI-02 deduction interrupted · LMI-03 savings decline · LMI-04 corroborated by second family · LMI-05 outage explains signal · LMI-06 arrangement active · LMI-07 recovery observed · LMI-08 forecast elevated · LMI-09 forecast uncertain
- OVR: as §7.

## 12. Event catalogue (`contracts/events.yaml`)

Each event: `name.vN`, key (partition key), payload schema ref, producer, consumers. Names:

application.created.v1, application.submitted.v1 (key case_id: {snapshot_id, product_code, member_id}),
document.uploaded.v1, document.classified.v1, document.extracted.v1, document.exception_detected.v1,
policy.evaluated.v1, features.snapshot_created.v1, risk.scored.v1, fraud.assessed.v1,
committee.started.v1, committee.opinion_published.v1, committee.recommendation_created.v1, committee.timed_out.v1,
human.decision_recorded.v1, action.proposed.v1, action.approved.v1, action.executed.v1, action.failed.v1,
core.financing_activated.v1, payment.due.v1, payment.received.v1, payment.late.v1, payment.partial.v1, payment.reversed.v1,
deduction.received.v1, deduction.missed.v1, member.baseline_updated.v1, behaviour.change_point_detected.v1,
forecast.generated.v1, member.state_changed.v1, early_warning.case_created.v1, early_warning.case_closed.v1,
intervention.proposed.v1, intervention.approved.v1, intervention.completed.v1, outreach.sent.v1, outreach.response_logged.v1,
member.hardship_signal.v1, outcome.recorded.v1, model.deployed.v1, model.monitor_alert.v1,
autonomy.setting_changed.v1, kill_switch.activated.v1, kill_switch.cleared.v1, ledger.appended.v1, audit.exported.v1.

Envelope: `{ event_id, name, version, key, trace_id, case_id?, occurred_at, producer, payload }`.
