Verify a whole phase of the build plan: $ARGUMENTS (e.g. `P3`).

1. Read `docs/00_BUILD_PLAN.md` for the phase and `docs/14_DEFINITION_OF_DONE.md` for its phase criteria.
2. Ensure the compose stack needed by the phase is up (`make up`, plus `make up-ai-local` from P4 on).
3. Run `make verify PHASE=$ARGUMENTS`; if that target does not yet cover every task's acceptance command, run each task's acceptance command individually.
4. Produce a table: task, acceptance command, result, duration. For any failure, diagnose root cause, fix if it is a defect in this phase's scope, and re-run. Do not paper over failures by weakening tests or guardrails.
5. Record the phase verification (date, results, measured performance numbers such as tokens/s, Tier 1 p95) in `docs/PROGRESS.md` under a "Phase verification" heading.
6. Commit as `verify: $ARGUMENTS`.
