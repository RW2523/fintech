# 09 — Web app specification (`apps/web`)

Single React + TypeScript SPA (Vite, Tailwind, TanStack Query, React Router, zod contracts from codegen).
Design: calm, dense, navy accent (`#0D2A5C`), light surfaces, no charts library beyond a small sparkline/bar
component (Recharts is acceptable). Every page shows a persistent **SYNTHETIC DATA** badge and the signed-in role.
The UI never computes ratios, scores or routes: it renders `DecisionRecord`, `PolicyResult`, `FactorScore` and
`CommitteeRun` payloads and links every number to its `calc_id`/`model_run_id` tooltip.

## 1. Shell and auth
- `/login`: role picker (officer, senior_officer, collections, manager, compliance, head_of_credit, member) → calls `/api/auth/dev-token` → JWT in memory (not localStorage for demo hygiene beyond a session).
- Left nav by role. Header: environment badge, trace id of last request (copy), kill-switch status pill (red when active), Autonomy setting pill per product.

## 2. Officer queue `/officer`
Table: case id (short), member token, product, amount, tier chip, route chip, recommendation chip, confidence %, disagreement (small bar), SLA countdown, assigned. Filters: route, state, tier, branch. Row click → case.
Sort default: route priority (COMPLIANCE, ENHANCED, SENIOR, OFFICER) then SLA.

## 3. Case page `/officer/cases/:caseId`
3.1 **Header**: applicant token, product, amount, tenor, purpose, state, SLA clock, snapshot id (copy), policy/dff/autonomy versions (tooltip).
3.2 **Signal cards** (three): Affordability (DSR vs limit gauge, headroom, stress rows with pass/fail), Risk (grade chip, PD %, top-3 reason codes), Documents (required list with status, min confidence, findings count by severity).
3.3 **Decision card**: recommendation chip; confidence and disagreement meters; factor bars (score × weight, decisive highlighted); hard-gate list with pass/fail; unresolved items; Challenger reservation callout; "Would change the outcome" list; route + reasons; required authority; tier and budgets (tokens/seconds).
3.4 **Actions** (per role and authority): Request information (pick documents → L1 proposal), Escalate, Approve / Approve with conditions / Decline (disabled with explanation when over authority), Override (opens reason-code form; text ≥ 20 chars; ≥ 60 for OVR-12), Defer. Submits `HumanDecision`; shows resulting token id and execution state.
3.5 **Ask the file** panel: chat with the officer copilot; answers show citations as evidence chips that open the evidence panel.
3.6 **Agent Discussion drawer** (collapsed by default): one row per agent → stance chip, confidence, factor score if owner, 3 top claims (each with evidence chips), unresolved, `changed_from_prior` in REVISE; Challenger row first; Synthesizer footer with hierarchy steps and which step decided. No raw model text beyond claims.
3.7 **Evidence panel**: list of `EvidenceRef`s grouped by type; DOCUMENT_FIELD opens the page image with bbox highlight; CORE_FIELD shows source table/field; POLICY_RULE shows rule text and clause; MODEL_OUTPUT shows drivers; TIMELINE_EVENT scrolls the timeline.
3.8 **Timeline tab** (servicing/early-warning cases): events chronologically with state badges and change-point markers.

## 4. Collections workbench `/collections`
Priority list: rank, member token, state chip (WATCH/ELEVATED/CRITICAL/RECOVERY), p30 with interval, exposure, why-now line, last contact, next action. Member drawer: timeline with baseline bands and change-point markers; forecast panel (7/30/60/90 bars with intervals, scenario A/B); Longitudinal Council decision card; proposed actions with `requires`; outreach draft editor (template + variables) → Approve & send; restructure options table (from affordability tool) → propose to senior; outcome logging (contacted, promise-to-pay with date, kept).

## 5. Member assistant `/member`
Chat UI with quick buttons (Balance, Next payment, Application status, Missing documents, Talk to a person). Messages from the assistant show "from your records" chips. Hardship handoff shows a confirmation card. Inbox tab shows reminders and messages. Never shows recommendations or scores.

## 6. Ledger viewer `/ledger`
Search by case id / member token / decision record id. Reconstruction view: vertical timeline of ledger entries (SNAPSHOT → COMMITTEE_RUN → OPINION×n → DECISION_RECORD → HUMAN_DECISION → TOKEN → ACTION → OUTCOME) with expandable JSON, hash and prev_hash, chain verification badge (calls `/ledger/verify`), export (JSON). Compliance list: overrides, high-disagreement, autonomous decisions with sample status, kill-switch history.

## 7. Management cockpit `/manager`
7.1 Tiles: applications received/approved/pending, median and p95 processing time by tier, approval rate by product/branch, autonomous share, risk-grade distribution, delinquency and roll rates, early-warning population by state, collections cure by tier, override rate, model health (GREEN/AMBER/RED), fairness status.
7.2 Ask the portfolio: question box → manager copilot; answer with the metric tables it used (rendered) and a one-paragraph narrative.
7.3 **Policy Sandbox**: product selector; weight sliders (sum shown, must equal 1.0); threshold inputs; replay range picker (last N months); Run → results: baseline vs candidate table (approval/decline/review rates, autonomous share, approved exposure, projected delinquency), segment breakdown, case diff list (before/after, decisive change) → "Adopt as version" with two approver role fields (demo stub).
7.4 Governance: autonomy settings per product (setting, bands, conditions) with change form (two approvers), kill switch button (owner roles) with reason, sampling queue.

## 8. Shared components
`DecisionChip`, `RouteChip`, `Meter` (0–1), `FactorBars`, `EvidenceChip`, `DocumentViewer` (page image + bbox overlay), `JsonDrawer`, `SyntheticBadge`, `KillSwitchPill`, `Sparkline`, `IntervalBar`.

## 9. Playwright smoke suite (`apps/web/tests`)
login as officer → queue shows ≥ 10 rows → open S1 → decision card values equal API JSON → open drawer → click a claim → evidence panel highlights bbox →
login as head_of_credit → set autonomy → kill switch → pill red → login as member → ask balance → answer contains amount from API → ledger reconstruct S2 → verify badge green.
