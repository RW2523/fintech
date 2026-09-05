# 14 — Definition of done

## Per task
- Acceptance command in `00_BUILD_PLAN.md` passes on the DGX Spark.
- Tests added or updated; `make lint typecheck test` green.
- No new dependency without the arm64 check (02 §6); no new LLM call outside the gateway.
- `docs/PROGRESS.md` updated; commit `T-xxx: …`.

## Per phase (`make verify PHASE=Px`)
P0: `make up` healthy; codegen round-trips; outbox/auth/hash tests; core stub seeded with a smoke population (100 members).
P1: policy packs validated; `policy.evaluate/synthesize/route` table tests; ledger append-only and verified; submit → snapshot → workflow → policy stop recorded.
P2: population and documents generated with sanity ranges; document accuracy ≥ 95 % critical fields; all injected anomalies detected; timeline imported.
P3: risk AUC ≥ 0.72 / calibration in range; fraud ring and duplicates found; explain endpoint.
P4: gateway works on `ai-local` and `ai-remote`; six agents valid on golden; Tier 1 end to end; Officer Workbench renders S1.
P5: S2 (Tier 2), S3, S4, S5, S7 pass; execution with tokens; ledger viewer reconstructs; override analytics.
P6: S8, S9 pass; nightly LMI within time; state machine tests; collections workbench and notifications.
P7: S6, S10 pass; copilots grounded; cockpit and sandbox UI.
P8: harness thresholds met on fake and real providers; dashboards; `make demo` green twice; fail-safe and security suites green; run-book dry-run recorded.

## Whole build (demo-ready)
1. `make demo` green twice consecutively from `make reset` on the Spark, `ai-local` profile.
2. All ten scenarios match `11_DEMO_SCENARIOS.md` within tolerances; harness report attached to PROGRESS.md.
3. Every on-screen number has a `calc_id`/`model_run_id` tooltip that resolves.
4. Any decision reconstructs from the ledger in ≤ 2 minutes; `GET /ledger/verify` green; tamper test red.
5. Autonomy Dial change, kill switch, sampling and override exercised and present in ledger/audit.
6. LLM outage drill passes (`scripts/drill_llm_outage.sh`).
7. `ai-remote` profile passes warmup and the golden harness (quality comparison recorded).
8. Latency targets in `02 §7` met or the deviation documented with cause.
9. `docs/DEMO_RUNBOOK.md` and `docs/OPERATIONS.md` complete; a second engineer executed the run-book from the doc.
10. No secrets in the repo; `make security` green.
