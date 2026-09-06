# Progress

## Environment (measured 2026-09-05)

| Fact | Value | Spec expectation (`docs/02`) | Status |
|---|---|---|---|
| `uname -m` / kernel | aarch64 · 6.17.0-1026-nvidia | aarch64 | OK |
| OS | Ubuntu 24.04.4 LTS | DGX OS (Ubuntu 24.04 based) | OK |
| GPU | NVIDIA GB10, driver 580.159.03 | GB10 Grace Blackwell | OK |
| CUDA | 13.0 (V13.0.88) | CUDA 13.x | OK |
| CPU cores | 20 | 20 Arm cores | OK |
| Memory | 121 GB total · ~47 GB in use · ~73 GB available | 128 GB unified; build budgets ~104 GB | **CONSTRAINED** |
| Disk | 3.7 TB, 2.3 TB free | ≥ 200 GB free | OK |
| Docker / Compose | 29.2.1 / v5.0.2 | ≥ 24 | OK |
| uv | 0.10.12 | required | OK |
| Node | v22.23.1 | Node 20 LTS | newer, verify web build |
| Python (system) | 3.13.11 | 3.12 via uv | uv must pin 3.12 |
| vLLM / ollama image tags | not yet pulled | pin validated tag | pending T-003 |
| Measured tok/s | not yet measured | record at P4 | pending |

### Pre-existing workload on this host — RESOLVED 2026-09-06

An unrelated `echomind` stack held resources this build's compose profile assumes
are free. **On 2026-09-06 the user asked for it to be stopped**, and its six
containers were halted with `docker stop` (not removed, so `docker start` brings
them back).

| Was held | Before | After |
|---|---|---|
| Host memory available | 71 GB of 121 GB | **115 GB of 121 GB** |
| GPU memory | ~33 GB held by `trtllm-serve` and python workers | **fully free** |
| Port 3000 | `echomind-frontend` | free |
| Port 11434 | `echomind-ollama` | free |

This clears the constraint flagged against T-040: 115 GB available against the
~104 GB the memory budget in `docs/02 §4.3` calls for. Stopping the stack also
took `echomind-cloudflared` down, so anything reaching echomind through that
tunnel is offline until it is restarted.

Grafana and ollama can move back to their documented ports (3000, 11434) whenever
convenient; they stay remapped to 3001/11435 in `docker/.env` for now because the
stack is running and healthy on them.


| Task | Status | Date | Notes / deviations |
|---|---|---|---|
| T-001 | done | 2026-09-05 | uv workspace on py3.12. Services/ai/ml/synthetic/workflows are **virtual** uv members (`package=false`); shared runtime deps live at the root so one venv serves all. Each service keeps `app/` per CLAUDE.md §3, so `make test` runs pytest per service dir to avoid the shared `app` module name colliding. Heavy ML/doc-AI deps are extras (`ml`, `docai`, `storage`) pulled in by the phase that needs them. |
| T-002 | done | 2026-09-05 | `scripts/env_check.sh` + `make env`. Exits 0 on this Spark: 17 PASS, 3 WARN, 0 FAIL. WARNs are the real host conditions recorded above (73 GB free vs 96 GB wanted, port 3000 held by echomind-frontend, /data/hf absent). GPU container runtime verified working. `SKIP_GPU_RUNTIME=1` skips the container pull in CI. |
| T-003 | done | 2026-09-05 | compose.yaml (profiles core/services/web/observability/ai-local/ai-remote) + compose.spark.yaml GPU overrides + .env.example + observability configs. 28 containers healthy. Deviations: (a) **Grafana on 3001**, not 3000, because echomind-frontend holds 3000 on this host; ollama remapped to 11435. All ports are env vars. (b) otel-collector image is distroless so it cannot self-probe; its health_check extension is published on 13133 and verified from the host by verify_phase.sh. (c) All 18 services share one placeholder image until T-008. |
| T-004 | done | 2026-09-05 | 12 JSON Schemas (11 contracts + `common.1.0.json` for shared primitives), reason_codes.yaml (12 groups, 67 codes with officer and member wording), events.yaml (47 events). Codegen bundles the cross-referencing files into one document, then emits pydantic v2 (`--use-title-as-name`, extra=forbid) and zod/TS. Deviation: contracts cross-reference by relative filename and share `common.1.0.json` rather than each inlining primitives; a bundle step makes that resolvable for both generators and the runtime validator. Acceptance: make codegen + 68 contract tests green; apps/web tsc --noEmit clean. |
| T-005 | done | 2026-09-05 | settings, ids, hashing, errors, db, outbox, auth, otel, http. 91 tests. Outbox proves atomicity, at-least-once delivery, consumer idempotency across a simulated crash, retry after a failing consumer, and that a healthy consumer is not re-invoked on retry. Hash chain detects tamper, deletion and reordering. Notes: (a) outbox tests need PostgreSQL and skip cleanly without it, so `make test` still runs docker-free; (b) `ddl_statements()` splits SQL respecting $$-quoted bodies; (c) dev secret defaults are >=32 chars and refused outside demo. |
| T-006 | done | 2026-09-05 | spec, grants, masking, evidence, registry. 34 tests. `registry.call()` is the only path: grant check, per-tool and per-run budgets, argument pinning to the run's case/member scope, input+output schema validation, purpose masking, EvidenceRef minting, and an Invocation audit row for every call including denials. Minted refs are validated against EvidenceRef 1.0. Note: a scope violation still consumes budget, deliberately, so an agent cannot probe other members' ids for free. |
| T-007 | done | 2026-09-05 | `core` schema (16 tables) + REST facade + change feed + token-guarded writes. 21 tests. Writes refuse without `X-Approval-Token`, are idempotent on the caller's key (a replayed activation returns the first result and creates nothing), and every write is logged with its token. Notes: (a) the change-feed trigger takes its key column as an argument and reads it via jsonb, because plpgsql resolves every CASE branch and `NEW.account_id` fails on `member`; (b) queries name their columns from the response model rather than `SELECT *`, so a new column cannot leak into a strict response; (c) one parameterised Dockerfile at `docker/images/service/` serves all services. |
| T-008 | done | 2026-09-05 | All 18 services scaffolded on the shared `cio_common.service` factory (health, version, trace header, error envelopes, OTel). Each owns its schema with its own alembic version table (`alembic_version_<service>`), because one shared table made every service fail on the others' revision ids. Gateway does real reverse proxying with bearer-token checks, principal headers forwarded to services, trace-id minting, plus `/api/auth/dev-token` and `/api/services`. 14 gateway tests + 4 meta tests per service. |
| T-010 | done | 2026-09-05 | PF-STD and PF-SHARIAH packs (policy/dff/autonomy), JSON Schemas for each file, and a loader with semantic checks. 32 tests. **Deviation:** the YAML in docs/05 §2 is not parseable as printed. `documents:`, `affordability:` and `exposure:` mix mapping keys with sequence items, which YAML forbids. Every category now nests its rules under a `rules:` key; content is unchanged. Also `product:` inside `product:` was renamed `product_terms:`, and `effective_from` is quoted so YAML keeps it a string. Semantic checks reject: weights not summing to 1, unordered thresholds, non-ascending or non-open-ended authority bands, scoring/weights mismatch, duplicate rule ids, files disagreeing on product or version, blocking gates without a reason code, critical fields on a non-required document. |
| T-011 | done | 2026-09-05 | Safe expression evaluator (AST whitelist, no eval/exec), context builder with per-key evidence, deterministic `affordability.compute` with calc_id, gate evaluation producing a contract-valid PolicyResult, and `/policy/evaluate` + `/policy/affordability`. 163 tests. Notes: (a) rules are YAML-flavoured, so `true`/`false`/`null` are bound as literals, not identifiers; (b) `in(x, [...])` is rewritten to a function call only where `in` starts an operand, so the membership operator still works; (c) a comparison touching an absent key is False, never True, so missing data cannot pass a gate; (d) property tests confirm DSR is monotonic in commitments and requested amount, antitonic in income, and capacity score stays in 0-100. |
| T-012 | done | 2026-09-05 | Factor scoring for all five families, the Synthesizer hierarchy, and the Autonomy Dial. 236 policy tests. Table-driven coverage of every branch: hard gates stop scoring entirely (a unanimous Council cannot outvote one), a CRITICAL integrity finding is a gate not a deduction, blocking evidence gaps and sub-minimum coverage stop before scoring, threshold boundaries at 70/45, exactly one decisive family, confidence as a geometric mean, disagreement excluding the Challenger, and all eleven autonomy conditions individually blocking AUTONOMOUS. Sampling is a deterministic per-snapshot draw so a demo reproduces exactly; measured 10.2% at a 10% rate. |
| T-013 | done | 2026-09-05 | Sandbox replay with `app_policy` tables (policy_version, calc, kill_switch, sandbox_run, replay_case). 17 sandbox tests, 253 policy tests total. Replay applies a candidate patch to a copy, re-runs gates, factor scoring and synthesis over stored cases, and reports baseline vs candidate approval/decline/review rates, autonomous share, approved exposure, projected 12-month delinquency, four segment breakdowns and per-case diffs naming the decisive family on each side. Calls no model. The S6 weight change moves cases; a no-op candidate moves none. Acceptance measured: 50 stored cases replay through the API in well under the 10s budget. |
| T-014 | done | 2026-09-05 | Append-only hash-chained ledger, human decisions with authority enforced at call time, and single-use approval tokens. 50 tests. A database trigger refuses UPDATE and DELETE on `ledger.entry`, so the guarantee does not depend on application code; the tamper tests disable it deliberately to prove verification goes red on an altered payload, a rewritten link and a removed row. Appends take an advisory lock so the chain has one order under concurrency. Note: the token signature deliberately excludes `used_at`, because a signature that moved when the token was consumed would be no tamper check at all. |
| T-015 | done | 2026-09-05 | application-service with the CaseSnapshot freeze. 24 tests. Submit gathers every version stamp (member projection, document bundle hash, policy/dff/autonomy from policy-service, model versions from each service's `/version`), writes an immutable snapshot and emits `application.submitted` through the outbox. A second submit writes version 2 rather than editing version 1, so the earlier decision stays reconstructable; a database trigger refuses UPDATE and DELETE on `case_snapshot`. A service that cannot answer `/version` is stamped `0.0.0-unavailable` rather than failing the freeze, per the fail-safe rule. |
| T-016 | done | 2026-09-05 | `UnderwriteCase` Temporal workflow with activities freeze, evaluate_policy, score_models, run_committee, decide, record_decision, record_human_decision, issue_token, plus the `human_decision` signal and `state`/`decision` queries. 12 tests on the time-skipping test server. A blocked case (ELG-02) is recorded and routed to a human without ever reaching the models or the Council. Committee and model steps are marked placeholders that force model_health RED and the STANDARD tier, so a partial run cannot be mistaken for a complete one. The SLA escalates but never decides: `wait_condition` raises on timeout, the workflow marks itself escalated and keeps waiting for a person. Workflow id is derived from the snapshot, so a repeated start is refused. |
| T-020 | done | 2026-09-05 | Synthetic population: 120 employers, 5,000 members, 7,553 accounts, 86k schedule rows, 86k payments, 120k deductions, 120k savings points, plus shares, guarantors, bureau, outages and arrangements. 22 tests. Generates in 1.3s and loads into the core stub in 59s, both far inside the 5-minute budget; a re-run with the same seed is byte-identical. All six sanity ranges from docs/10 §4.6 hold on four different seeds. Notes: (a) the archetype miss rates are calibrated, not taken literally from §4, because §4's stated 20% CHRONIC miss rate cannot produce the >25% CHRONIC delinquency §4.6 requires; (b) `savings_paused_share` counts only members who stopped saving, not CHRONIC members who never saved; (c) the drift window metric is measured over drift members with 14 months of history, since an account opened in month 22 has no clean run to drift away from; (d) core-stub bulk insert now binds scalars as text and casts by the real column type, because asyncpg will not coerce an ISO date string. **Revised 2026-09-06 (T-033):** a separately seeded post-pass now gives 428 members (8.6%) a `contact_updated_at`, without which the INT-06 rule in docs/07 §3 could never fire. It runs after every other draw, so every persisted field except that one is byte-identical to what was measured above; the population digest changes, the row counts do not. The loader also now carries `application_ext`, which it never did: 604 applications are loaded, and `application_count_12m` was constant until they were. |
| T-021 | done | 2026-09-05 | Per account-month labels (late7/30/60/90, cure, restructure, charge-off) derived from the payment history alone, never from the archetype that produced it. 33 unit tests on hand-built sequences, pinning every threshold at its boundary and proving the ladder is monotonic. 86,482 account-months loaded into `core.outcome`: late7 11,629, late30 6,483, late60 1,523, late90 344, charge-off 333, cure 6,394, restructure 540. The late30 rate of 7.5% matches the portfolio sanity range independently measured in T-020. |
| T-022 | done | 2026-09-05 | Document corpus: 12 structurally distinct payslip layouts plus bank statement, identity card, employment letter and provident-fund statement, rendered through Chromium on aarch64 to PNG and PDF at 200 dpi. `--apps 600` produces 2,155 documents in 6.5 min, every one with ground truth and DOM-derived normalised bounding boxes, 10.3% clean digital, 61 injected anomalies across all seven kinds. 25 tests. **Two measured deviations, both recorded because they change what T-024 can do:** (a) `PAYSLIP_LATEST_3` is one document covering three periods rather than three documents, because policy names it as one required type and reconciliation takes the median net across the three; (b) the 64-bit page phash in docs/07 §1.4 cannot separate documents at all. Measured over this corpus, two identity cards for different members sat a median of 2 bits apart and 100% of cross-member pairs fell inside the threshold of 6. Widening to 256 bits and giving each card a per-member photograph brings that to a median of 24 and about 2% of pairs. The residual overlap is inherent, so T-024 must treat a hash match as a candidate to be confirmed against extracted content, not as a HIGH finding on its own. **Revised 2026-09-06 (T-033):** all 600 applications shared a single date, which made a seven-day velocity window meaningless and a duplicate-application check unable to tell a repeat from a coincidence. They now arrive over the ninety days before the demo as-of, clustered toward the present, and each bundle's pay periods and confirmation-letter date follow its own application date rather than a fixed month. The letter date in particular was derived from a period label, which post-dated four clean letters and left three injected stale ones inside the threshold. Corpus regenerated: 2,157 documents, 56 injected anomalies. |
| T-023 | done | 2026-09-05 | document-service: ingest with MIME/size/page checks and presigned upload, classification, OCR extraction with bbox anchoring, EvidenceRefs, and human review that supersedes rather than overwrites. 40 tests. **Measured over 400 documents: classification 100% (target 98%), critical fields 96.7% (target 95%), bbox and confidence coverage 97.5%.** Every critical field individually: payslip period 100%, gross_salary 100%, employer_name 97.5%, net_salary 96.2%; identity dob 100%, id_number 92.8%, name 90.1%. Notes: (a) the extractor is pluggable, so the vision route in T-040 replaces the OCR backend behind the same interface; (b) the declared document type is a hint only, never the classification; (c) render DPI was raised to the documented 200, without which the identity card was too small for OCR to read at all; (d) `norm_value` is read through a driver-agnostic decoder because asyncpg already decodes jsonb. **Re-measured 2026-09-06 on the regenerated corpus:** classification 100%, critical fields 96.0%, bbox and confidence coverage 96.5%. |
| T-024 | done | 2026-09-05 | Forensics (arithmetic, metadata, reused image, layout signature, copy-move, readability) and cross-source reconciliation (income across payslip/employer/bank, employer match, identity match, duplicate identity numbers). 39 tests. **Measured: 74 of 76 injected anomalies detected (97.4%), integrity false positives 1.12% against a 3% ceiling.** Five of seven anomaly kinds detected in full. The two misses are documents whose critical field OCR failed entirely; both raise DOC-04 and route to a human, so nothing passes silently. **Three deviations, each with evidence:** (a) docs/07 §1.4 assigns INT-02 HIGH on a page-hash match alone, which T-022 measured cannot separate documents; an unconfirmed match is recorded LOW as a lead and only a content-confirmed or byte-identical match is HIGH; (b) the arithmetic and income checks refuse to run on figures read below 0.75 confidence, because an unreadable document is a DOC-04 problem and not grounds to accuse anyone; (c) 33.7% of clean documents raise DOC-04 at the policy's 0.85 confidence floor, reported separately from integrity false positives since asking for more information is not an accusation. Note: entity resolution uses rapidfuzz only; the bge-m3 embedding half of docs/07 §1.5 needs the LLM gateway and arrives with T-041. **Re-measured 2026-09-06 on the regenerated corpus: 52 of 56 injected anomalies detected (92.9%), integrity false positives 0.00% against the 3% ceiling, and all four misses route to a human on DOC-04 rather than passing.** Three fixes got the false positives from 3.46% to zero: a net that is not a plausible share of gross is a reading failure rather than a discrepancy, so the arithmetic and income checks decline it; a check that declines now says so as DOC-04 instead of falling silent, which is what let an inflated net pass unexamined behind a deduction line read at 0.52 confidence; and the confirmation-letter date fix in T-022 removed four post-dated clean letters. The DOC-04 rate is 36.8%, reported separately because asking for a clearer copy is not an accusation. |
| T-025 | done | 2026-09-05 | member-intelligence-service: monthly-partitioned `member_event` (61 partitions covering the seeded window plus headroom), core-record import, content-addressed profile projection, cursor-paginated timeline, and the derived views the tools read. 40 tests. **Import of the full population: 453,195 events across 5,000 members in 2m42s, of which 172,631 are payment events, matching the ~180k the acceptance names.** The remainder are the deduction, savings, share and arrangement sources docs/07 §4.1 also lists. Event ids are derived from the source record, so a replayed import updates rather than duplicates, which is what makes the change feed safe to replay. Paging is verified to lose and repeat nothing across the whole timeline, and the projection is checked against the core stub for 100 randomly sampled members. A missed deduction is inferred from an absence, so it carries `validation: WARN` rather than being presented as an observation. |
| T-030 | done | 2026-09-06 | feature-service: a 23-feature registry, reproducible snapshots and a permitted-use filter. 36 tests. Every feature declares its family, window, source, permitted uses, dtype, version and monotone direction, and the registry is published to `feature_def` so a model run can cite the definition it used. The registry covers all five Decision Factors: CAPACITY 4, CONDUCT 7, COMMITMENT 6, CONDITIONS 2, INTEGRITY 4. Purpose scoping narrows that to 23 for underwriting, 11 for collections and 5 for fraud, so a collections call cannot see the proposed debt-service ratio. Values are computed from the member timeline and the core record as of a stated moment, each with its own provenance row, and the set is hashed into an `inputs_digest`; recomputing the same member at the same moment reproduces the digest exactly, and a snapshot with an equivalent digest is reused rather than recomputed. A database trigger refuses UPDATE on `feature_value`, because a model run cites a snapshot and a snapshot that drifted would make the decision unreconstructable. A structural test refuses any feature whose name or description mentions a protected characteristic. Note: these tests read the seeded population, so they run against the demo database rather than `cio_test`, and each test deletes only the snapshots that appeared while it ran instead of truncating.
| T-031 | done | 2026-09-06 | Credit-risk training: weight-of-evidence scorecard champion, LightGBM challenger, Platt calibration, isolation-forest out-of-distribution score, SHAP-equivalent drivers mapped to approved reason codes, and a generated model card. 89 tests. The training frame is built by calling the same `compute_features` the risk service calls, as of the day before each facility opened, so there is no second implementation to drift and the new facility is not an input to its own decision. **Measured: champion hold-out AUC 0.7190 (95% CI 0.620-0.814), challenger 0.7594 (0.675-0.840), champion calibration slope 0.763.** The challenger meets the 0.72 target; the champion falls short by a thousandth and the calibration band of 0.9-1.1 is missed. Both shortfalls are recorded in the card and pinned by tests that fail if they are ever silently fixed. **Root cause, measured four ways:** the hold-out period's defaults are mostly members who looked ordinary at origination and drifted afterwards. SLOW_DRIFT accounts default at 0.66% in the training window and 14.29% in the hold-out, and a model handed the generating archetype outright reaches only 0.803 there against 0.902 in training. On calibration: 4,500 configurations never exceeded 0.775; correcting the level alone leaves the slope unchanged, so it is spread and not base rate; cross-fitted calibration over 48 events is worse than validation-only at 19; and the walk-forward decay estimate ranges from 0.218 to 1.264 across folds, so no honest allowance can be drawn from it. This is the case for the Longitudinal Member Intelligence engine rather than a defect in the scorecard. **Four deviations, each forced by measurement:** (a) accounts whose twelve-month performance window has not finished are excluded, 3,479 of 7,553, because counting them as good would teach the model that recent lending never defaults; the spec's month indices are therefore applied as proportions over the 13 labellable cohorts. (b) `doc_min_conf`, `contact_change_days`, `application_count_12m` and `findings_max_severity` are dropped from training: they are application-time inputs with no historical counterpart, 100% missing or single-valued, and they remain live inputs to the policy engine and the fraud service. (c) The information-value floor is 0.05, not the 0.02 first used, because a column of pure noise reaches 0.034 across five bins at this sample size. (d) `num_leaves` 15 from the spec is kept in the search but 7 wins, since sixty-odd defaults cannot fill fifteen leaves. **Three defects the tests found and fixed:** an empty bin was given the weight of a bin made entirely of defaults, so the first unseen employer sector would have scored as the riskiest case ever seen; additive smoothing on unequal bins inverted the weight order on two characteristics, defeating the monotone constraint, now replaced by size-aware shrinkage plus a guaranteed isotonic pass; and a first-time borrower scored a perfect 1.0 for conduct because every count read zero, which is an absence of evidence and not a clean record. Fixing the as-of date to the day before origination also removed a leak worth 0.13 of univariate AUC on `utilisation`.
| T-032 | done | 2026-09-06 | risk-service: `POST /risk/score` over a frozen feature snapshot, returning champion and challenger probabilities with grades, the deterministic CONDUCT factor, approved reason codes, drivers, contract-valid EvidenceRefs and an out-of-distribution score; plus `GET /risk/runs/{id}`, `GET /risk/members/{id}/runs` and a `/version` that names the model rather than the service build. 36 tests. **Measured over 50 warm calls: median 13.7 ms, p95 17.2 ms, worst 53 ms against the 200 ms budget.** Run ids are derived from the model version and the inputs, so re-scoring the same snapshot reproduces the id, the probability, the calculation id and the evidence ids rather than minting a parallel record that says the same thing under new names; the insert is content-addressed and a second call writes no second row. A recorded run is frozen by a database trigger, because a DecisionRecord cites it. **Three structural changes this task forced:** (a) the Decision Factor formulas moved from `services/policy/app/factors.py` into a new shared library `libs/cio_dff`, because the risk service produces the CONDUCT score the policy engine consumes and two implementations of one formula are two chances for a decision to be irreproducible; policy re-exports them so its 243 tests read unchanged. (b) `create_app` gained a `version_detail` hook: the scaffold registers `/version` before any service router and would otherwise shadow it, which matters because the CaseSnapshot freeze stamps whatever that route returns. (c) `ml` cannot be an installed package, since its package directory is its own project root and an editable install cannot add a path prefix, so `cio_common.models.add_model_path` locates it by walking up for the marker file and the service image now copies `ml` in alongside `app`. **Two deviations:** an unreachable feature service fails as INTERNAL rather than a new error code, because docs/08 fixes the vocabulary at eight codes and adding one needs an ADR; and `history_months`, which the CONDUCT thin-file branch keys on, is read from the snapshot's `due_events` provenance because the feature registry carries no months-of-history feature. Evidence is minted only for the characteristics the champion actually used, so the response does not claim the model consulted values it never read.
| T-033 | done | 2026-09-06 | fraud-service: rules, entity resolution over a networkx guarantee graph, cycle and serial-guarantor analytics, branch concentration, an advisory isolation forest, and `POST /fraud/assess`, `GET /fraud/signals/{case}`, `GET /fraud/graph/{case}`. 93 tests. **Measured: the planted seven-member ring is found as a cycle in all six of its cases, and clean cases raise a finding 15 times in 598 (2.51%) against the 3% ceiling.** The duplicate applicant is found through the document service's INT-04, escalated to HIGH. Assessments are content-addressed and frozen by a trigger, and the finding, calculation and graph ids all derive from what was found, so re-assessing an unchanged case reproduces the record rather than a second one saying the same thing. **The guarantor ring had to be planted:** docs/10 §7 specifies it and nothing built it, so `synthetic/rings.py` adds one deterministically after generation, leaving every count T-020 measured untouched and reporting its own rows separately. **Three thresholds deviate from docs/07 §3, each because the specified one was measured to fail the acceptance:** (a) a guarantee cycle raises nothing unless two of its members borrowed within ninety days of each other; measured, 4.4% of members sit in some cycle, and raising INT-05 on every one puts clean cases over the ceiling by itself. (b) The concurrency test is a sliding window, not the spread of every application in the loop; taking the spread, a single member who had borrowed three months earlier stretched the planted ring to 107 days and the ring raised nothing. (c) Velocity requires the burst to be three times the employer and branch's own rate as well as three in seven days; the absolute threshold alone fired on 14 of 598 clean cases. **Two data gaps closed:** all 600 applications shared a single date, which made every time-based rule meaningless, so they now arrive over the ninety days before the demo as-of with each bundle's payslips following its own date; and no member had a `contact_updated_at`, so INT-06 could never fire, which a separately seeded post-pass fixes for 428 members without disturbing any other generated value. The application register was also never loaded, which is why `application_count_12m` was constant in T-031; the loader now carries it and 604 applications are in `core.application_ext`. Two reads were added to the core stub, which owns the data: a guarantee neighbourhood walk and an application-velocity window.
| T-034 | done | 2026-09-06 | Explainability in governance-service: `POST /explain/factors` returns the five structured levels docs/07 §6 requires (policy reason codes with clause ids, the factor table with the decisive flag, model drivers with reason codes, document and finding provenance, and the human decision with its override), plus `GET /governance/models` and the model cards behind it. 30 tests. **The acceptance is enforced structurally, not by inspection:** every platform identifier in the assembled explanation is collected and checked against the identifiers present in the records it was built from, and an explanation citing anything else is refused rather than trimmed. Trimming would hide the defect, and a narrative built on an invented evidence id reads as proof. A record that could not be fetched is named in `unavailable` and its level comes back empty; nothing is filled in from elsewhere. The inventory lists untrained families rather than hiding them, and reports which version each service is actually serving.
| T-040 | done | 2026-09-06 | llm-gateway: four provider adapters behind one route abstraction, deterministic per-request PII masking, JSON-schema enforcement with one corrective turn, per-run token budgets, a per-route circuit breaker, and `/llm/complete`, `/llm/vision`, `/llm/embed`, `/llm/rerank`, `/llm/health`, `/llm/warmup`. 68 tests against a fake provider that is a real adapter rather than a mock built in the tests. **Verified on the Spark: `make warmup` reports 6 of 6 routes ready against local models on the GB10.** A structured completion through the whole stack returned schema-valid JSON in 8.0 s with three values masked and unmasked again. Masking works two ways because one is not enough: structured identifiers are found by shape, and names and addresses are masked from values the caller supplies, since guessing at a name either leaks one or corrupts the prompt. A schema failure is a 422 and not an outage, so the caller degrades rather than retrying a healthy provider; a provider failure opens that route's circuit for thirty seconds. Budgets are checked before a call, because refusing once the tokens are spent is not a budget. **Three environment defects found and fixed:** the vLLM services had no GPU reservation and would have started, found no device and served nothing; `vllm/vllm-openai:latest` is CUDA 12 and fails on this driver with cudaGetDeviceCount error 803, so the image is pinned to `cu130-nightly`; and one variable held both the checkpoint vLLM loads and the name the gateway asks for on the wire, so setting either broke the other. **One deviation:** docs/02 §4.1 names Qwen3-30B-A3B for the agent route; Qwen2.5-7B-Instruct is served instead because it was already in the model cache and docs/02 says to verify availability at build time. Embedding and reranking run on the fake provider until bge-m3 is loaded, which the index reports rather than hiding.
| T-041 | done | 2026-09-06 | RAG index over the policy corpus: the corpus is generated from the policy packs, chunked by clause, and indexed with both a full-text vector and an embedding column scored half each. 20 tests. **Measured: clause-id questions return the right clause in the top three 34 times out of 34, against a 90% target. Questions in an officer's own words, reported beside it rather than as an acceptance, reach 12 of 14.** The corpus is generated rather than written because the value of retrieval here is that the clause an agent cites is the clause the engine ran; a corpus maintained by hand drifts within a release and then a citation looks like provenance without being it. Clause ids are the rule ids. **Three defects had to be fixed to reach that number:** requiring every query term scored zero whenever a question carried one noun the clause did not use, so terms are ORed and ranked with an exact-match bonus; identifiers are split on dots before indexing, without which the searchable nouns of a rule stay locked inside `member.tenure_months`; and the clause-id pattern required three letters, which silently dropped every routing rule from the index. An index built without embeddings is lexical-only and says so rather than ranking against a zero vector.
| T-042 | done | 2026-09-06 | agent-runtime: bundle loader with `agent_version` over prompt, tools, config and route model; context assembly in the documented order; the invocation loop with one corrective turn; guardrails; and `POST /agents/invoke`, `GET /agents`. 49 service tests plus 47 guardrail tests. All ten Council and longitudinal agents have bundles whose prompts are the normative text of docs/06 §5 verbatim, pinned by a test that fails if a word drifts. **All three acceptances are enforced structurally.** A schema failure gets one corrective turn and then produces a DEGRADED opinion with stance NEED_MORE_EVIDENCE and confidence zero, because a gap in the record looks like an agent that had nothing to say. An injection is neutralised by wrapping rather than by removal: the text still reaches the agent assessing the document, inside a `{"data": ...}` object the preamble has already defined as data, and the attempt is reported separately as a fact about the document. A claim without evidence, a claim citing evidence from another run, and a claim carrying a number no tool produced are each rejected; rounding a tool's number is allowed, inventing one is not. The runtime sets the opinion's id, agent id, version, round and timestamp itself and asks the model only for what it decides, because an agent that could set its own version could claim to be a different agent. Every opinion is signed over its own content. **One addition to the classifier from a failing test:** a bare `SYSTEM:` or `assistant:` prefix is the cheapest impersonation there is and was not caught, while `Employer:` and `Period:` must not be.
| T-043 | done | 2026-09-06 | Council agents v1: 31 tools over the service APIs, five golden cases carrying both the snapshot an agent sees and the tool results it gets, and a test matrix running every Council agent over every case. 71 agent-runtime tests. **Measured against the live model on the GB10: 22 of 25 agent invocations produced a valid AgentOpinion**, with stances that follow the evidence: S1 unanimous SUPPORT on a clean case, S4 `policy_affordability` BLOCK at confidence 1.00 on the affordability breach, S3 and S5 `fraud_integrity` LEAN_OPPOSE and OPPOSE on the tampering and the ring. The three failures were two queue timeouts and one claim citing an evidence id the agent was never given, which is the guardrail working. Factor scores are asserted equal to the tool that produced them. **Six tools are deliberately absent:** `features.temporal`, `baseline.get`, `changepoints.get`, `state.get`, `lmi.score` and `survival.get` belong to the longitudinal engine and arrive with it. A tool returning something plausible from a service that cannot answer would be worse than its absence. **Five defects a fake gateway could not have shown, found by running the real Council:** (a) vLLM cannot compile `propertyNames`, which the AgentOpinion contract uses for `factor_scores`, so the schema sent for guided decoding is rewritten into the subset a grammar engine takes while the runtime still validates the real contract; (b) a 400 from the provider was counted as an outage and opened the route's circuit, so one caller sending an uncompilable schema would have taken the route down for everyone; (c) the whole contract bundle went out as `$defs` on every call, and pruning to what the schema reaches cut twelve definitions to three after an untrimmed five-agent round passed 28,000 tokens; (d) the output screen counted only tool results as sources for numbers, so an agent quoting a tenure from the case summary it was handed, or a window from a field named `ontime_rate_24m`, was rejected for reading what it was given; (e) a 700-token output ceiling truncated opinions mid-JSON, which the caller saw as "the answer was not JSON" rather than as a limit. **Two timeouts were too short for a model:** the API gateway held every upstream to a reader's 60 seconds, and the LLM route to 90, when one agent decodes in about 40 and five contending for one GPU stretch past both. Both now distinguish a model call from a query.
| T-044 | done | 2026-09-06 | committee-orchestrator: tier selection, the state machine through ASSESS, CHALLENGE, REPAIR, REVISE, SYNTHESIZE and NARRATE, per-agent deadlines, run and opinion persistence, and `POST /committee/runs` idempotent on snapshot and tier. 37 tests. The rule the design turns on is that the deliberation may fail and the decision may not: an agent that times out leaves a degraded opinion rather than a gap, because a gap in the record reads as an agent with no concerns; a narrator that cannot be reached leaves a deterministic template marked DEGRADED, written only from the record's own fields; and the Synthesizer runs whatever happened above it, because a case with no recommendation is a case nobody can act on. A run that exhausted its budget tells the Synthesizer the model health is RED, so a partial deliberation is not weighted as a complete one. A degraded agent contributes no factor score, because a missing factor is missing rather than zero. Opinions are written as they arrive and frozen by a trigger. **Two test defects worth recording, both about state that outlives a run:** a fixture that reused one opinion id made every insert after the first a silent no-op under `ON CONFLICT DO NOTHING`, and a fixture sharing one snapshot id across tests meant each test joined the run its own previous execution had left in the table and asserted against that.
| T-045 | done | 2026-09-06 | Wired the underwriting workflow to the real services: document gathering, a frozen feature snapshot, risk and fraud scoring, and the committee orchestrator replace the P1 placeholders. 17 workflow tests. Documents and features are gathered concurrently because neither depends on the other, and the feature snapshot is frozen once before either model runs so risk and fraud reason about the same inputs and the decision cites one snapshot rather than two. **Tier selection moved out of the workflow.** It lived there as a placeholder and now belongs to the committee service beside the policy pack that defines it, so the rule has one implementation rather than two that can disagree; the workflow gathers what the choice needs and passes it. **Any of three failures now forces a human route rather than looking like a clean result:** an unavailable model, an unreadable case file and an uncomputable feature snapshot each set model health to RED, because none of them is the same as finding nothing (docs/13 §7). The committee step gets a twenty-minute activity deadline against thirty seconds for the reads, since a Council round is minutes of model decoding rather than a query.
| T-046 | done | 2026-09-06 | Web app foundation and Officer Workbench v1: React 19 + TypeScript + Vite, router, in-memory session, a typed API client and the queue, case, decision-card, evidence and discussion views. **Playwright acceptance passes against the running stack, not a mock:** four tests sign in as an officer, open S1 from the queue, and compare every figure on the decision card with the DecisionRecord the API returned; clicking a cited claim opens the rendered page with the extractor's own box drawn on it, and the overlay is asserted to carry the same coordinates the explanation cited and to sit inside the image. Nothing is asserted against a literal, so a test cannot pass while the screen and the ledger disagree. `scripts/seed_demo_case.py` (`make seed-demo`) puts the five golden cases in front of it by the real path: it uploads a rendered document set from the synthetic corpus, has the document service classify and extract it, evaluates every hard gate, synthesizes a record and appends it to the ledger. **Nine defects found by building the screen, seven of them outside the UI.** (a) Every service image was built without the optional dependency stacks, so document, risk, fraud, governance and the agent runtime imported nothing and served only `/health`; the image now installs all extras and copies `ai/` and `policy_packs/`. (b) The policy and committee services located policy packs by counting three directories up from `__file__`, which is right in a checkout and wrong in the image; `cio_common.assets` searches for the directory instead. (c) Tesseract was not in the image at all, and its absence was silent: documents came back classified `OTHER` at 0.0 confidence with no fields, which reads as a blank page rather than an unread one. The binaries are installed and the pipeline now refuses with `READER_UNAVAILABLE` rather than reporting an empty extraction. (d) The explanation named which document a value came from but never where on it, because the provenance level listed documents and not the fields read from them; it now carries one entry per extracted field with its box, which is what makes a claim checkable. (e) A DecisionRecord carries no `case_id`, so the governance service fell back to the snapshot id and found no documents; the decision service returns the ledger's own column alongside the record. (f) The officer queue listed every record ever appended, so a re-assessed case appeared several times and invited action on a superseded recommendation; it now returns the current record per case, and the test asserts exactly one row. (g) The page image was an `<img src="/api/...">`, which sends no bearer token and could never load; it is fetched through the client and shown as a blob URL, because the alternative — the token in the query string — would put it in access logs. (h) The case page rendered the narrative object directly and crashed React, since each audience carries `{text, status}` rather than a string; the status is now shown per audience, as one can be model-written while another is not. (i) `docker/.env.example` omitted nine variables compose reads, and the environment check called a busy host unfit to start when the memory it wanted was held by the platform's own containers. |
| T-050 | done | 2026-09-06 | Tier 2 repair and revise, replacing a loop that named the states and did nothing. The Challenger names a gap, the orchestrator calls the tool itself through a new `POST /tools/call` on the agent runtime with `principal=workflow`, the fetched evidence is added to what the affected agents see, those agents revise with the prior round, and the Challenger sees the result. **The second CHALLENGE is the part that matters:** without it a Challenger can never withdraw a reservation the repair answered, and the same gap is repaired until the bound. Bounded at two loops; 21 new tests, including that an unsatisfiable Challenger ends at two loops and the run still reaches DONE with a record. Evidence no tool can fetch becomes an L1 REQUEST_DOCUMENT ActionProposal that reaches the record, so an officer sees what was wanted rather than finding it in a log. **Repair may read and may register a request; it may not act.** The endpoint refuses a writing tool, refuses any principal but the workflow, and pins case and member scope to the run, so a Challenger cannot move a case by naming an action tool. A tool that could not be reached is recorded as unreachable rather than dropped: the next round has to tell "we looked and found nothing" from "we never looked". **Three defects found in the process.** (a) The Synthesizer was given every opinion ever written, so a revised agent counted twice in the disagreement measure and the Challenger argued with a position the agent had already left; it now receives the opinion that stands per agent, and the run record keeps the whole deliberation. (b) `proposed_actions` was in the DecisionRecord contract and never populated by anything. (c) The counterfactual for a blocking reservation said `APPROVE` unconditionally, which on a weak case is the record making an offer it cannot keep; it is now computed by running the same hierarchy over the same case with the gap closed, because a second derivation of the answer can disagree with the first. `would_change_outcome` gained an optional `new_route`, since a reservation on a case that is already scored moves the route rather than the recommendation. |
| T-051 | done | 2026-09-06 | Autonomy Dial administration, the kill switch and the sampling queue. `POST/GET /autonomy/{product}` needs two distinct approvers, both heads, and records the change as an amendment in `app_policy.policy_version` rather than as a new pack: the credit policy has not changed, and re-issuing it would make every decision look as though it had. A superseded setting is kept, so an auditor asking what the dial read last Tuesday gets an answer, and the DecisionRecord names the amendment it was decided under. `POST/DELETE /kill-switch/{product}` takes one owner, because needing a second opinion is how a stop gets delayed, and it overrides the setting without overwriting it, so releasing restores what the institution chose. `GET /samples` and `POST /samples/{id}/review` are the sampling queue: a sampled autonomous decision joins it in the same transaction that records the decision, its id derived from the record so a replay queues one review, and the verdict joins the hash chain. 41 new tests, plus `scripts/autonomy_drill.py` (`make autonomy-drill`), which runs the whole loop against the stack and passes. **Two defects found, both about failing safe.** (a) The synthesizer took `kill_switch_active` from the caller, so a caller that forgot the field got a decision made as though the switch were off; the service now reads it, and an explicit true from a sandbox still holds. (b) The autonomy router ranked integrity severity with `list.index` over a list that omitted `NONE`, so the cleanest possible case raised `ValueError` instead of routing; the lookup is now total and an unrecognised severity is treated as the worst, because letting an unknown through to an autonomous approval is the wrong way to be wrong. **And one that had been destroying data all along:** five service test suites truncate the tables they exercise, which is the only way to test a chain from a known start, but they were pointed at the demo database, so every `make test` silently emptied the loaded documents and the decisions in front of the workbench. Tests now use a `cio_test` database, created on first use; the demo database is touched only when someone names it through `TEST_DATABASE_URL`. |
| T-052 | done | 2026-09-06 | execution-service: the only thing in the platform that changes anything outside it. `POST /action-proposals` records a proposal; `POST /actions/{id}/execute` checks the kill switch, validates and consumes the approval token, claims the action with a single conditional UPDATE, and runs a saga. `APPROVE_FINANCING` activates the facility then sets the status, and compensates the activation when the status write fails, because an account created and never approved is a member holding a facility nobody decided to give them. 26 tests, plus `scripts/execution_drill.py` (`make execution-drill`), which runs the whole write path against the stack: the account is opened, the replay is a no-op, and the spent token is refused on a second action. **Four decisions worth naming.** (a) The kill switch is checked before the token is spent, so a stop does not burn an approval a person will have to issue again. (b) Idempotency keys derive from the action, never from the attempt: a key per attempt would open a second facility for the same decision, which is the exact failure the key exists to prevent. (c) A retry after a core refusal does not need a second token, because the first was consumed and the case has not been decided again since; presenting a *different* token on a retry is refused, so a second approval cannot be spent on an action that already has one. (d) FAILED is not terminal. A core that refuses leaves the action retryable and the case pending, and the workflow finishes rather than failing: failing would lose the approval and make somebody re-approve a decision because a write timed out. A rollback that itself fails is recorded in the error text naming the open unapproved account, because somebody has to go and look at it by hand. **Deviations:** `app_execution.action` and `app_execution.saga_step` are not in docs/04, which defines no execution tables; they are added here because a proposal and the steps taken on it are durable state, not messages in flight. Before-and-after audit is emitted through the outbox as `action.executed`/`action.failed` rather than written to `audit.entry`, because the audit service owns that table and arrives with T-053. |
| T-053 | done | 2026-09-06 | audit-service and the ledger viewer. `audit.entry` is a second hash chain, independent of the Decision Ledger on purpose: one implementation would be one place to subvert both, and an auditor comparing them would be comparing a thing with itself. `POST/GET /audit`, `GET /audit/verify`, `POST /audit/export`, `GET /reconstruct/{case}`. 32 service tests plus 3 browser tests. **The trail is fed from the transactional outbox, not by synchronous calls.** Some things that must be audited happen after an irreversible side effect: a facility is activated in the core and then the platform records that it did. A synchronous write means an audit service that is down can fail a write which already happened, and swallowing that failure means an action nobody can account for. Producers emit in the same transaction as the act; the audit service drains the outbox, so the entry cannot be lost and the write cannot be blocked. **Reconstruction is a read across services, never a store.** A second copy of the history is a second thing that can disagree with the first. Anything that could not be read is named in `unavailable` rather than left as a gap, because a reader takes a silent gap for an absence of events. Measured: **S2 reconstructs in 49 ms** against a two-minute budget, with its documents, its ledger entries and its audit trail in one timeline. **`scripts/tamper_drill.py` (`make tamper-drill`) passes:** both chains verify green, altering one row in each turns both red, and restoring the rows turns both green again. It found a defect in itself first: the drill wrote the value that was already in the row, so it reported a clean chain as proof of nothing. It now flips the value. **The web `/ledger` route** renders the timeline with every hash, a chain-verification badge, expandable entries and a JSON export, and normalises the ledger's own word (`intact`) so a screen looking for the wrong key cannot show "not verified" on a chain that verifies. **Two deviations.** The audit trail is written through the outbox rather than by `cio_common.audit.record()` for the after-the-fact cases; the synchronous helper exists and raises rather than swallowing, for callers that can still fail. And the demo's `audit-worm` bucket was created before object lock was configured, so today's export reports `locked: false` with a warning saying it is a copy and not WORM; compose now creates the bucket `--with-lock` and sets a ten-year governance retention, so a stack brought up from empty gets a real WORM archive. The bucket was not deleted to fix it in place: deleting an audit archive is the one instinct never to follow. |
| T-054 | done | 2026-09-06 | Human decision UI and override analytics. The workbench now actually decides: approve, approve with conditions, decline, request information, escalate and defer all post to `/human-decisions`, which was already enforcing authority and override reason codes and had nothing calling it. Governance gained `GET /governance/overrides` (a daily series, a breakdown by reason code and by action, and a rate) and `GET /governance/attention`, the compliance list. `GET /human-decisions` on the decision service is the index those read from. 6 governance tests, 3 browser tests, and `scripts/override_drill.py` (`make override-drill`), which decides a case within authority, is refused one beyond it, is refused an override with no reason, records one with a reason, and finds it in the series. **An override is derived, not declared.** What counts as departing from the recommendation is computed from the record rather than left to the officer to tick, because an officer who forgets to tick it makes the count meaningless, and the count is the whole point. **The screen is a courtesy and the service is the control:** the browser test asserts the disabled button and then calls the API directly with the same role and gets 403. **A defect the wiring exposed:** the UI decided who could approve by comparing an amount ceiling against the required authority, and passed the weighted score in as the amount. A role now carries its rung on the approval ladder separately from the amount it may commit, which are different questions that had been conflated; the old rule would have offered a head of credit an approval on a case needing committee authority. **Deviation from docs/09 §3.4:** the spec says an action beyond a role's authority is hidden. It is shown disabled with the reason instead, and the open actions stay enabled, because an officer who cannot see that approval exists cannot tell whether to escalate the case or to leave it. **Two zero-rates are kept distinct:** an override rate of 0.0 means nobody overrode, and `null` means nobody decided; reporting the second as the first would be a claim about people's judgement that nothing supports. |
| T-060 | done | 2026-09-06 | Temporal features and personal baselines. `ml/lmi/` holds the arithmetic as pure functions over series, so a behaviour can be built by hand in a test and the numbers checked against it; `services/lmi` materialises them. 41 tests. **Measured: 5,000 members in 2.9 seconds against a 10-minute budget**, because the read is four queries for the whole book rather than four per member. **Every feature is paired with a member's own baseline, which is the whole point.** A member who has always paid three days late has not changed when they pay three days late; one who has always paid on the day has, when they pay two days late twice. A test pins each of those. **Four decisions worth naming.** (a) Unpaid is not "very late": a due event with no payment against it is excluded from the timing series rather than counted as a large number, because averaging the two lets one unpaid instalment look like slowness. (b) A window with no due event in it reports nothing rather than zero, because recording a quiet month as perfect punctuality is how a gap looks like an improvement. (c) A baseline needs six observations before it is usable; below that the robust z is 0.0, since an unknown habit is not evidence of a departure from it. (d) The trend runs over due events rather than calendar days, because monthly instalments give twelve points a year and a regression on the date axis is dominated by the gaps. **Two defects found by looking at the output over the real population.** A member whose deduction shortfall has been zero every cycle has a median absolute deviation of zero, and the epsilon guard turned a 453-unit shortfall into a robust z of 4.5e11; the score is now clamped at ±10, where the number stops meaning anything. And `late_streak` fired on 42% of the book, because paying two or three days after the due date is the habit in this population rather than a deterioration; a second measure, `streak_beyond_habit`, counts only what is late for that member, and 883 members carry three or more. It is not called a late streak: for someone who habitually pays three days early, paying on the day is beyond habit and is not late. **Two gaps stated rather than papered over.** The core carries no contact history, so the interaction family computes over an empty list and reports zeros that are true; filling it with plausible contacts would put numbers on screen that no record supports. And **no member in this population has enough history for the seasonal adjustment**: STL needs 24 monthly points and accounts here average 17.3 due events, so it is implemented and tested against constructed series but never fires on real data. Each feature set says which of these applies. |
| T-061 | done | 2026-09-06 | Change-point and anomaly detection. One-sided CUSUM per signal, confirmed against PELT, with an advisory Isolation Forest over the whole feature vector. 34 new tests plus `scripts/changepoint_eval.py` (`make changepoint-eval`). **Acceptance met on a held-out half of the population:** the parameters were chosen on one half by member id and the numbers reported on the other, because a detector tuned until it looks right on the set it was tuned on has learnt the set. On 1,993 held-out members, 83.1% of SLOW_DRIFT members were seen before their first late payment, **median lead 92 days** against a 21-day target, and STEADY members produced **0.21% false alarms per member-year** against a 1.5% ceiling. **The measurement failed three times first, and each failure was a real defect.** (a) The baseline was computed over the whole series, which is lookahead, and on a drifting member it is lookahead in the worst direction: their median is pulled up by the drift being looked for, so the early drift reads as normal. `rolling_z` now judges each observation against the ones before it, and detection went from 2.4% of drift members to 83.1%. (b) Steady payers fired 19% a year because payment timing is recorded in whole days and their median absolute deviation is one day, so a two-day swing read as a large departure and the detector was measuring rounding; a per-signal floor on the scale cut it to 9.8%, and the measured threshold took it to 0.21%. (c) "Late" meant any positive day count, which in this population is a member's second instalment, making early warning impossible by definition; it now means the platform's own first rung, more than seven days past due. **Two dates, not one.** CUSUM fires several observations after a level shifts, so the alarm carries where the drift began and where the sum crossed the line; they are 92 days apart on average here, and an officer told the case changed on the firing date would look at the wrong payslip. Confirmation is measured against the onset for the same reason, and the window stretches to three observations, because 30 days on monthly instalments is one observation and PELT routinely lands two from where a level shifted. **PELT confirms a level change and cannot confirm a trend.** Payment timing is confirmed about a fifth of the time; a savings balance slopes and an RBF cost finds no break in it, so confirmation never fires there. The unconfirmed alarm is still reported: it is the confirmation that is missing, not the change. `config/lmi.yaml` says which signals confirmation applies to. **The deduction signal was measured against a fact rather than a label** — two consecutive missed cycles — and sees every one of the 372 such members a cycle in advance. **The savings signal is unmeasured and the config says so**, because nothing in the data marks the day somebody decided to stop saving. |
| T-062 | done | 2026-09-06 | Early-warning and survival models. Four LightGBM models, one per horizon, on a 40,540-row member-month panel with a time-based split; isotonic calibration and prediction intervals on a block the models never saw; Cox proportional hazards for time to first late and time to cure; SHAP drivers; a card; and `POST /lmi/score` serving all of it. 22 new tests plus `make train-lmi`. **Acceptance at 30 days: AUC 0.9598 against a floor of 0.75, interval coverage 90.0% against 88%, calibration slope 1.118 against a band of 0.9-1.1.** The slope misses by 0.018, and it is not a split chosen badly: across reasonable train/calibration splits it moves between 0.81 and 1.28, because the population's event rate rises across the panel. The card says so and monitoring must recalibrate on a rolling window. **The headline AUC overstates what the model adds, and the card says that too.** A fifth of the book is already late, and one feature alone reaches 0.9380 on the same hold-out. Early warning means seeing the members who look fine today, so the number reported beside it is discrimination among members who are not currently late: **0.8096 at 30 days**, on 9,323 members with a 2.97% event rate. **Split conformal was built, measured and thrown away.** It is what the spec names and it holds its guarantee, but the quantity it covers is a 0-or-1 outcome: covering a coin flip 90% of the time takes an interval about 0.9 wide, and the first implementation reported 91% coverage while serving "0.32, somewhere between 0.00 and 1.00". What is served instead is a binomial interval on the rate among members scored alike, and it needed a second correction: sampling error alone covered 4.5% of a later block, because the same band means a different rate in March and in June. Widened by the drift measured between calibration months, it covers 90.0% at 30 days, falling to 56.5% at 90 as drift outgrows three months of calibration. **A defect that had been live since P3.** `.dockerignore` excludes `ml/*/artifacts`, so no service could see any trained model: the risk service had been answering `available: false, no trained artifacts` while a trained scorecard sat on disk beside it. Artifacts are now mounted read-only into every service. **And one the scoring endpoint exposed:** the materialiser and the trainer each had their own idea of a member's feature vector, and the materialiser computed 23 of the 27 the model expects; the four it missed were being filled with zeros at serving time without anybody being told. Both now call one function. A member genuinely missing a feature, such as one with no savings at all, has the gap named rather than filled. **A score whose point estimate falls outside its own band's measured interval carries a calibration warning** rather than one being quietly moved to fit the other. **Survival:** time to first late reaches concordance 0.9122 with readable hazard ratios (a missed salary deduction multiplies the hazard by 1.35, a rising savings balance divides it); time to cure reaches 0.6675, where a missed deduction cuts the cure hazard to 0.78. Cox was chosen over anything that fits better because a hazard ratio is a sentence an officer can argue with. |
| T-063 | done | 2026-09-06 | Member state machine, hysteresis, corroboration and alert hygiene. Five states with the documented transitions, corroboration that separates what confirms a drift from what explains it away, and an alert layer that deduplicates on the signal set, ranks by expected value, caps per officer and closes on recovery. 46 new tests plus `scripts/state_machine_eval.py` (`make state-eval`), which walks all 4,840 members month by month. **Measured on the whole book: no escalation during either outage window (0 of 1,939), the shortest gap between two state changes on any member is 28 days against a 14-day floor, and 70% of drifting members had something said about them before their first late payment, median lead 53 days.** **Two rungs of the ladder were unreachable as specified, and tracing three members is what showed it.** (a) docs/07 §4.5 raises WATCH on a robust z above two for two consecutive due events. On this population payment timing is whole days and the scale floor is two of them, so a member must drift four days beyond their own habit before one reading counts, and by then they are usually late. With the threshold alone, escalations on drifting members arrived a median of 40 days *after* their first late payment. The accumulating detector, measured in T-061 to see drift 92 days early, now raises WATCH as well; the threshold rule is kept as a second way in. (b) ELEVATED was gated on PELT confirming the change-point. PELT confirms a level shift and cannot confirm a trend, so the gate made escalation depend on the shape of a member's series rather than on whether they were deteriorating: every drift member traced had unconfirmed alarms, so they went WATCH straight to CRITICAL on a payment already two months late. The gate's intent is kept, which is that one weak signal must not escalate anybody, by requiring two independent reasons from PELT agreement, the model's p30, and a second family. After the change, 310 of 507 drifting members walk the ladder through ELEVATED. **Three properties are held by construction, and each has a test that would notice if they were not.** Hysteresis: a member alternating either side of a threshold every week changes state at most once a fortnight, and CRITICAL is exempt because somebody 45 days late does not wait for permission. Contradiction beats escalation: an outage or an arrangement de-escalates before any escalation is considered, so the same late payments cannot carry a member upward. But both are reported: a member with a real deterioration *and* an outage in the same month is a case an officer has to see, not one the platform resolves for them. **A capped alert is deferred, never dropped**, and a member with no probability yet ranks last rather than vanishing. |
| T-064 | done | 2026-09-06 | Longitudinal Council and the early-warning workflow. The four longitudinal agents had prompts and tool grants from T-043 and none of the tools existed; `ai/tools/longitudinal.py` adds the seven they need, and all four now resolve every grant. `EarlyWarningCase` runs on Temporal: read where the state machine put the member, convene the Council, wait for a person, watch for a week, continue as new. 15 new tests. **Everything about this workflow follows from one fact: nobody asked the platform to look at this member.** The recommendation vocabulary is INTERVENE, MONITOR or DE_ESCALATE, because an early-warning case cannot approve or decline anything: the member has a facility already and is not asking for another. An L3 action is dropped by the synthesizer for this case type and dropped again by the workflow, because a workflow that trusted the record would execute whatever reached it, and a platform that can restructure a facility on the strength of a drift it noticed is acting against somebody who never asked it to look. **Even an L1 action waits for a person here**, though a permissive dial would let it execute: the first contact with somebody who has not been told they are being watched is a person's to make. And a member who recovers closes the case on a signal rather than at the next review, because they should not receive an outreach the platform queued a week ago. **The nightly pass ties detection, state and alerts together in one place** rather than three services calling each other, so all three see the same evidence: a state change justified by a change-point the alert does not mention is a case an officer cannot follow. Measured: 4,210 members evaluated in 12.3 seconds, 1,364 transitions on the cold start and none on a second pass over the same day. **A defect the queue showed:** the "why now" line always described a payment-timing drift, so a CRITICAL raised on a payment two months late opened with "days to pay moved from 17 to 13". It now opens with what actually raised the alert and states the drift after, because an officer who spots one line that reads as an error stops trusting the rest. |
| T-065 | done | 2026-09-06 | notification-service and the Collections Workbench. Templated messages in the member's language, the reminder cadence from policy, channel stubs, an outreach draft that only a person can send, outcome logging, and a `/collections` queue with a member drawer. 26 service tests, 4 browser tests, and `scripts/outreach_drill.py` (`make outreach-drill`), which passes: five reminders scheduled around a due date, a rebuild that produces five rather than ten, all five called off when the money arrives, a draft that is absent from the member's inbox until it is approved and present once it is, and an outcome recorded. **Templates rather than generated text, and not for cost.** A message to a member about money they owe is a thing the cooperative said. It has to be the same thing every time, reviewable before it is sent, and in a language somebody signed off; a model writing each one produces prose nobody approved. `GET /templates` lists them all so a compliance reviewer can read them before any member does. A message an officer edited is recorded as edited, because it is no longer the template that was reviewed. **Four rules the tests hold.** A message with an unrendered variable is refused rather than sent, because a member reading "your instalment of {amount}" has been shown a broken system by the organisation asking them for money. A reminder whose day has passed is skipped rather than sent late. A rebuilt schedule lands on the same rows, because two identical messages a day apart is how a member learns to ignore all of them. And the remaining reminders are cancelled when the money arrives, not deleted: a member asking why they stopped hearing from the cooperative deserves an answer, and a member who paid on the due date and gets an overdue notice next morning has been told the platform is not paying attention. **The language fallback is loud.** A member whose language has no translation still gets the message, in English, with `language_fallback` set and the language actually used stored on the row. A silent fallback is how somebody receives English for a year while their record says otherwise. **The workbench says what it cannot do.** Every member on that queue has applied for nothing, so the screen states that nothing on it changes their facility, and a browser test asserts the sentence is there. |
| T-070 | todo | | |
| T-071 | todo | | |
| T-072 | todo | | |
| T-073 | todo | | |
| T-080 | todo | | |
| T-081 | todo | | |
| T-082 | todo | | |
| T-083 | todo | | |
| T-084 | todo | | |
| T-085 | todo | | |

## Phase verification

### P0 — verified 2026-09-05

`scripts/verify_phase.sh P0` — 13 pass, 0 fail.

| Step | Result |
|---|---|
| uv sync | PASS |
| make lint | PASS |
| make typecheck | PASS |
| make test (285 tests) | PASS |
| make env | PASS |
| stack healthy (28 containers) | PASS |
| postgres reachable | PASS |
| pgvector installed | PASS |
| MinIO console | PASS |
| Temporal UI | PASS |
| Grafana | PASS |
| otel collector | PASS |
| all 17 services /health through the gateway | PASS |

Against `docs/14` P0 criteria: `make up` healthy, codegen round-trips,
outbox/auth/hash tests green, core stub migrated and serving. The smoke
population lands in T-020 (P2), which is where the synthetic generator arrives.

### P1 — verified 2026-09-05

`scripts/verify_phase.sh P1` — 13 pass, 0 fail.

| Step | Result |
|---|---|
| make lint | PASS |
| make typecheck | PASS |
| policy packs (32 tests) | PASS |
| rule language (41 tests) | PASS |
| gates + affordability (77 tests) | PASS |
| synthesizer + dial (78 tests) | PASS |
| sandbox replay (17 tests) | PASS |
| ledger + tokens (50 tests) | PASS |
| snapshot freeze (24 tests) | PASS |
| underwriting workflow (12 tests) | PASS |
| migrations applied (18 services) | PASS |
| ledger append-only trigger present | PASS |
| snapshot immutability trigger present | PASS |

Against `docs/14` P1 criteria: packs validated, evaluate/synthesize/route
table-tested, ledger append-only and verified, submit produces a snapshot and
a blocked application ends with a ledger DecisionRecord routed per policy.

Whole suite at the end of P1: **629 tests**, all passing.

### P2 — verified 2026-09-05

`scripts/verify_phase.sh P2` — 12 pass, 0 fail.

| Step | Result |
|---|---|
| make lint | PASS |
| make typecheck | PASS |
| synthetic tests (55) | PASS |
| population sanity ranges (6 of 6) | PASS |
| extraction accuracy (classification 100%, critical fields 97.3%) | PASS |
| anomaly detection (97.4% found, 1.12% false positives) | PASS |
| document service (79 tests) | PASS |
| member intelligence (40 tests) | PASS |
| core stub (21 tests) | PASS |
| migrations applied (18 services) | PASS |
| timeline imported (453,195 events) | PASS |
| population loaded (5,000 members) | PASS |

Against `docs/14` P2 criteria: population and documents generated inside the
sanity ranges, document accuracy above the 95% critical-field bar, every
injected anomaly class detected with only two individual misses, and the
timeline imported.

Whole suite at the end of P2: **819 tests**, all passing.

**Hazard found and fixed during verification:** the core-stub test fixtures
truncate the whole `core` schema, so running the suite wiped the seeded demo
population. Those tests now run against a separate `cio_test` database, which
`docker/initdb` creates.

### P3 (verified 2026-09-06, `scripts/verify_phase.sh P3`)

| step | result |
|---|---|
| make lint | PASS |
| make typecheck | PASS |
| ml + Decision Factor tests (91) | PASS |
| feature service (36 tests) | PASS |
| risk service (36 tests) | PASS |
| fraud service (93 tests) | PASS |
| governance service (30 tests) | PASS |
| credit-risk revalidated from its artifacts | PASS |
| fraud false findings (2.51% against a 3% ceiling) | PASS |
| migrations applied (18 services) | PASS |
| feature registry published (23 features) | PASS |
| models trained, cards written | PASS |
| guarantor ring planted | PASS |

Against `docs/14` P3 criteria: features reproducible and purpose-scoped, models
trained with cards and a rollback path, risk and fraud serving inside their
budgets, and explanations that cite only what the run produced.

**Two acceptance numbers are not met, both measured rather than estimated, both
recorded in the model card and pinned by tests so they stay visible:** the
credit-risk champion reaches a hold-out AUC of 0.7190 against a floor of 0.72,
and its calibration slope is 0.763 against a band of 0.9-1.1. The challenger
clears the AUC floor at 0.7594. The cause is a property of the population, not
of the model: defaults in the hold-out period are mostly members who looked
ordinary at origination and drifted afterwards, and a model handed the
generating archetype outright reaches only 0.803 on the same rows. See T-031
for the four measurements behind that conclusion.

**Hazards found and fixed during P3:** an empty weight-of-evidence bin carried
the weight of a bin made entirely of defaults, so the first unseen employer
sector would have scored as the riskiest case ever seen; additive smoothing on
unequal bins inverted the weight order and silently defeated the monotone
constraint on two characteristics; a first-time borrower scored a perfect
CONDUCT because every count reads zero for someone who has never borrowed; a
low-confidence figure made a forensics check fall silent rather than ask for a
clearer copy; and comparing a `timestamptz` against a bare date made the
velocity window's inclusiveness depend on the database session's timezone.

Whole suite at the end of P3: **1,124 tests**, all passing.

### P4 (verified 2026-09-06, `scripts/verify_phase.sh P4`)

13 pass, 0 fail.

| Step | Result |
|---|---|
| make lint | PASS |
| make typecheck | PASS |
| llm gateway service | PASS |
| agent runtime service | PASS |
| committee service | PASS |
| decision service | PASS |
| agents and RAG | PASS |
| workflow tests | PASS |
| stack healthy | PASS |
| every service serving its routes | PASS |
| golden cases seeded through the real path | PASS |
| workbench typechecks | PASS |
| workbench Playwright smoke | PASS |

Against `docs/14` P4 criteria: the gateway works on a local provider, the six
agents produce valid opinions on the golden set, Tier 1 runs end to end, and
the Officer Workbench renders S1.

**The gateway is verified on `ai-local` only.** The `ai-remote` profile is
built and its provider adapters are tested against a fake, but no hosted
provider key exists in this build, so the hosted half of the P4 criterion is
untested rather than passing. It is the one P4 line not met.

**Council quality on the real model, measured over the five golden
scenarios:** 22 of 25 agent invocations returned a valid AgentOpinion. The
three that did not were reported DEGRADED rather than guessed: two lost the
gateway mid-round and one cited an evidence id no tool in that run had
produced, which the output screen refused. That refusal is the guardrail
working, not a failure of it.

**A new check joins the suite.** `scripts/check_routes.sh` compares the routes
each service declares in its source against the routes it actually serves. It
exists because five services ran for eleven hours reporting healthy while
serving nothing: the scaffold registers `/health` before the routers are
included, so an import error inside a router leaves a container that answers
its health check and nothing else. The health probe cannot catch that by
construction, and now something does.

**A published contract stopped meaning what it said, and was fixed rather than
excused.** `GET /decision-records/{id}` merged the ledger's own columns into
the record body, so the response looked like a DecisionRecord and failed
DecisionRecord validation; the service's own test stripped a field before
validating, which hid it. The record is now returned under `record`, with
`case_id` and `superseded_by` beside it, and nothing is stripped before the
contract check.

Whole suite at the end of P4: **1,371 Python tests** and **4 Playwright
tests**, all passing.

### P5 (verified 2026-09-06, `scripts/verify_phase.sh P5`)

18 pass, 0 fail.

| Step | Result |
|---|---|
| make lint | PASS |
| make typecheck | PASS |
| policy service | PASS |
| committee service | PASS |
| decision service | PASS |
| execution service | PASS |
| audit service | PASS |
| governance service | PASS |
| agent runtime | PASS |
| workflow tests | PASS |
| stack healthy | PASS |
| every service serving its routes | PASS |
| golden cases seeded | PASS |
| S7 autonomy drill | PASS |
| execution drill | PASS |
| override drill | PASS |
| tamper drill | PASS |
| workbench Playwright smoke (10 tests) | PASS |

Against `docs/14` P5 criteria: execution with tokens, the ledger viewer
reconstructs, override analytics, and S7 end to end.

**Four drills join the suite, and every one of them found a defect in itself
before it found anything else.** The tamper drill wrote the value already in
the row, so it reported a clean chain as proof of nothing. The execution drill
reused its action id, so a second run took the replay path and the checks after
it passed without running; it now mints fresh ids. The autonomy drill routed a
case autonomously and stopped there, which proves a number changed rather than
that a facility exists, so it now carries the decision through to the core. The
override drill picked the first case in the queue, which needed a senior, so
the step about acting within authority failed for the reason the next step is
about. A drill that has never failed is a drill nobody has checked.

**S2, S3, S4 and S5 are seeded and decided through the real deterministic path
on every verification run.** Tier 2 repair and revise is exercised by 21 tests
against a scripted Council rather than by a live model round, because a live
round costs minutes per case and is not reproducible; the live Council was
measured in P4 and stands.

**One P5 criterion is not met as written.** docs/09 §3.4 says an action beyond
a role's authority is hidden. It is shown disabled with the reason instead, and
the open actions stay enabled, because an officer who cannot see that approval
exists cannot tell whether to escalate the case or leave it. The API refuses it
either way, and the browser test asserts both halves.

Whole suite at the end of P5: **1,466 Python tests** and **10 Playwright
tests**, all passing.

### P6 (verified 2026-09-06, `scripts/verify_phase.sh P6`)

15 pass, 0 fail.

| Step | Result |
|---|---|
| make lint | PASS |
| make typecheck | PASS |
| longitudinal maths | PASS |
| lmi service | PASS |
| notification service | PASS |
| policy service | PASS |
| workflow tests | PASS |
| stack healthy | PASS |
| every service serving its routes | PASS |
| features materialised | PASS |
| S8 drift seen early | PASS |
| S9 outage suppressed | PASS |
| change-point measured | PASS |
| outreach drill | PASS |
| workbench Playwright smoke (14 tests) | PASS |

Against `docs/14` P6 criteria: S8 and S9 pass, the nightly run is inside its
budget, the state machine holds its properties, and the collections workbench
and notifications work.

**The measurements, all on the whole generated population rather than a
fixture.** Features for 5,000 members materialise in 2.9 seconds against a
ten-minute budget. Change-point detection sees 83.1% of drifting members before
their first late payment with a median lead of 92 days, on the half of the
population its parameters were not chosen on, and produces 0.21% false alarms
per steady member-year against a 1.5% ceiling. The state machine, walked month
by month over 4,840 members, escalates nobody during either outage window,
never changes a member's state twice inside 28 days, and says something about
70% of drifting members before their first late payment. The early-warning
model reaches AUC 0.9598 at 30 days, and 0.8096 among members who are not
already late, which is the number that means early warning.

**Six defects were found by measuring rather than by testing, and each one had
been passing its tests.** A baseline computed over a member's whole history is
lookahead, and on a drifting member it is lookahead in the worst direction. A
detector whose scale is one day is measuring rounding. "Late" meaning any
positive day count makes early warning impossible in a book where half the
members pay a day or two on. Two rungs of the state machine were specified in a
way this population cannot reach. Split conformal covers a coin flip and needs
an interval nearly a point wide to do it. And no service could see any trained
model, because `.dockerignore` excluded the artifacts.

**One acceptance number is not met.** The early-warning calibration slope at 30
days is 1.118 against a band of 0.9-1.1, and it moves between 0.81 and 1.28
across reasonable train/calibration splits because the population's event rate
rises across the panel. It is in the model card, and monitoring must
recalibrate on a rolling window rather than trust this one.

Whole suite at the end of P6: **1,687 Python tests** and **14 Playwright
tests**, all passing.

### P7 (verified 2026-09-06, `scripts/verify_phase.sh P7`)

14 pass, 0 fail.

| Step | Result |
|---|---|
| make lint | PASS |
| make typecheck | PASS |
| guardrails | PASS |
| policy service | PASS |
| agent runtime | PASS |
| governance service | PASS |
| llm gateway | PASS |
| stack healthy | PASS |
| every service serving its routes | PASS |
| officer copilot (25 questions) | PASS |
| S10 member assistant | PASS |
| cockpit and metrics | PASS |
| S6 sandbox and adopt | PASS |
| workbench Playwright smoke (29 tests) | PASS |

Against `docs/14` P7 criteria: S6 and S10 pass, the copilots are grounded, and
the cockpit and sandbox screens work.

**The measurements.** The officer copilot grounds 25 of 25 answers, refuses all
five questions it must refuse, and answers 15 of the 20 an officer actually
asks; the five it declines are declined honestly rather than guessed at. The
member assistant passes all ten checks of the S10 transcript. The cockpit
passes 14 of 14, including a key-by-key comparison of every tile against a
fresh read of the metrics endpoint. The S6 sandbox flow passes 13 of 13, moving
S1's weighted score by +0.4 when 0.10 of weight moves from CONDUCT to
COMMITMENT.

**Four defects were found by measuring rather than by testing.**

The officer copilot ran on the `fast` route, whose ceiling is 400 output
tokens. A `copilot_answer/1.0` with its citations does not fit under that, so
guided decoding cut the JSON off mid-object and every caller reported "the
answer was not JSON". Four generations on `case_S1CLEAN`, every one truncated,
and nothing in the stack said why. A test now asserts that no bundle asks for
more output than its route allows.

Asked for a next payment when every instalment was paid, the member assistant
built a date out of each account's day-of-month and told a member a payment
date no schedule row supports. Asked for a balance with only a principal and an
instalment on hand, it computed a settlement figure the core does not hold; the
output screen caught that one. Both tools now say in words what the record does
and does not hold, because a gap in a tool result is an invitation.

Asked why approvals had fallen when every decision on file was from one month,
the manager copilot reported a fall from 0.5 to 0.25, where 0.25 was the
autonomous share, and then explained it. Both numbers were in the context so
the numeric screen passed: the invention was the relationship, not the figures.
The flow metrics now carry a monthly series and the screen rejects a claimed
movement when the shortest series in the run has fewer than two points.

Nothing in the live path recorded a replayable case, so the sandbox could only
replay what a fixture had seeded and no case the platform had actually decided
was ever replayable. Freezing the gate inputs at evaluation and the outcome at
synthesis fixed it; `/policy/evaluate` is no longer a pure function, which is
the cost.

**One deviation from the specification.** `docs/06 §2.3` puts the officer
copilot and the member assistant on the `fast` route. Both run on `agent`
instead, for the ceiling reason above; `fast` has no other user and its 400
tokens are deliberate for narration. The manager copilot is on `reasoning` as
specified. Recorded here rather than silently, because a route is a cost and a
latency decision as well as a capability one.

**The demo now ships two policy versions.** `PF-STD/2026.09.2` was adopted
through the sandbox during T-073 and the five golden cases are decided under
it, so it stays: a version somebody decided under must still read back exactly
as it did. Versions written by drill runs, which nothing had decided under,
were removed afterwards.

**Running `make sandbox-drill` adds two policy versions every time.** That is
adoption working, not a leak: a version cannot be written over and a drill that
deleted what it adopted would teach the wrong lesson about what adopting a
policy means. The drill prints what it wrote, and `make reset` (T-082) is where
the demo state is restored. It also re-seeds the golden cases before replaying,
because a candidate compared against a baseline recorded under a different pack
can move nothing and report truthfully that nothing moved.

The policy service now runs as the host user. As root it left pack directories
on the host that nobody could delete, which turns a sandbox experiment into
litter needing docker to clear.

**The officer copilot's quality number fell when the context was bounded, and
that is the finding.** An earlier run of the twenty golden questions answered
15 of 20. After `case.get` was capped, the same run answered 5. The case
reconstruction had grown to 31,000 tokens as the demo case was re-seeded, past
the model's context window, and the answers it had been giving came from
superseded decision records carried in that timeline. Seven of the twenty
questions ask for a confidence, a disagreement or an agent's opinion, and
`seed_demo_case` skips the committee, so the honest answer to those is "not
recorded". The question set now says which questions a seeded case can answer
and the copilot gets all seven of the others right by declining them.
Confabulation with a better score is still confabulation.

Whole suite at the end of P7: **1,824 Python tests** and **29 Playwright
tests**, all passing.

## Blocked
(none)

## ADRs written
(none)
