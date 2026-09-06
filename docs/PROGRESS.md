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

### Pre-existing workload on this host (affects T-002/T-003)

An unrelated `echomind` stack is running in Docker and holds resources this build's
compose profile assumes are free:

| Conflict | Detail | Affected task |
|---|---|---|
| Port 3000 | `echomind-frontend` — spec assigns 3000 to Grafana | T-003 |
| Port 11434 | `echomind-ollama` — spec assigns 11434 to the ollama fallback | T-003 |
| GPU memory | ~33 GB held by echomind (`trtllm-serve` + python workers) | T-040 |
| Host memory | ~47 GB in use, leaving ~73 GB vs the ~104 GB budgeted in `docs/02 §4.3` | T-040 |

Resolution options (decide before T-003, record as an ADR if it changes the spec):
stop the echomind stack for demo runs; remap Grafana/ollama ports in `docker/.env`;
or reuse the running ollama (it already serves `bge-m3`, the spec's `embed` model)
instead of starting a second one.


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
| T-022 | todo | | |
| T-023 | todo | | |
| T-024 | todo | | |
| T-025 | todo | | |
| T-030 | todo | | |
| T-031 | todo | | |
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

## Blocked
(none)

## ADRs written
(none)
