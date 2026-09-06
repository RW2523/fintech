#!/usr/bin/env bash
# Bring the demo back from nothing (docs/11 §3).
#
# Wipes the data volumes, migrates, loads the generated population, indexes the
# policy corpus, decides the golden cases, materialises the longitudinal
# features and warms the model routes. Then it prints the case ids and URLs the
# run-book needs.
#
# Measured at about four and a half minutes on the Spark, of which ninety
# seconds is building 453,000 timeline events from the core records and
# seventy is loading the population into it. docs/11 §3 asks for two, which
# this does not meet and is not close to meeting: the two slow steps are both
# doing real work over five thousand members and neither is waste.
#
# Generation is not part of that: `synthetic/out` is the generated population
# and it is reused if present, because regenerating five thousand members with
# their documents takes longer than the demo has and produces a population
# nobody has checked. Pass --regenerate to build it again.
#
# What this deletes: the `postgres_data`, `redis_data` and `minio_data`
# volumes. That is the whole point, and it is why the script says so and asks
# unless --yes is given. The audit archive lives in MinIO with object lock, so
# a reset does not remove what the lock protects; it removes the bucket the
# lock was on, which is a different thing and worth being deliberate about.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GATEWAY="${GATEWAY_URL:-http://localhost:8000}"
WEB="${WEB_URL:-http://localhost:8080}"
GRAFANA="${GRAFANA_URL:-http://localhost:3001}"
COMPOSE=(docker compose --env-file docker/.env -f docker/compose.yaml)
PROFILES=(--profile core --profile services --profile observability)

REGENERATE=0
ASSUME_YES=0
SKIP_WARMUP=0
for arg in "$@"; do
  case "$arg" in
    --regenerate) REGENERATE=1 ;;
    --yes|-y)     ASSUME_YES=1 ;;
    --no-warmup)  SKIP_WARMUP=1 ;;
    --help|-h)
      sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "  unknown option: $arg" >&2; exit 2 ;;
  esac
done

started=$(date +%s)
step() { printf '\n  \033[1m%s\033[0m\n' "$1"; }
took() { printf '    %ss\n' "$(( $(date +%s) - $1 ))"; }

if [ "$ASSUME_YES" -ne 1 ]; then
  cat <<WARN

  This deletes the demo's data volumes: every member, document, decision and
  ledger entry in this stack. It does not touch anything outside docker.

WARN
  read -r -p "  Type 'reset' to continue: " answer
  [ "$answer" = "reset" ] || { echo "  nothing done"; exit 1; }
fi

step "stopping the stack and deleting its data"
t=$(date +%s)
# The model containers are deliberately left alone. They hold no demo data,
# they take minutes to load a checkpoint, and stopping them here meant the
# corpus was indexed with no embedding route to reach: retrieval came back
# lexical-only and nothing said so, because this script had redirected the
# indexer's warning to /dev/null.
"${COMPOSE[@]}" "${PROFILES[@]}" --profile web down --remove-orphans -v >/dev/null 2>&1 || true
took "$t"

step "starting core and services"
t=$(date +%s)
"${COMPOSE[@]}" "${PROFILES[@]}" up -d --remove-orphans >/dev/null
scripts/wait_healthy.sh 300 >/dev/null
took "$t"

step "migrating every service"
t=$(date +%s)
scripts/migrate.sh >/dev/null
took "$t"

if [ "$REGENERATE" -eq 1 ] || [ ! -f synthetic/out/member.jsonl ]; then
  step "generating the population (this is the slow part)"
  t=$(date +%s)
  uv run python -m synthetic population >/dev/null
  uv run python -m synthetic rings >/dev/null
  uv run python -m synthetic documents >/dev/null
  took "$t"
fi

step "loading the population into the core"
t=$(date +%s)
uv run python -m synthetic load --token "$(scripts/dev_token.sh system)" >/dev/null
took "$t"

if [ "$SKIP_WARMUP" -eq 1 ]; then
  step "skipping model warm-up (--no-warmup)"
else
  step "warming the model routes"
  t=$(date +%s)
  # Before the corpus is indexed, not after: the index wants the embedding
  # route, and a cold route means an index with no vectors in it.
  scripts/warmup_llm.sh >/dev/null 2>&1 || echo "    no model reachable; the deterministic path still works"
  took "$t"
fi

step "building and indexing the policy corpus"
t=$(date +%s)
uv run python -m ai.rag build >/dev/null
# Not silenced. It says when it could not embed, and a demo running on
# lexical-only retrieval is worth knowing about before the demo.
CIO_TOKEN="$(scripts/dev_token.sh system)" uv run python -m ai.rag index | sed 's/^/  /'
took "$t"

step "deciding the golden cases"
t=$(date +%s)
uv run python scripts/seed_demo_case.py >/dev/null
took "$t"

step "building member timelines from the core records"
t=$(date +%s)
TOKEN="$(scripts/dev_token.sh system)"
# Without this the core has members and nothing has a history: the collections
# workbench is empty, the longitudinal features have nothing to compute from,
# and the feature service's own tests skip because no member has a timeline.
# It was missing from this script, and the reset above is what found it.
curl -fsS --max-time 900 -X POST "$GATEWAY/api/member_intelligence/members/import" \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"rebuild_profiles": true}' >/dev/null
took "$t"

step "materialising longitudinal features and evaluating the book"
t=$(date +%s)
TOKEN="$(scripts/dev_token.sh system)"
curl -fsS -X POST "$GATEWAY/api/lmi/lmi/materialise" \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{}' >/dev/null
curl -fsS -X POST "$GATEWAY/api/lmi/lmi/evaluate" \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{}' >/dev/null
took "$t"

elapsed=$(( $(date +%s) - started ))
printf '\n  \033[1mReady in %ss.\033[0m\n' "$elapsed"
cat <<SUMMARY

  Scenario cases
    S1  case_S1CLEAN     clean application, fast path
    S2  case_S2INCOME    income discrepancy
    S3  case_S3TAMPER    altered document and reused image
    S4  case_S4BREACH    policy breach on affordability
    S5  case_S5RING      guarantor ring

  Where to look
    Officer queue      $WEB/officer
    Collections        $WEB/collections
    Compliance         $WEB/compliance
    Ledger             $WEB/ledger
    Manager cockpit    $WEB/manager
    Policy sandbox     $WEB/sandbox
    Member assistant   $WEB/member
    Grafana            $GRAFANA
    API                $GATEWAY/docs

SUMMARY
printf ''
