# 07 — Intelligence services (document AI, features, risk, fraud, LMI, explainability)

## 1. Document AI (`services/document`)

### 1.1 Ingest
`POST /cases/{case_id}/documents` returns a presigned MinIO PUT URL and `document_id`; `POST /documents/{id}/complete`
triggers processing. Checks: MIME ∈ {pdf, png, jpg}, size ≤ 20 MB, pages ≤ 20, ClamAV optional (skip in demo, log),
render pages to PNG at 200 dpi (`pdf2image`), compute sha256 and perceptual hash per page (`imagehash.phash`).

### 1.2 Classify
Labels: IDENTITY, PAYSLIP, BANK_STATEMENT, EMPLOYMENT_CONFIRMATION, PROVIDENT_FUND_STATEMENT, FINANCING_STATEMENT, OTHER.
Method: Tesseract text of page 1 → keyword prior; VLM route with the page image and a constrained JSON schema
`{label, confidence}`; final = VLM label unless VLM confidence < 0.6 and keyword prior is strong. Confidence < 0.85 → human classification task.

### 1.3 Extract
Tesseract `image_to_data` (words with boxes) per page. VLM extraction per document type with a schema of expected
fields (e.g. PAYSLIP: employer_name, employee_name, period, gross_salary, net_salary, deductions[], pay_date;
BANK_STATEMENT: account_holder, account_number_masked, period, closing_balance, salary_credits[] {date, amount, description};
IDENTITY: id_number, name, dob, expiry; EMPLOYMENT_CONFIRMATION: employer_name, employee_name, position, start_date, monthly_salary, letter_date).
Anchoring: for each extracted value, find the best matching OCR word span (normalised string match ≥ 0.85) to get the
bbox; if no anchor, bbox = null and confidence ×0.7. Normalisation: amounts → Decimal, dates → ISO, names → casefold.
Confidence = VLM self-reported × anchor score. Each field → `EvidenceRef(type=DOCUMENT_FIELD, locator={document_id,page,bbox,field_path})`.

### 1.4 Forensics (findings INT-01/02/07 and tampering signals)
- Metadata: PDF creation/modification dates vs claimed period; producer software anomalies; PNG lacking EXIF where template expects.
- Image reuse: page phash within Hamming distance ≤ 6 of any prior document of another member → INT-02 (HIGH).
- Copy-move: OpenCV ORB keypoints + RANSAC self-matching regions → tampering signal (MEDIUM), boosted to HIGH if the region overlaps an amount field.
- Font/kerning: per-line character height variance and inter-word spacing outliers on amount lines vs document median → MEDIUM.
- Template mismatch: employer known template id vs detected layout signature (line positions of header fields) → INT-07 (MEDIUM).

### 1.5 Reconciliation (findings INT-03/04/06/08)
- Income: payslip net median (3) vs deduction-record net for same cycles vs bank salary credits (if uploaded). Variance = |a−b|/max(a,b). > 5 % → INT-03 MEDIUM; > 15 % → HIGH.
- Employer: application employer vs payslip employer vs deduction employer (rapidfuzz JW ≥ 0.92 or embedding cos ≥ 0.90 to match) → mismatch INT-06/INT-07 MEDIUM.
- Identity: id_number/name/dob across IDENTITY vs core member → mismatch INT-08 CRITICAL.
- Duplicates: same id_number or same document hash across applications of different members → INT-04 HIGH.
Findings carry `sources[]` evidence ids and severity; the Fraud service reads them.

## 2. Features and credit risk

### 2.1 Origination feature set (`app_feature.feature_def`, version 1)
`ontime_rate_24m, arrears_events_12m, months_since_last_arrears, restructures_36m, facilities_open, facilities_new_6m,
utilisation, tenure_months, savings_balance, savings_slope_180d, savings_paused_months, share_capital_units, share_capital_ratio,
income_verified_monthly, income_source_variance, dsr_proposed, commitments_monthly, employer_sector, employer_tenure_months,
application_count_12m, contact_change_days, doc_min_conf, findings_max_severity`. Each with permitted uses (all UNDERWRITING).

### 2.2 Model training (`ml/credit_risk/train.py`)
Label: `late90` within 12 months of origination (from `core.outcome`). Split: by origination month, train ≤ month 14,
validation 15–18, test 19–24. Champion: binned features (5 quantile bins, WoE), logistic regression with sign constraints
per feature (monotone direction table in `ml/credit_risk/monotone.yaml`), L2. Challenger: LightGBM `monotone_constraints`,
`num_leaves 15`, early stopping. Calibration: isotonic on validation (Platt if < 300 events). Grade bands on calibrated PD:
A < 1.5 %, B < 3 %, C < 6 %, D < 12 %, E ≥ 12 %. SHAP (TreeExplainer / linear) → top-5 drivers → reason-code map
(`ml/credit_risk/reason_map.yaml`). OOD: IsolationForest on standardised features, score → [0,1]. Artifacts:
`ml/credit_risk/artifacts/<version>/{champion.joblib, challenger.joblib, calibrator.joblib, ood.joblib, card.md, metrics.json}`.
Metrics required in card: AUC, PR-AUC, KS, Brier, calibration slope/intercept, decile lift, fairness note (no protected features present).

### 2.3 Serving (`POST /risk/score`)
Input `{snapshot_id}` → loads feature snapshot → returns
`{model_run_id, champion:{model, version, pd_12m, grade, calibration, ood_score}, challenger:{...}, conduct_score, conduct_calc_id, reason_codes[], drivers[], evidence_refs[]}`.
`conduct_score` per `dff.yaml` CONDUCT formula computed here from feature values (deterministic), recorded as a calc. `GET /version`.

## 3. Fraud and integrity (`services/fraud`)

- Rules (each → finding with severity): velocity (≥ 3 applications from same employer+branch in 7 days → MEDIUM),
  contact change within 14 days before application → INT-06 LOW, reused image (from document findings) → HIGH, duplicate applicant → HIGH,
  identity mismatch → CRITICAL, guarantor findings (below).
- Entity resolution: nodes = members, employers, guarantors, documents (by phash), phones/emails (synthetic); edges = employed_by, guarantees, shares_document, shares_contact. `networkx`.
- Guarantor analytics: cycles of length 2–8 among guarantee edges → INT-05 MEDIUM (HIGH if ≥ 3 applications in cycle within 90 days); serial guarantor (out-degree ≥ 5) → MEDIUM; concentration (≥ 40 % of a branch's guarantees on ≤ 3 people) → LOW portfolio note.
- Anomaly: IsolationForest on origination features + document stats; score ≥ 0.7 → advisory finding "ANOMALY" (LOW; never alone raises level).
- Level: max severity among findings; `integrity_score` per `dff.yaml`. Output `{findings[], integrity_score, level, calc_id, graph_ref, evidence_refs[]}`.

## 4. Longitudinal Member Intelligence (`services/feature` + `services/lmi`)

### 4.1 Event sources (synthetic core → `member_event`)
PAYMENT_DUE/RECEIVED/LATE/PARTIAL/REVERSED from schedule+payment; DEDUCTION_RECEIVED/MISSED from deductions; SAVINGS_BALANCE
monthly; SHARE_CAPITAL quarterly; APPLICATION, DOCUMENT_VERIFIED, CONTACT_*, PROMISE_TO_PAY, HARDSHIP_REQUEST, ARRANGEMENT_*, OUTAGE_WINDOW.

### 4.2 Temporal features (windows 7/30/90/180/365)
Per account: `days_to_pay` per due event (paid_at − due_date, negative = early); `days_to_pay_median_{w}`, `days_late_p95_{w}`,
`late_streak`, `due_to_pay_slope_180d` (OLS slope over due events); `deduction_missed_count_90d`, `deduction_amount_delta_90d`,
`employer_gap_flag` (≥ 30 % of employer's members missed the same cycle); `savings_slope_180d`, `savings_paused_months`,
`share_capital_ratio`; `dsr_trend_180d`, `new_obligations_6m`; `contact_response_rate`, `promise_kept_rate`, `extension_requests_12m`, `hardship_flag`;
personal baseline per signal: `median_365d`, `MAD_365d`, `robust_z = (x − median)/(1.4826·MAD + ε)`; seasonal adjustment: STL
(statsmodels) on monthly `days_to_pay` when ≥ 24 points, features on residuals; recovery: `consecutive_on_time_since_alert`, `distance_to_baseline` (|robust_z|), `risk_decay = exp(−0.15 · on_time_events_since_alert)`.

### 4.3 Change-point detection (`services/lmi/changepoint.py`)
For signals `days_to_pay`, `deduction_received_ratio`, `savings_balance` (monthly): one-sided CUSUM on robust z:
`S_t = max(0, S_{t−1} + z_t − k)`, `k=0.5`, alarm `S_t > h`, `h=4.0` (configurable per signal in `lmi.yaml`); reset after alarm.
Confirm with PELT (`ruptures.Pelt(model="rbf", min_size=3).fit(series).predict(pen=3.0)`) — confirmed when a PELT break lies within 30 days of the CUSUM alarm. Emit `behaviour.change_point_detected` with `{signal, cp_date, stats}`.
Isolation Forest over the 12-feature window vector → `anomaly_score` (advisory only).

### 4.4 Early-warning and survival models (`ml/lmi/train.py`)
Rows: account-month snapshots (as_of = month end) with features above; labels `late_event_within_{7,30,60,90}d` (any PAYMENT_LATE/MISSED after as_of within horizon).
Split by as_of month (same scheme as risk). Model per horizon: LightGBM (`monotone_constraints` on timing/deduction features), isotonic calibration;
conformal intervals via split-conformal on validation residuals (α=0.10). Survival: `lifelines.CoxPHFitter` on time-to-first-late-event and time-to-cure with the same features (standardised); challenger `RandomSurvivalForest` optional.
Serving `POST /lmi/score {member_id, account_id, as_of?}` → `{forecast_id, horizons:{7:{p,lo,hi},30:{...},60,90}, survival:{median_days_to_late, median_days_to_cure, curve[]}, drivers[], scenarios:{A:{...}, B:{...}}, model_versions, evidence_refs[]}`.
Scenario A = re-score with the current slope extrapolated 30 days; B = re-score with timing/deduction features reset to baseline medians.

### 4.5 Member state machine (`services/lmi/state.py`; evaluated nightly and on events)
States STABLE, WATCH, ELEVATED, CRITICAL, RECOVERY. Transitions (all conditions must hold; evaluated in order):

| From → To | Conditions |
|---|---|
| STABLE → WATCH | `robust_z(days_to_pay) > 2` for ≥ 2 consecutive due events OR `deduction_missed_count_90d ≥ 1` |
| WATCH → ELEVATED | change-point CONFIRMED AND (`p30 ≥ 0.25` OR a second family corroborates) AND no OUTAGE_WINDOW overlapping the deviating due dates AND no ARRANGEMENT_ACTIVE |
| ELEVATED → CRITICAL | PAYMENT_LATE with days_late > 30 OR `p30 ≥ 0.60` with two corroborating families OR fraud level HIGH |
| ELEVATED/CRITICAL → RECOVERY | ≥ 2 consecutive on-time due events AND `distance_to_baseline` decreasing over last 2 events |
| RECOVERY → STABLE | ≥ 3 consecutive on-time due events AND `robust_z < 1` for 60 days |
| WATCH → STABLE | 2 consecutive on-time events with `robust_z < 1` |
| any → WATCH (de-escalate) | contradicting evidence: outage explains all deviating events, or arrangement active covering them |

Hysteresis: a state may change at most once per 14 days except to CRITICAL. Every transition writes `member_state` with `reason` (rule + evidence ids) and emits `member.state_changed`.

### 4.6 Corroboration rules (Cross-Data Investigator inputs; also applied deterministically before ELEVATED)
outage overlap → EXPLAINS; arrangement active → EXPLAINS (route via arrangement policy); deduction irregular + payment drift → CONFIRMS;
savings paused + payment drift → CONFIRMS; employer_gap_flag → EXPLAINS (employer-level issue; alert routed to employer contact task);
recent outreach followed by 2 on-time → RECOVERY.

### 4.7 Alert hygiene (`services/lmi/alerts.py`)
One open alert per account; dedupe on signal set; rank_value = `p90 × exposure × (1 + uplift_proxy)` where uplift_proxy = 0.2 if `contact_response_rate > 0.5` else 0;
per-officer daily cap from policy (`collections.cap_per_officer`, default 25); `why_now` = template "`{signal}` moved from {baseline} to {current} over {days} days; change-point {cp_date}; {corroboration}"; auto-close on RECOVERY→STABLE.
`early_warning.case_created` opens an EARLY_WARNING case (CaseSnapshot with `temporal_context_id`) and starts the workflow.

## 5. Notification cadence (`services/notification`)
From `policy.reminders.cadence`: schedule messages at due−14, −7, −3, 0, +1 days; template ids `REM_14, REM_7, REM_3_PRIORITY, DUE, OVERDUE_1`; language from member; channel preference; cancel remaining reminders on PAYMENT_RECEIVED; DEDUCTION_MISSED → same-day `DEDUCTION_MISSED_MEMBER` + employer-contact task.

## 6. Explainability (`services/governance /explain/factors`)
Input: DecisionRecord id → returns structured explanation levels: (1) policy reason codes with clause ids, (2) factor table with decisive flag and counterfactual, (3) model drivers with reason codes, (4) document provenance list, (5) human decision and override. Narratives are produced by the orchestrator's narrator from this structure; the structure is authoritative.
