# 10 — Synthetic data programme (`synthetic/`)

Everything is generated from a seed (`--seed 42`) with `numpy.random.default_rng`. No real names: member
names are generated from syllable tables and the UI shows tokens (`M-004512`). Currency is a neutral "LCU".
CLI: `python -m synthetic.cli population|documents|scenarios|golden|all [--seed --n --months --out]`.

## 1. Employers and sectors
120 employers across sectors {PUBLIC_ADMIN, EDUCATION, HEALTH, UTILITIES, MANUFACTURING, RETAIL, TRANSPORT, AGRICULTURE}
with sizes (log-normal, median 60 members), a payslip `template_id` (12 templates), `deduction_day` (25–28), and an
`outage_profile` (probability of a missed deduction cycle affecting all their members: 0.01/month; two employers get a 2-month interruption in months 14–15 for scenario S9).

## 2. Members (n = 5,000)
| Attribute | Distribution |
|---|---|
| joined_at | uniform over the last 25 years, skewed recent (beta(2,3)) |
| age | 22–60 at join |
| employer | weighted by employer size |
| salary_monthly | sector base × log-normal(σ=0.35); bands 1,800–18,000 |
| savings_balance | tenure-correlated: 0.3–4 × monthly salary, monthly deposit habit per archetype |
| share_capital | ≥ product minimum (100 units) for 85 %; grows with tenure |
| identity_verified | 97 % true |
| language | 60 % en, 25 % lang_b, 15 % lang_c (labels only) |
| archetype | STEADY 55 %, SEASONAL 12 %, IMPROVING 8 %, SLOW_DRIFT 12 %, SHOCK 8 %, CHRONIC 5 % |
| grade (synthetic bureau) | derived from archetype + noise: STEADY→A/B, IMPROVING→B/C, SEASONAL→B, SLOW_DRIFT→B/C, SHOCK→B→D, CHRONIC→D/E |

## 3. Accounts and schedules
Accounts per member: 1 (60 %), 2 (30 %), 3 (10 %); product PF-STD 80 %, PF-SHARIAH 20 %; principal 0.5–8 × salary; tenor 12–84;
instalment from the product formula; `due_day` 1–28; opened 1–24 months ago (some before the 24-month window: history truncated).
Guarantors: 30 % of accounts have 1–2 guarantors from the same employer or branch; scenario S5 injects a 7-member cycle.

## 4. Payment behaviour by archetype (`days_to_pay` = paid_at − due_date)
| Archetype | Baseline `days_to_pay` | Dynamics over 24 months |
|---|---|---|
| STEADY | N(−1, 1.5) | stationary; 1 % chance of a single +5 blip |
| SEASONAL | N(0, 2) with +4 in months 11–12 and 23–24 (festive) | annual |
| IMPROVING | starts N(+6, 3), slope −0.4/month | converges to STEADY |
| SLOW_DRIFT | N(−1, 1.5) for months 1–14; then +0.8/month drift + widening σ (S8 pattern); deduction receipts become irregular from month 16 (30 % missed); savings deposits pause month 17 | reaches first late (> 7 days) around month 19–20 |
| SHOCK | STEADY until a random month m∈[8,18]; then +12 days for 2–3 cycles, one missed; then RECOVERY (on-time) if outreach event present else continued late | tests recovery |
| CHRONIC | N(+9, 6); 20 % missed cycles; 1–2 restructures | high DPD |
Payments: `paid_at = due + days_to_pay` (clipped ≥ −10); missed if `days_to_pay > 30` for the cycle (recorded when a later payment covers it or charge-off after 120 days). Partial payments 3 % of on-time payers (60–90 % of instalment, remainder within 10 days).
Deductions: expected each cycle for salaried members; received unless employer outage or archetype rule.
Savings: monthly deposit habit {none, 2 %, 5 %, 8 % of salary} by archetype; withdrawals for SHOCK/CHRONIC.
Interactions: reminders per cadence; contacts and promises for late payers (promise kept 70 % STEADY-like, 35 % CHRONIC).
Outage windows: two system outages of 2–3 days affecting payment posting (S9).

### 4.6 Sanity ranges (asserted by `synthetic/tests`)
Overall 30-day delinquency per account-month 6–8 %; STEADY late30 rate < 1 %; CHRONIC > 25 %; SLOW_DRIFT first late event in months 18–21 for ≥ 80 % of the cohort; deduction missed rate ≈ 3 % overall; savings paused ≈ 12 % of members at some point.

## 5. Labels (`core.outcome`)
Per account-month: `late7/30/60/90` if any due event in the month reached that DPD; `cure` when a late account returns to 0 DPD; `restructure` when an arrangement starts; `charge_off` at 120+ DPD. Also `first_late_date` per account for lead-time metrics.

## 6. Documents (`synthetic/documents`)
Templates (Jinja2 HTML + CSS) rendered with Playwright Chromium to PDF (A4) and PNG (200 dpi):
- `payslip/<template_id>.html` ×12 (different layouts, fonts, logos as generated shapes, deduction tables).
- `bank_statement.html` (3-month, salary credits matching payslip net ± noise, other transactions from a generic list without personal narrative).
- `identity_card.html` (name, id number pattern `[A-Z]{2}\d{7}`, dob, expiry; photo = generated avatar silhouette).
- `employment_letter.html`, `provident_fund_statement.html`.
Scan noise pipeline (Pillow): rotation ±1.5°, Gaussian blur 0–0.8 px, JPEG quality 70–90, slight brightness/contrast jitter; 10 % of documents "clean digital" (no noise).
Ground truth JSON per document (all fields + bbox from the DOM via Playwright `boundingBox` mapped to page coordinates).
Anomaly injection (manifest `synthetic/documents/anomalies.json`): edited net_salary total (+8–20 %) with a different font (S3), reused bank-statement image across two members (S3), payslip net 6 % below deduction record (S2), metadata date before period (INT-01), template mismatch (employer's template swapped), identity mismatch (S-variant), duplicate id number across two applicants.

## 7. Applications (600)
Sampled members with amounts 0.5–6 × salary (skewed low), tenor 12–60, purpose from list; 100 flagged `demo_queue=true`
spread across routes; document bundles: complete (85 %), missing one required (10 %), low-quality scan (5 %).
Guarantor ring (S5): 7 members A→B→C→D→E→F→G→A with 4 applications in 60 days.

## 8. Scenarios (`synthetic/scenarios/*.yaml`)
Each scenario file: `id, title, setup: {member/application/document overrides}, expected: {tier, hard_gates[], recommendation, route, challenger_open, findings[], state?, p30_range?, actions[]}`.
S1 clean fast path · S2 income discrepancy · S3 altered document + reused image · S4 policy breach (DSR 0.68) · S5 guarantor ring ·
S6 sandbox weights (uses S1) · S7 autonomy + kill switch (uses a clean small case S7a and S7b) · S8 slow-drift member (14 months clean, drift from month 15, no missed payment yet at "today") ·
S9 outage noise + recovery (S8 member after outreach; 300 receipts delayed by outage) · S10 member assistant + hardship + reconstruction (uses S2).
"Today" for the demo = month 20 of the 24-month history for LMI scenarios (so future months exist for backtests but are hidden from the live views by `as_of`).

## 9. Golden cases (`synthetic/golden/`)
50 cases = 10 scenario cases + 40 sampled applications with expected outputs computed by the deterministic path
(policy + factor scores + route with a **fake gateway** whose agents return scripted opinions per case). Each golden case:
`{snapshot_fixture, expected: {hard_gates, weighted_score±2, recommendation, route, challenger_open, findings, tier}}`.
The harness also stores real-gateway results per run to track drift.

## 10. Policy corpus (`synthetic/policy_corpus/`)
`credit_policy.md` (~30 pages; sections mirror `policy.yaml` rule ids as clause ids), `product_PF-STD.md`, `product_PF-SHARIAH.md`,
`collections_procedure.md`, `hardship_policy.md`, `authority_matrix.md`. Front-matter with product and version.
