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
| T-052 | todo | | |
| T-053 | todo | | |
| T-054 | todo | | |
| T-060 | todo | | |
| T-061 | todo | | |
| T-062 | todo | | |
| T-063 | todo | | |
| T-064 | todo | | |
| T-065 | todo | | |
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

## Blocked
(none)

## ADRs written
(none)
