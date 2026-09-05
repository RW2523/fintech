# 05 — Policy packs, Decision Factor Framework, Synthesizer, Autonomy Dial

A policy pack is three YAML files per product per version under `policy_packs/<PRODUCT>/<version>/`:
`policy.yaml` (gates, calculations, authority, routing), `dff.yaml` (weights, thresholds), `autonomy.yaml`
(dial). Packs are loaded, schema-validated, semantically checked and stored in `app_policy.policy_version`.
The version string used everywhere is `policy/<PRODUCT>/<version>` (and `dff/…`, `autonomy/…`).

## 1. Rule expression language

Rules are boolean expressions over a **flat input context** built by policy-service from the CaseSnapshot,
the member profile, document extraction results, feature snapshot and model outputs. Grammar (implement
with a small safe evaluator; no `eval`): literals, identifiers with dots, comparison `== != < <= > >=`,
`and or not`, arithmetic `+ - * /`, functions `min max abs len coalesce(a,b) days_between(d1,d2) in(x,[…]) all(list, expr) any(list, expr)`.
Every rule declares `id`, `category`, `rule`, `on_fail` (or `on_pass` for flags), `reason_code`, and the
context keys it reads (so the engine can emit `EvidenceRef`s for exactly those inputs).

Context keys (minimum): `member.status member.tenure_months member.age member.class member.total_exposure member.grade
identity.verified identity.mismatch documents.required_complete documents.<TYPE>.present documents.<TYPE>.confidence
income.verified_monthly income.stability income.source_variance commitments.monthly proposed.instalment requested.amount requested.tenor requested.purpose
fraud.level fraud.integrity_score history.arrears_12m history.restructures product.* actor.role actor.max_amount`.

## 2. `policy.yaml` — PF-STD 2026.09.1 (complete)

```yaml
schema: policy/1.0
product: PF-STD
name: Personal Financing — Standard
version: 2026.09.1
effective_from: 2026-09-01
currency: LCU
product:
  min_amount: 1000
  max_amount: 150000
  min_tenor: 6
  max_tenor: 84
  profit_rate: 0.065            # flat annual for instalment calc in the stub
  instalment_formula: "amount * (1 + profit_rate * tenor / 12) / tenor"
  purposes_allowed: [PERSONAL, EDUCATION, MEDICAL, HOME_IMPROVEMENT, DEBT_CONSOLIDATION, VEHICLE, OTHER]
eligibility:
  - { id: ELG-01, rule: "member.status == 'ACTIVE'",            on_fail: INELIGIBLE,            reason_code: ELG-01, reads: [member.status] }
  - { id: ELG-02, rule: "member.tenure_months >= 6",           on_fail: INELIGIBLE,            reason_code: ELG-02, reads: [member.tenure_months] }
  - { id: ELG-03, rule: "identity.verified == true",           on_fail: BLOCK_NORMAL_PATH,     reason_code: ELG-03, reads: [identity.verified] }
  - { id: ELG-04, rule: "member.age >= 18 and member.age + requested.tenor/12 <= 65", on_fail: INELIGIBLE, reason_code: ELG-04, reads: [member.age, requested.tenor] }
  - { id: ELG-05, rule: "requested.amount >= product.min_amount and requested.amount <= product.max_amount and requested.tenor >= product.min_tenor and requested.tenor <= product.max_tenor", on_fail: INELIGIBLE, reason_code: ELG-05, reads: [requested.amount, requested.tenor] }
documents:
  required: [IDENTITY, PAYSLIP_LATEST_3, EMPLOYMENT_CONFIRMATION]
  optional: [BANK_STATEMENT_3M, PROVIDENT_FUND_STATEMENT]
  critical_fields: { PAYSLIP_LATEST_3: [net_salary, gross_salary, employer_name, period], IDENTITY: [id_number, name, dob] }
  min_critical_confidence: 0.85
  - { id: DOC-01, rule: "documents.required_complete == true",                  on_fail: MORE_INFORMATION_REQUIRED, reason_code: DOC-01, reads: [documents.required_complete] }
  - { id: DOC-04, rule: "documents.min_critical_confidence >= 0.85",            on_fail: MORE_INFORMATION_REQUIRED, reason_code: DOC-04, reads: [documents.min_critical_confidence] }
affordability:
  income_basis: "income.verified_monthly"          # min(payslip net median 3m, deduction-record net) when both exist
  dsr: "(commitments.monthly + proposed.instalment) / income.verified_monthly"
  dsr_limit: 0.60
  residual_income_min: 800
  stress:
    - { case: income-10%,  income_factor: 0.90 }
    - { case: rate+2%,     profit_rate_delta: 0.02 }
    - { case: commit+10%,  commitments_factor: 1.10 }
  - { id: AFF-01, rule: "affordability.dsr <= affordability.dsr_limit",                        on_fail: POLICY_EXCEPTION_OR_DECLINE, reason_code: CAP-02, reads: [affordability.dsr] }
  - { id: AFF-02, rule: "all(affordability.stress, dsr <= affordability.dsr_limit + 0.05)",   on_fail: "FLAG:THIN_HEADROOM",        reason_code: CAP-03, reads: [affordability.stress] }
  - { id: AFF-03, rule: "income.verified_monthly - commitments.monthly - proposed.instalment >= affordability.residual_income_min", on_fail: POLICY_EXCEPTION_OR_DECLINE, reason_code: CAP-02, reads: [income.verified_monthly, commitments.monthly, proposed.instalment] }
  - { id: AFF-04, rule: "income.verified == true",                                             on_fail: MORE_INFORMATION_REQUIRED,   reason_code: CAP-04, reads: [income.verified] }
exposure:
  limit_by_grade: { A: 200000, B: 150000, C: 100000, D: 60000, E: 30000 }
  - { id: EXP-01, rule: "member.total_exposure + requested.amount <= exposure.limit", on_fail: POLICY_EXCEPTION_OR_DECLINE, reason_code: EXP-02, reads: [member.total_exposure, requested.amount, member.grade] }
authority:
  bands:
    - { max_amount: 20000,  role: CREDIT_OFFICER }
    - { max_amount: 75000,  role: SENIOR_OFFICER }
    - { max_amount: null,   role: CREDIT_COMMITTEE }
  exception_approver: SENIOR_OFFICER         # who may approve a POLICY_EXCEPTION
routing:
  - { id: RT-01, when: "fraud.level == 'HIGH'",           route: COMPLIANCE_REVIEW }
  - { id: RT-02, when: "fraud.level == 'CRITICAL'",       route: COMPLIANCE_REVIEW, on_fail: BLOCK_NORMAL_PATH }
  - { id: RT-03, when: "identity.mismatch == 'CRITICAL'", route: ENHANCED_ASSESSMENT }
  - { id: RT-04, when: "history.arrears_12m >= 2",        route: ENHANCED_ASSESSMENT }
  - { id: RT-05, when: "requested.amount > actor.max_amount", route: escalate }
tier_selection:
  fast_path_max_amount: 10000
  fast_path_requires: { clean_12m: true, no_findings: true, all_gates_pass: true }
  extended_triggers: { top_band: true, fraud_level_min: MEDIUM, identity_mismatch: true, active_hardship: true, disagreement_min: 0.35, contradiction: true }
reminders:                                    # payment management cadence (days relative to due date)
  cadence: [-14, -7, -3, 0, +1]
  priority_if: "member.state in ['WATCH','ELEVATED'] or history.late_12m >= 1"
collections:
  tiers: { P1: "p_default_90d >= 0.60 or state == 'CRITICAL'", P2: "state == 'ELEVATED'", P3: "days_late between 1 and 7 and state == 'STABLE'" }
  restructure_options: [DEFERMENT_1M, DEFERMENT_2M, RESCHEDULE_EXTEND_12M, RESCHEDULE_EXTEND_24M]
  restructure_authority: SENIOR_OFFICER
```

### 2.1 PF-SHARIAH differences

`product.profit_rate` → `profit_rate` on a Murabahah-style cost-plus schedule (`instalment_formula` uses a
fixed selling price); `purposes_allowed` excludes non-permissible purposes; extra category `shariah`:

```yaml
shariah:
  - { id: SHR-01, rule: "in(requested.purpose, product.purposes_allowed)", on_fail: INELIGIBLE, reason_code: SHR-02, reads: [requested.purpose] }
  - { id: SHR-03, rule: "product.structure == 'MURABAHAH'",               on_fail: BLOCK_NORMAL_PATH, reason_code: SHR-03, reads: [product.structure] }
  - { id: SHR-04, rule: "restructure.type != 'RATE_INCREASE'",             on_fail: INELIGIBLE, reason_code: SHR-03, reads: [restructure.type] }
restructure_options: [DEFERMENT_1M, DEFERMENT_2M, RESCHEDULE_EXTEND_12M]   # no rate increase
```

## 3. Evaluation semantics (policy-service)

1. Build the context; attach an `EvidenceRef` per context key used (CORE_FIELD / DOCUMENT_FIELD / MODEL_OUTPUT / ANALYTIC_RESULT).
2. Evaluate categories in order ELIGIBILITY → DOCUMENTS → SHARIAH → AFFORDABILITY → EXPOSURE → AUTHORITY → ROUTING.
3. `blockers` = ids of failed rules whose `on_fail` ∈ {INELIGIBLE, BLOCK_NORMAL_PATH, MORE_INFORMATION_REQUIRED, POLICY_EXCEPTION_OR_DECLINE, COMPLIANCE_REVIEW}. `FLAG:*` becomes a flag, not a blocker.
4. `required_authority` = smallest band whose `max_amount ≥ requested.amount` (null band = committee); if any POLICY_EXCEPTION blocker exists, `required_authority = max(required_authority, exception_approver)`.
5. `evidence_coverage` = (required documents present with critical fields ≥ min confidence + core fields resolved) / (required documents + core fields) — computed over the `reads` union of all rules.
6. Route for a blocked case: INELIGIBLE → recommendation DECLINE, route OFFICER_REVIEW (officer confirms); BLOCK_NORMAL_PATH/COMPLIANCE_REVIEW → COMPLIANCE; MORE_INFORMATION_REQUIRED → OFFICER_REVIEW with actions REQUEST_DOCUMENT; POLICY_EXCEPTION_OR_DECLINE → recommendation REVIEW, route SENIOR_REVIEW.
7. `affordability.compute` records a `calc` row and returns `calc_id`; every number in `PolicyResult.affordability` comes from it.

## 4. `dff.yaml` — Decision Factor Framework

```yaml
schema: dff/1.0
product: PF-STD
version: 2026.09.1
weights: { CAPACITY: 0.35, CONDUCT: 0.30, COMMITMENT: 0.20, CONDITIONS: 0.10, INTEGRITY: 0.05 }   # must sum to 1.0
thresholds: { approve: 70, decline: 45 }           # weighted_score >= approve → APPROVE; < decline → DECLINE; else REVIEW
min_evidence_coverage: 0.90
hard_gate_families: [COMPLIANCE]                   # never weighted
integrity_critical_is_gate: true
scoring:
  CAPACITY:
    tool: affordability.compute
    formula: |
      base = 100 - 120 * max(0, dsr - 0.35)
      base -= 10 * count(stress where not pass)
      base -= 10 if residual_income < residual_income_min * 1.25
      score = clamp(round(base), 0, 100)
  CONDUCT:
    tool: risk.score                                  # returns conduct_score computed by the scorecard component below
    formula: |
      s = 40 * ontime_rate_24m                       # 0..40
      s += min(25, months_since_last_arrears / 24 * 25) if any_arrears else 25
      s -= 15 * restructures_36m
      s += {A:20, B:16, C:12, D:6, E:0}[bureau_or_internal_grade]
      s = 55 if history_months < 6 (limited history) else s
      score = clamp(round(s), 0, 100)
  COMMITMENT:
    tool: member.commitment_score
    formula: |
      s = min(30, tenure_years * 3)
      s += min(40, 40 * savings_balance / (12 * proposed_instalment))
      s += 30 if share_capital >= product_min_share else 30 * share_capital / product_min_share
      s -= 10 if savings_paused_months >= 3
      score = clamp(round(s), 0, 100)
  INTEGRITY:
    tool: fraud.assess
    formula: |
      score = clamp(100 - sum(severity_points(f) for f in open_findings), 0, 100)   # LOW 5, MEDIUM 20, HIGH 45; CRITICAL → hard gate
  CONDITIONS:
    tool: portfolio.conditions
    formula: |
      s = 100
      s -= 20 if employer_share_of_portfolio > 0.08 else 10 if > 0.05 else 0
      s -= 15 if sector_stress_flag else 0
      score = clamp(round(s), 0, 100)
decisive_rule: "family with the largest |weighted_contribution - mean_contribution|"
reliability_weights: { document_evidence: 1.0, policy_affordability: 1.0, credit_risk: 1.0, fraud_integrity: 1.0, member_relationship: 1.0, challenger: 1.0 }   # re-estimated quarterly from the ledger; used for disagreement only
```

## 5. Synthesizer (deterministic; `POST /policy/synthesize`)

Inputs: `PolicyResult`, `FactorScore[]` (from owning agents' tool calls), `AgentOpinion[]`, `dff`, `autonomy`, `snapshot`, `model_health`.

```python
STANCE = {"SUPPORT": 1.0, "LEAN_SUPPORT": 0.5, "REVIEW": 0.0, "LEAN_OPPOSE": -0.5, "OPPOSE": -1.0}


def synthesize(pr, factors, opinions, dff, autonomy, snap, model_health) -> DecisionRecord:
    rec = DecisionRecord.skeleton(snap, pr)
    # 1. Hard gates
    if pr.blockers:
        rec.recommendation, rec.route = route_for_blockers(pr)  # §3.6
        rec.route_reasons.append("HARD_GATE:" + ",".join(pr.blockers))
        return finalize(rec, confidence=None, disagreement=None)
    if any(f.family == "INTEGRITY" and f.level == "CRITICAL" for f in factors):
        rec.recommendation, rec.route = "COMPLIANCE_REVIEW", "COMPLIANCE"
        return finalize(rec)
    # 2. Evidence validity
    blocking = [u for o in opinions for u in o.unresolved if u.blocking]
    if blocking or pr.evidence_coverage < dff.min_evidence_coverage:
        rec.recommendation, rec.route = "MORE_INFORMATION_REQUIRED", "OFFICER_REVIEW"
        rec.proposed_actions += request_actions(blocking)
        return finalize(rec)
    # 3. Authority attached (never bypassed)
    rec.required_authority = pr.required_authority
    # 4. Weighted score
    ws = sum(dff.weights[f.family] * f.score for f in factors) / sum(dff.weights.values())
    rec.factor_scores = {
        f.family: dict(
            score=f.score,
            weight=dff.weights[f.family],
            weighted=dff.weights[f.family] * f.score,
            calc_id=f.calc_id,
        )
        for f in factors
    }
    rec.weighted_score = round(ws, 1)
    rec.recommendation = (
        "APPROVE" if ws >= dff.thresholds.approve else "DECLINE" if ws < dff.thresholds.decline else "REVIEW"
    )
    mark_decisive(rec.factor_scores)
    # 5. Confidence
    agent_conf = mean(o.confidence for o in opinions if o.agent_id != "challenger")
    rec.confidence = round(
        gmean([pr.evidence_coverage, model_reliability(snap.model_versions, model_health), agent_conf]), 3
    )
    # 6. Disagreement (reliability-weighted std-dev of stance values; BLOCK/NEED_MORE_EVIDENCE excluded)
    pts = [
        (STANCE[o.stance], dff.reliability_weights[o.agent_id])
        for o in opinions
        if o.stance in STANCE and o.agent_id != "challenger"
    ]
    rec.disagreement = round(weighted_std(pts), 3)
    rec.challenger_open = any(o.agent_id == "challenger" and o.unresolved for o in opinions)
    # 7. Route
    rec.route, rec.route_reasons = route(autonomy, rec, snap, model_health)  # §6
    rec.would_change_outcome = counterfactuals(pr, factors, dff, rec)  # §5.1
    return finalize(rec)
```

`model_reliability` = 1.0 when all model monitors GREEN, 0.85 AMBER, 0.6 RED (RED also forces route ≠ AUTONOMOUS).
`weighted_std` = sqrt(Σw(s−s̄)²/Σw). Thresholds: disagreement > 0.35 → route at least OFFICER_REVIEW; > 0.60 → ENHANCED_ASSESSMENT with the disagreement summary first.

### 5.1 Counterfactuals (`would_change_outcome`)

Deterministic probes on the weighted score and gates: for each family, the score change needed to cross
the nearest threshold (only report if ≤ 25 points); for each open Challenger question, the recommendation
if it resolved favourably/unfavourably (using the requested evidence's effect on the relevant family via
its tool with substituted inputs); for THIN_HEADROOM, the income confirmation that removes the flag. Output
≤ 4 entries, ordered by proximity.

## 6. `autonomy.yaml` and routing

```yaml
schema: autonomy/1.0
product: PF-STD
version: 2026.09.1
setting: ASSIST                          # ADVISE | ASSIST | ACT_WITH_APPROVAL | AUTONOMOUS_WITHIN_LIMITS
bands:
  - { max_amount: 10000, autonomous_eligible: true }
  - { max_amount: 20000, autonomous_eligible: true }
  - { max_amount: null,  autonomous_eligible: false }
autonomous_conditions:                    # ALL must hold
  recommendation_in: [APPROVE]
  min_confidence: 0.90
  max_disagreement: 0.25
  challenger_open: false
  hard_gate_exceptions: 0
  integrity_findings_open_max_severity: LOW
  member_watchlist: false
  active_hardship_arrangement: false
  model_health: GREEN
  case_type_in: [ORIGINATION]
sampling: { rate: 0.10, reviewer_role: SENIOR_OFFICER, sla_hours: 24 }
kill_switch: { owners: [HEAD_OF_CREDIT, HEAD_OF_RISK], effect: { revert_to: ADVISE } }
action_levels:
  L0: { requires: AUTO }
  L1: { requires: AUTO_IF_POLICY, allowed_types: [REQUEST_DOCUMENT, SEND_REMINDER, CREATE_NOTE, CREATE_TASK] }
  L2: { requires: OFFICER,        allowed_types: [ESCALATE, ASSIGN_REVIEWER, PROPOSE_VERIFICATION, OFFICER_OUTREACH, HARDSHIP_REVIEW] }
  L3: { requires: PER_SETTING,    allowed_types: [APPROVE_FINANCING, DECLINE_FINANCING, RESTRUCTURE, LIMIT_CHANGE] }
```

Routing algorithm:

```python
def route(autonomy, rec, snap, model_health):
    reasons = []
    if kill_switch_active(snap.product_code):
        return "OFFICER_REVIEW", ["KILL_SWITCH"]
    base = {
        "APPROVE": "OFFICER_REVIEW",
        "DECLINE": "OFFICER_REVIEW",
        "REVIEW": "OFFICER_REVIEW",
        "ENHANCED_ASSESSMENT": "ENHANCED_ASSESSMENT",
        "COMPLIANCE_REVIEW": "COMPLIANCE",
    }[rec.recommendation]
    if rec.required_authority == "SENIOR_OFFICER":
        base = "SENIOR_REVIEW"
    if rec.required_authority == "CREDIT_COMMITTEE":
        base = "COMMITTEE"
    if rec.disagreement > 0.60:
        base, reasons = "ENHANCED_ASSESSMENT", reasons + ["DISAGREEMENT_HIGH"]
    if autonomy.setting != "AUTONOMOUS_WITHIN_LIMITS":
        return base, reasons + [f"SETTING:{autonomy.setting}"]
    band = band_for(snap.requested_amount, autonomy.bands)
    checks = {
        "BAND": band.autonomous_eligible,
        "REC": rec.recommendation in cond.recommendation_in,
        "CONF": rec.confidence >= cond.min_confidence,
        "DISAGREE": rec.disagreement <= cond.max_disagreement,
        "CHALLENGER": not rec.challenger_open,
        "GATES": no_exceptions(rec),
        "INTEGRITY": max_open_severity(rec) <= cond.max_sev,
        "WATCHLIST": not watchlist(snap.member_id),
        "HARDSHIP": not active_hardship(snap.member_id),
        "MODEL_HEALTH": model_health == "GREEN",
        "CASE_TYPE": snap.case_type in cond.case_type_in,
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        return base, reasons + [f"AUTONOMY_FAIL:{k}" for k in failed]
    sampled = random_draw(seed=snap.snapshot_id) < autonomy.sampling.rate
    return "AUTONOMOUS", reasons + (["SAMPLED"] if sampled else [])
```

ASSIST vs ACT_WITH_APPROVAL affect the UI (individual confirm vs batch confirm) and are recorded in `route_reasons`.
For EARLY_WARNING cases L3 actions are always `PROHIBITED`; L1 actions may auto-execute only if `setting ≠ ADVISE`.

## 7. Policy Sandbox (`POST /policy/sandbox/replay`)

Input: `{ product_code, candidate: { policy?: full pack or patch, dff?: patch, autonomy?: patch }, range: { from, to } | { snapshot_ids[] }, compare_to: "current" }`.
For each stored snapshot with a stored `PolicyResult` and `FactorScore[]`: re-evaluate gates with the candidate,
re-score factors whose formulas changed (tool re-run with stored inputs from `app_policy.calc.inputs`), re-synthesize
with stored opinions, re-route. Output: baseline vs candidate — approval rate, decline rate, review rate, autonomous
share, total approved exposure, projected 12-month delinquency (Σ pd_12m of approved), per segment (grade, branch,
employer sector, amount band), and a per-case diff list (`snapshot_id, before, after, decisive_change`). No LLM calls.
Adoption (`POST /policy/{product}/versions`) requires two approver roles and writes a new version + `autonomy.setting_changed`/`policy.version_adopted` events.
