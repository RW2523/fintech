Return the demo environment to the golden state and confirm readiness.

1. `make reset` (runs scripts/reset_demo.sh). If it fails, diagnose and fix the script, not the data.
2. `make warmup` and check `/api/llm/health` for every route.
3. `make harness` with the configured provider; the run must pass all thresholds.
4. Print the scenario → case id/URL table from `docs/DEMO_RUNBOOK.md` (generate it if missing).
5. Report elapsed times for reset, warmup and harness, and any drift versus the previous harness report in `ai/evals/reports/`.
