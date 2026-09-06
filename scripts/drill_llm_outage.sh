#!/usr/bin/env bash
# Take the model away and check the platform still decides (docs/11 §3, docs/13 §7).
#
#     scripts/drill_llm_outage.sh
#
# The claim this platform makes is that a model never computes a number and
# never makes a decision. The way to test a claim like that is to remove the
# model and see whether anything stops.
#
# Stops the model gateway, submits a case, and asserts three things: the
# deterministic path still produces a decision, the case routes to a person
# rather than being decided alone, and the narrative says it is degraded rather
# than being quietly absent. Then it puts the gateway back.
#
# The gateway is stopped rather than vLLM, deliberately: stopping vLLM means
# waiting minutes for a checkpoint to load again, and what is being tested is
# how the platform behaves when the model cannot be reached, which is the same
# either way.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GATEWAY="${GATEWAY_URL:-http://localhost:8000}"
COMPOSE=(docker compose --env-file docker/.env -f docker/compose.yaml)

PASS=0
FAIL=0
line() {
  if [ "$1" = "ok" ]; then
    PASS=$((PASS + 1)); printf '  \033[32mok  \033[0m %-46s %s\n' "$2" "${3:-}"
  else
    FAIL=$((FAIL + 1)); printf '  \033[31mMISS\033[0m %-46s %s\n' "$2" "${3:-}"
  fi
}

restore() {
  printf '\n  putting the model gateway back\n'
  "${COMPOSE[@]}" --profile core --profile services --profile observability \
    up -d llm_gateway >/dev/null 2>&1 || true
  # Wait for it, so the next thing anybody runs is not the first to discover
  # that it has not finished starting.
  # The container's own health endpoint, not one behind the gateway proxy: the
  # proxy needs a token and a 403 is not "not started yet".
  for _ in $(seq 1 30); do
    if [ "$("${COMPOSE[@]}" ps --format '{{.Status}}' llm_gateway 2>/dev/null | grep -c healthy)" -ge 1 ]; then
      printf '  the model gateway is back\n\n'; return 0
    fi
    sleep 2
  done
  printf '  \033[31mthe model gateway did not come back; run: make up\033[0m\n\n'
}
trap restore EXIT

TOKEN="$(scripts/dev_token.sh system)"

printf '\n  stopping the model gateway\n'
"${COMPOSE[@]}" stop llm_gateway >/dev/null 2>&1

# The circuit breaker in front of the route opens after a few failures, and a
# run submitted while it is still closed waits for the timeout on every agent.
# One throwaway call opens it, so the drill measures the degraded path rather
# than the timeout path.
curl -s --max-time 30 -X POST "$GATEWAY/api/agent_runtime/copilot/ask" \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"case_id":"case_S1CLEAN","question":"warm the breaker"}' >/dev/null 2>&1 || true

printf '\n  submitting a case with no model behind it\n\n'

RESULT="$(
  uv run python - <<'PY'
import json, subprocess, sys, time, urllib.error, urllib.request
sys.path.insert(0, ".")
from scripts.seed_demo_case import case_inputs  # noqa: E402
from synthetic.golden import GOLDEN  # noqa: E402

BASE = "http://localhost:8000"
token = subprocess.check_output(["scripts/dev_token.sh", "system"], text=True).strip()
head = {"authorization": f"Bearer {token}", "content-type": "application/json"}


def post(path, body):
    request = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode(), headers=head)
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)


case = GOLDEN[0]
gates = post(
    "/api/policy/policy/evaluate",
    {
        "product_code": "PF-STD",
        "policy_version": "2026.09.1",
        "snapshot_id": case.snapshot["snapshot_id"],
        "inputs": case_inputs(case),
    },
)
# A tier nobody else uses, made unique per run. The committee is idempotent on
# snapshot and tier, so a second drill would join the first one's result and
# report on a run made under different conditions.
tier = f"OUTAGE_DRILL_{int(time.time())}"
run = post(
    "/api/committee/committee/runs",
    {"snapshot": case.snapshot, "tier": tier, "policy_result": gates},
)
print(json.dumps(run))
PY
)"

state=$(printf '%s' "$RESULT" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("state"))')
record=$(printf '%s' "$RESULT" | python3 -c 'import sys,json;print(json.dumps(json.load(sys.stdin).get("decision_record") or {}))')

recommendation=$(printf '%s' "$record" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("recommendation"))')
route=$(printf '%s' "$record" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("route"))')
# `status` is a sibling of the audiences, not nested inside each one:
# {member, officer, auditor, status}.
narrative_status=$(printf '%s' "$record" | python3 -c '
import sys, json
narrative = json.load(sys.stdin).get("narrative") or {}
print(narrative.get("status", "") if isinstance(narrative, dict) else "")')

[ "$state" = "DONE" ] && line ok "the run completed without a model" "$state" \
                       || line miss "the run completed without a model" "$state"

[ -n "$recommendation" ] && [ "$recommendation" != "None" ] \
  && line ok "the deterministic path still answered" "$recommendation" \
  || line miss "the deterministic path still answered" "$recommendation"

# And it must not have declined anybody. With no Council there is no factor
# score, and a case nobody could assess is not a case that failed.
[ "$recommendation" != "DECLINE" ] \
  && line ok "nobody was declined for want of a model" "$recommendation" \
  || line miss "nobody was declined for want of a model" "declined on no evidence"

case "$route" in
  AUTONOMOUS) line miss "the case went to a person" "routed $route with no model" ;;
  ""|None)    line miss "the case went to a person" "no route" ;;
  *)          line ok "the case went to a person" "$route" ;;
esac

[ "$narrative_status" = "DEGRADED" ] \
  && line ok "the narrative says it is degraded" "$narrative_status" \
  || line miss "the narrative says it is degraded" "${narrative_status:-absent}"

printf '\n  %s/%s checks pass\n' "$PASS" "$((PASS + FAIL))"
printf '\n  outage drill: %s\n' "$([ "$FAIL" -eq 0 ] && echo PASS || echo FAIL)"
[ "$FAIL" -eq 0 ]
