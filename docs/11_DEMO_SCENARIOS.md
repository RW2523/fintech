# 11 — Demo scenarios and run-book

## 1. Scenario table (normative expected outputs; verified by the harness before every demo)

| ID | Title | Setup (scenario injector) | Expected system output | Demo action |
|---|---|---|---|---|
| S1 | Clean application, fast path | STEADY member, tenure 9 y, amount 8,000 (≤ fast_path_max), complete documents, no findings, 12 m clean | tier FAST; all gates PASS; weighted 82–88; confidence ≥ 0.90; recommendation APPROVE; route OFFICER_REVIEW (setting ASSIST); decisive CONDUCT or CAPACITY | open queue, open case, approve |
| S2 | Income discrepancy, Challenger | payslip net 6 % below deduction record; amount 25,000; tenure 10 y | tier EXTENDED; finding INT-03 MEDIUM; INTEGRITY ≈ 80; Challenger unresolved "confirm current salary" (non-blocking); recommendation APPROVE; confidence 0.80–0.90; disagreement 0.20–0.35; route SENIOR_REVIEW (amount band) ; would_change lists salary confirmation and permanent reduction | open drawer, click evidence, approve with condition |
| S3 | Altered document + reused image | edited payslip total (+12 %, font swap); bank statement image reused from another member | findings: tampering HIGH, INT-02 HIGH → fraud level HIGH; RT-01 → route COMPLIANCE; recommendation COMPLIANCE_REVIEW; weighted_score null | show forensics + graph link |
| S4 | Policy breach | DSR 0.68 vs 0.60; residual ok | AFF-01 FAIL → POLICY_EXCEPTION_OR_DECLINE; recommendation REVIEW; route SENIOR_REVIEW; required_authority SENIOR_OFFICER; no weighted score | show affordability card |
| S5 | Guarantor ring | 7-member guarantee cycle, 4 applications in 60 days | INT-05 HIGH on each; tier EXTENDED; fraud level HIGH → COMPLIANCE for the four; graph subgraph with cycle | open any, show graph |
| S6 | Board changes the weights | manager raises COMMITMENT 0.20→0.30, CONDUCT 0.30→0.20; replay last 12 months | sandbox report: approval rate delta, exposure delta, segments; S1 weighted score changes by +1 to +3; decisive may change | run sandbox, adopt, re-open S1 |
| S7 | Autonomy Dial and kill switch | head_of_credit sets PF-STD AUTONOMOUS_WITHIN_LIMITS; S7a clean case 6,000; then kill switch; S7b clean case 6,000 | S7a: route AUTONOMOUS, token issued to AUTONOMY_DIAL, execution → core activated, ledger TOKEN+ACTION, maybe SAMPLED; S7b: route OFFICER_REVIEW, route_reasons [KILL_SWITCH] | live on stage |
| S8 | Perfect payer drifting | SLOW_DRIFT member at month 20: 14 m on-time then +3, +8 days; deductions irregular; savings paused; no missed payment | state ELEVATED; change-point ≈ 42 days before today; p30 0.25–0.40 with interval; Longitudinal Council: recommendation INTERVENE; actions: PROPOSE_VERIFICATION (L2), OFFICER_OUTREACH (L2); no L3; corroboration table: deductions CONFIRMS, savings CONFIRMS, outage SILENT, arrangement SILENT | collections workbench, approve outreach |
| S9 | Outage noise and recovery | outage delays 300 receipts by 2–3 days; S8 member pays on time twice after outreach | zero new ELEVATED from outage (EXPLAINS annotations); S8 member → RECOVERY, alert paused | show suppressed alerts, state change |
| S10 | Member assistant + audit reconstruction | member asks balance & next payment; then writes about job loss; compliance reconstructs S2 | assistant answers with tool values; hardship → request_callback + `member.hardship_signal`; ledger reconstruction of S2 complete with verify green in < 2 min | live |

Tolerances: weighted scores ±2; probabilities within stated ranges; routes and gates exact.

## 2. Run-book (45 min, Board format)

| Time | Step | Screen | Talking point |
|---|---|---|---|
| 0–3 | Frame | architecture figure | system of intelligence beside the core; nothing executes without the dial |
| 3–8 | S1 | queue → case → approve | speed, completeness, every number has a calc id |
| 8–16 | S2 | drawer, evidence bbox, approve with condition | deliberation, dissent, evidence; "would change the outcome" |
| 16–20 | S3, S4 | forensics/graph; affordability card | gates cannot be outvoted; policy applied as written |
| 20–26 | S6 | sandbox → adopt → S1 | the Board writes the policy and tests it first |
| 26–31 | S7 | autonomy form → S7a executes → kill switch → S7b | bounded, revocable authority; sampling queue |
| 31–39 | S8, S9 | collections workbench; forecast; outreach; recovery | prevention with reasons; no overreaction; no grudges |
| 39–43 | S10 | member chat; ledger reconstruct | safe member service; two-minute audit |
| 43–45 | Close | cockpit tiles | what the Client provides; eight weeks to pilot |

Technical deep-dive additions (90 min): Grafana trace of S2; contracts live from the ledger API; edit a threshold in a
branch and run the harness; tool-denial and injection demo; stop the gateway and submit a case; LMI notebook; adapter discussion.

## 3. Scripts
- `scripts/reset_demo.sh`: `docker compose down -v` (data volumes only) → `make migrate` → `make seed` (population, documents, scenarios, golden, policy corpus index) → `POST /lmi/nightly` for the as-of date → `make warmup` → prints scenario case ids and URLs. Target ≤ 2 min excluding model load.
- `scripts/warmup_llm.sh`: one call per route with a tiny schema; verifies `/llm/health`.
- `scripts/demo_check.sh`: reset → `make harness` → exit non-zero on any failure → prints the run-book URL table.
- `scripts/drill_llm_outage.sh`: stops `vllm`/`llm_gateway`, submits S1, asserts route OFFICER_REVIEW and `narrative.status=DEGRADED`, restarts.

## 4. Pre-flight (T-60) and recovery
Pre-flight: `make demo` green; open tabs in run-book order; role logins ready; recording of each scenario available; LLM provider health checked.
Recovery: LLM slow → switch `LLM_PROVIDER_*` to the remote profile or show deterministic path + recorded narrative; misroute → open golden expectation and harness diff; UI issue → ledger viewer/API; total failure → recording + Q&A.
