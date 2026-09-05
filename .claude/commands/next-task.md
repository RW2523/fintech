Pick up and complete the next task in the build plan.

1. Read `CLAUDE.md`, `docs/00_BUILD_PLAN.md` and `docs/PROGRESS.md`.
2. Select the first task whose status is `todo` and whose `deps` are all `done`. If a task is `in_progress`, resume it instead.
3. Mark it `in_progress` in `docs/PROGRESS.md` with today's date.
4. Read every document listed under the task's `spec` before writing code. Quote the acceptance test back to yourself.
5. Write or update tests first where the task has testable behaviour. Implement the task completely — no TODO stubs left behind unless the plan explicitly allows a stub (it says "stub" in the scope).
6. Run the task's acceptance command and `make lint typecheck test`. Fix until green.
7. Update `docs/PROGRESS.md`: status `done`, date, notes and any deviation from the spec (with reason). If blocked, set `blocked`, record the exact error, and continue with the next unblocked task.
8. Commit: `git add -A && git commit -m "T-xxx: <title>"`.
9. Report: task id, what was built, acceptance result, deviations, and the next task id.

Do not skip acceptance tests. Do not modify contracts or policy semantics without writing an ADR (`/adr`).
