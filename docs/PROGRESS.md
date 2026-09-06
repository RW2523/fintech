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
| T-020 | done | 2026-09-05 | Synthetic population: 120 employers, 5,000 members, 7,553 accounts, 86k schedule rows, 86k payments, 120k deductions, 120k savings points, plus shares, guarantors, bureau, outages and arrangements. 22 tests. Generates in 1.3s and loads into the core stub in 59s, both far inside the 5-minute budget; a re-run with the same seed is byte-identical. All six sanity ranges from docs/10 §4.6 hold on four different seeds. Notes: (a) the archetype miss rates are calibrated, not taken literally from §4, because §4's stated 20% CHRONIC miss rate cannot produce the >25% CHRONIC delinquency §4.6 requires; (b) `savings_paused_share` counts only members who stopped saving, not CHRONIC members who never saved; (c) the drift window metric is measured over drift members with 14 months of history, since an account opened in month 22 has no clean run to drift away from; (d) core-stub bulk insert now binds scalars as text and casts by the real column type, because asyncpg will not coerce an ISO date string. |
| T-021 | done | 2026-09-05 | Per account-month labels (late7/30/60/90, cure, restructure, charge-off) derived from the payment history alone, never from the archetype that produced it. 33 unit tests on hand-built sequences, pinning every threshold at its boundary and proving the ladder is monotonic. 86,482 account-months loaded into `core.outcome`: late7 11,629, late30 6,483, late60 1,523, late90 344, charge-off 333, cure 6,394, restructure 540. The late30 rate of 7.5% matches the portfolio sanity range independently measured in T-020. |
| T-022 | done | 2026-09-05 | Document corpus: 12 structurally distinct payslip layouts plus bank statement, identity card, employment letter and provident-fund statement, rendered through Chromium on aarch64 to PNG and PDF at 200 dpi. `--apps 600` produces 2,155 documents in 6.5 min, every one with ground truth and DOM-derived normalised bounding boxes, 10.3% clean digital, 61 injected anomalies across all seven kinds. 25 tests. **Two measured deviations, both recorded because they change what T-024 can do:** (a) `PAYSLIP_LATEST_3` is one document covering three periods rather than three documents, because policy names it as one required type and reconciliation takes the median net across the three; (b) the 64-bit page phash in docs/07 §1.4 cannot separate documents at all. Measured over this corpus, two identity cards for different members sat a median of 2 bits apart and 100% of cross-member pairs fell inside the threshold of 6. Widening to 256 bits and giving each card a per-member photograph brings that to a median of 24 and about 2% of pairs. The residual overlap is inherent, so T-024 must treat a hash match as a candidate to be confirmed against extracted content, not as a HIGH finding on its own. |
| T-023 | done | 2026-09-05 | document-service: ingest with MIME/size/page checks and presigned upload, classification, OCR extraction with bbox anchoring, EvidenceRefs, and human review that supersedes rather than overwrites. 40 tests. **Measured over 400 documents: classification 100% (target 98%), critical fields 96.7% (target 95%), bbox and confidence coverage 97.5%.** Every critical field individually: payslip period 100%, gross_salary 100%, employer_name 97.5%, net_salary 96.2%; identity dob 100%, id_number 92.8%, name 90.1%. Notes: (a) the extractor is pluggable, so the vision route in T-040 replaces the OCR backend behind the same interface; (b) the declared document type is a hint only, never the classification; (c) render DPI was raised to the documented 200, without which the identity card was too small for OCR to read at all; (d) `norm_value` is read through a driver-agnostic decoder because asyncpg already decodes jsonb. |
| T-024 | done | 2026-09-05 | Forensics (arithmetic, metadata, reused image, layout signature, copy-move, readability) and cross-source reconciliation (income across payslip/employer/bank, employer match, identity match, duplicate identity numbers). 39 tests. **Measured: 74 of 76 injected anomalies detected (97.4%), integrity false positives 1.12% against a 3% ceiling.** Five of seven anomaly kinds detected in full. The two misses are documents whose critical field OCR failed entirely; both raise DOC-04 and route to a human, so nothing passes silently. **Three deviations, each with evidence:** (a) docs/07 §1.4 assigns INT-02 HIGH on a page-hash match alone, which T-022 measured cannot separate documents; an unconfirmed match is recorded LOW as a lead and only a content-confirmed or byte-identical match is HIGH; (b) the arithmetic and income checks refuse to run on figures read below 0.75 confidence, because an unreadable document is a DOC-04 problem and not grounds to accuse anyone; (c) 33.7% of clean documents raise DOC-04 at the policy's 0.85 confidence floor, reported separately from integrity false positives since asking for more information is not an accusation. Note: entity resolution uses rapidfuzz only; the bge-m3 embedding half of docs/07 §1.5 needs the LLM gateway and arrives with T-041. |
| T-025 | done | 2026-09-05 | member-intelligence-service: monthly-partitioned `member_event` (61 partitions covering the seeded window plus headroom), core-record import, content-addressed profile projection, cursor-paginated timeline, and the derived views the tools read. 40 tests. **Import of the full population: 453,195 events across 5,000 members in 2m42s, of which 172,631 are payment events, matching the ~180k the acceptance names.** The remainder are the deduction, savings, share and arrangement sources docs/07 §4.1 also lists. Event ids are derived from the source record, so a replayed import updates rather than duplicates, which is what makes the change feed safe to replay. Paging is verified to lose and repeat nothing across the whole timeline, and the projection is checked against the core stub for 100 randomly sampled members. A missed deduction is inferred from an absence, so it carries `validation: WARN` rather than being presented as an observation. |
| T-030 | done | 2026-09-06 | feature-service: a 23-feature registry, reproducible snapshots and a permitted-use filter. 36 tests. Every feature declares its family, window, source, permitted uses, dtype, version and monotone direction, and the registry is published to `feature_def` so a model run can cite the definition it used. The registry covers all five Decision Factors: CAPACITY 4, CONDUCT 7, COMMITMENT 6, CONDITIONS 2, INTEGRITY 4. Purpose scoping narrows that to 23 for underwriting, 11 for collections and 5 for fraud, so a collections call cannot see the proposed debt-service ratio. Values are computed from the member timeline and the core record as of a stated moment, each with its own provenance row, and the set is hashed into an `inputs_digest`; recomputing the same member at the same moment reproduces the digest exactly, and a snapshot with an equivalent digest is reused rather than recomputed. A database trigger refuses UPDATE on `feature_value`, because a model run cites a snapshot and a snapshot that drifted would make the decision unreconstructable. A structural test refuses any feature whose name or description mentions a protected characteristic. Note: these tests read the seeded population, so they run against the demo database rather than `cio_test`, and each test deletes only the snapshots that appeared while it ran instead of truncating.
| T-031 | done | 2026-09-06 | Credit-risk training: weight-of-evidence scorecard champion, LightGBM challenger, Platt calibration, isolation-forest out-of-distribution score, SHAP-equivalent drivers mapped to approved reason codes, and a generated model card. 89 tests. The training frame is built by calling the same `compute_features` the risk service calls, as of the day before each facility opened, so there is no second implementation to drift and the new facility is not an input to its own decision. **Measured: champion hold-out AUC 0.7190 (95% CI 0.620-0.814), challenger 0.7594 (0.675-0.840), champion calibration slope 0.763.** The challenger meets the 0.72 target; the champion falls short by a thousandth and the calibration band of 0.9-1.1 is missed. Both shortfalls are recorded in the card and pinned by tests that fail if they are ever silently fixed. **Root cause, measured four ways:** the hold-out period's defaults are mostly members who looked ordinary at origination and drifted afterwards. SLOW_DRIFT accounts default at 0.66% in the training window and 14.29% in the hold-out, and a model handed the generating archetype outright reaches only 0.803 there against 0.902 in training. On calibration: 4,500 configurations never exceeded 0.775; correcting the level alone leaves the slope unchanged, so it is spread and not base rate; cross-fitted calibration over 48 events is worse than validation-only at 19; and the walk-forward decay estimate ranges from 0.218 to 1.264 across folds, so no honest allowance can be drawn from it. This is the case for the Longitudinal Member Intelligence engine rather than a defect in the scorecard. **Four deviations, each forced by measurement:** (a) accounts whose twelve-month performance window has not finished are excluded, 3,479 of 7,553, because counting them as good would teach the model that recent lending never defaults; the spec's month indices are therefore applied as proportions over the 13 labellable cohorts. (b) `doc_min_conf`, `contact_change_days`, `application_count_12m` and `findings_max_severity` are dropped from training: they are application-time inputs with no historical counterpart, 100% missing or single-valued, and they remain live inputs to the policy engine and the fraud service. (c) The information-value floor is 0.05, not the 0.02 first used, because a column of pure noise reaches 0.034 across five bins at this sample size. (d) `num_leaves` 15 from the spec is kept in the search but 7 wins, since sixty-odd defaults cannot fill fifteen leaves. **Three defects the tests found and fixed:** an empty bin was given the weight of a bin made entirely of defaults, so the first unseen employer sector would have scored as the riskiest case ever seen; additive smoothing on unequal bins inverted the weight order on two characteristics, defeating the monotone constraint, now replaced by size-aware shrinkage plus a guaranteed isotonic pass; and a first-time borrower scored a perfect 1.0 for conduct because every count read zero, which is an absence of evidence and not a clean record. Fixing the as-of date to the day before origination also removed a leak worth 0.13 of univariate AUC on `utilisation`.
| T-032 | todo | | |
| T-033 | todo | | |
| T-034 | todo | | |
| T-040 | todo | | |
| T-041 | todo | | |
| T-042 | todo | | |
| T-043 | todo | | |
| T-044 | todo | | |
| T-045 | todo | | |
| T-046 | todo | | |
| T-050 | todo | | |
| T-051 | todo | | |
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

## Blocked
(none)

## ADRs written
(none)
