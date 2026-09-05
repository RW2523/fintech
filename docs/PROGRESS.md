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
| T-008 | todo | | |
| T-010 | todo | | |
| T-011 | todo | | |
| T-012 | todo | | |
| T-013 | todo | | |
| T-014 | todo | | |
| T-015 | todo | | |
| T-016 | todo | | |
| T-020 | todo | | |
| T-021 | todo | | |
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
(none yet)

## Blocked
(none)

## ADRs written
(none)
