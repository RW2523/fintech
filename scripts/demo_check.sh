#!/usr/bin/env bash
# Prove the demo works from nothing (docs/11 §3, T-082).
#
#     scripts/demo_check.sh
#
# Reset, then run every acceptance the platform has, then print the run-book
# table. Exits non-zero on the first failure, because a demo that half works is
# a demo nobody should walk into a boardroom with.
#
# This is the thing to run the evening before. It takes a while: the copilot
# evaluation alone asks a 7B model twenty-five questions on a shared GPU.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GATEWAY="${GATEWAY_URL:-http://localhost:8000}"
WEB="${WEB_URL:-http://localhost:8080}"
GRAFANA="${GRAFANA_URL:-http://localhost:3001}"

SKIP_RESET=0
QUICK=0
for arg in "$@"; do
  case "$arg" in
    --no-reset) SKIP_RESET=1 ;;
    --quick)    QUICK=1 ;;
    --help|-h)  sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "  unknown option: $arg" >&2; exit 2 ;;
  esac
done

RESULTS=()
FAILED=0

step() {
  local label="$1"; shift
  local t; t=$(date +%s)
  printf '\n  \033[1m%s\033[0m\n' "$label"
  if "$@" > "/tmp/demo_check_$$.log" 2>&1; then
    RESULTS+=("PASS|$label|$(( $(date +%s) - t ))s")
    printf '    \033[32mpass\033[0m  %ss\n' "$(( $(date +%s) - t ))"
  else
    RESULTS+=("FAIL|$label|$(( $(date +%s) - t ))s")
    FAILED=1
    printf '    \033[31mfail\033[0m  %ss\n' "$(( $(date +%s) - t ))"
    tail -25 "/tmp/demo_check_$$.log" | sed 's/^/      /'
  fi
  rm -f "/tmp/demo_check_$$.log"
}

if [ "$SKIP_RESET" -eq 1 ]; then
  printf '\n  starting from the stack as it stands (--no-reset)\n'
else
  step "reset the demo"            scripts/reset_demo.sh --yes
fi

step "every service serving its routes" scripts/check_routes.sh
step "the golden set decides correctly" uv run python -m ai.evals.harness --set golden --no-agents
step "the adversarial set holds"        uv run python -m ai.evals.harness --set adversarial
step "S6: sandbox, replay and adopt"    uv run python scripts/sandbox_drill.py
step "S7: autonomy and the kill switch" uv run python scripts/autonomy_drill.py
step "execution and replay"             uv run python scripts/execution_drill.py
step "override and the series"          uv run python scripts/override_drill.py
step "both chains go red when altered"  uv run python scripts/tamper_drill.py
step "S10: the member assistant"        uv run python scripts/member_assistant_eval.py
step "the cockpit and its metrics"      uv run python scripts/cockpit_eval.py

if [ "$QUICK" -eq 0 ]; then
  # The slow ones. Skipped by --quick, and named in the summary when they are,
  # because a check that did not run has not passed.
  step "the officer copilot"            uv run python scripts/copilot_eval.py
  step "the workbench"                  bash -c 'cd apps/web && npx playwright test'
else
  RESULTS+=("SKIP|the officer copilot|--quick")
  RESULTS+=("SKIP|the workbench|--quick")
fi

printf '\n\n  %-40s %-6s %s\n' "CHECK" "RESULT" "TOOK"
printf '  %-40s %-6s %s\n' "----------------------------------------" "------" "----"
for row in "${RESULTS[@]}"; do
  IFS='|' read -r verdict label took <<< "$row"
  case "$verdict" in
    PASS) colour=$'\033[32m' ;;
    FAIL) colour=$'\033[31m' ;;
    *)    colour=$'\033[33m' ;;
  esac
  printf '  %-40s %b%-6s\033[0m %s\n' "$label" "$colour" "$verdict" "$took"
done

if [ "$FAILED" -ne 0 ]; then
  printf '\n  \033[31mThe demo is not ready.\033[0m Fix the failures above and run this again.\n\n'
  exit 1
fi

cat <<TABLE

  The demo is ready.

  | Time  | Scenario | Where                              |
  |-------|----------|------------------------------------|
  | 00-05 | Opening  | $WEB/officer
  | 05-12 | S1 clean | $WEB/officer/cases/case_S1CLEAN
  | 12-18 | S2 income discrepancy | $WEB/officer/cases/case_S2INCOME
  | 18-20 | S3 altered document    | $WEB/officer/cases/case_S3TAMPER
  | 20-26 | S6 sandbox and adopt   | $WEB/sandbox
  | 26-30 | S4 policy breach       | $WEB/officer/cases/case_S4BREACH
  | 30-34 | S5 guarantor ring      | $WEB/officer/cases/case_S5RING
  | 34-39 | S8/S9 collections      | $WEB/collections
  | 39-43 | S10 member and audit   | $WEB/member and $WEB/ledger
  | 43-45 | Close: the cockpit     | $WEB/manager

  Grafana  $GRAFANA
  API      $GATEWAY/docs

TABLE
