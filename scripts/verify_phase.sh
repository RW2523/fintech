#!/usr/bin/env bash
# Run a phase's acceptance suite (docs/14_DEFINITION_OF_DONE.md).
# Usage: scripts/verify_phase.sh P0
set -uo pipefail

PHASE="${1:?usage: verify_phase.sh P0}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PASS=0; FAIL=0
RESULTS=()

step () { # label | command
  local label="$1"; shift
  local start end dur out rc
  start=$(date +%s)
  out=$("$@" 2>&1); rc=$?
  end=$(date +%s); dur=$((end - start))
  if [ $rc -eq 0 ]; then
    RESULTS+=("PASS|${label}|${dur}s"); PASS=$((PASS+1))
  else
    RESULTS+=("FAIL|${label}|${dur}s"); FAIL=$((FAIL+1))
    echo "--- ${label} failed ---" >&2
    echo "$out" | tail -30 >&2
  fi
}

case "$PHASE" in
  P0)
    step "uv sync"            uv sync
    step "make lint"          make lint
    step "make typecheck"     make typecheck
    step "make test"          make test
    step "make env"           make env
    step "stack healthy"      scripts/wait_healthy.sh 60
    step "postgres reachable" bash -c 'docker compose --env-file docker/.env -f docker/compose.yaml exec -T postgres psql -U "${POSTGRES_USER:-cio}" -d "${POSTGRES_DB:-cio}" -c "select 1" >/dev/null'
    step "pgvector installed" bash -c 'docker compose --env-file docker/.env -f docker/compose.yaml exec -T postgres psql -U "${POSTGRES_USER:-cio}" -d "${POSTGRES_DB:-cio}" -tAc "select extname from pg_extension where extname='"'"'vector'"'"'" | grep -q vector'
    step "minio console"      bash -c 'source docker/.env; curl -fsS "http://localhost:${MINIO_CONSOLE_PORT:-9001}" >/dev/null'
    step "temporal ui"        bash -c 'source docker/.env; curl -fsS "http://localhost:${TEMPORAL_UI_PORT:-8233}" >/dev/null'
    step "grafana"            bash -c 'source docker/.env; curl -fsS "http://localhost:${GRAFANA_PORT:-3000}/api/health" >/dev/null'
    step "otel collector"     bash -c 'source docker/.env; curl -fsS "http://localhost:${OTELCOL_HEALTH_PORT:-13133}/" >/dev/null'
    step "every service /health" scripts/check_services.sh
    ;;
  P1)
    # docs/14 P1: packs validated; evaluate/synthesize/route table tests; ledger
    # append-only and verified; submit -> snapshot -> workflow -> policy stop.
    step "make lint"            make lint
    step "make typecheck"       make typecheck
    step "policy packs"         bash -c 'cd services/policy && uv run pytest -c "$OLDPWD/pyproject.toml" --rootdir=. -o testpaths=tests tests/test_packs.py -q'
    step "rule language"        bash -c 'cd services/policy && uv run pytest -c "$OLDPWD/pyproject.toml" --rootdir=. -o testpaths=tests tests/test_expr.py -q'
    step "gates + affordability" bash -c 'cd services/policy && uv run pytest -c "$OLDPWD/pyproject.toml" --rootdir=. -o testpaths=tests tests/test_evaluate.py tests/test_affordability.py -q'
    step "synthesizer + dial"   bash -c 'cd services/policy && uv run pytest -c "$OLDPWD/pyproject.toml" --rootdir=. -o testpaths=tests tests/test_synthesize.py -q'
    step "sandbox replay"       bash -c 'cd services/policy && uv run pytest -c "$OLDPWD/pyproject.toml" --rootdir=. -o testpaths=tests tests/test_sandbox.py -q'
    step "ledger + tokens"      bash -c 'cd services/decision && uv run pytest -c "$OLDPWD/pyproject.toml" --rootdir=. -o testpaths=tests tests -q'
    step "snapshot freeze"      bash -c 'cd services/application && uv run pytest -c "$OLDPWD/pyproject.toml" --rootdir=. -o testpaths=tests tests -q'
    step "underwriting workflow" uv run pytest workflows -q
    step "migrations applied"   make migrate
    step "ledger append-only"   bash -c '\
      docker compose --env-file docker/.env -f docker/compose.yaml exec -T postgres \
        psql -U "${POSTGRES_USER:-cio}" -d "${POSTGRES_DB:-cio}" -tAc \
        "select count(*) from pg_trigger where tgname = '"'"'ledger_no_update'"'"'" | grep -q 1'
    step "snapshots immutable"  bash -c '\
      docker compose --env-file docker/.env -f docker/compose.yaml exec -T postgres \
        psql -U "${POSTGRES_USER:-cio}" -d "${POSTGRES_DB:-cio}" -tAc \
        "select count(*) from pg_trigger where tgname = '"'"'snapshot_immutable'"'"'" | grep -q 1'
    ;;
  P2|P3|P4|P5|P6|P7|P8)
    echo "  phase ${PHASE} verification not implemented yet" >&2
    exit 1
    ;;
  *)
    echo "  unknown phase: ${PHASE}" >&2; exit 2 ;;
esac

printf '\n  %-30s %-6s %s\n' "STEP" "RESULT" "DURATION"
printf '  %-30s %-6s %s\n' "------------------------------" "------" "--------"
for r in "${RESULTS[@]}"; do
  IFS='|' read -r s l d <<< "$r"
  [ "$s" = "PASS" ] && c=$'\033[32m' || c=$'\033[31m'
  printf '  %-30s %b%-6s\033[0m %s\n' "$l" "$c" "$s" "$d"
done
printf '\n  %s: %d pass · %d fail\n\n' "$PHASE" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
