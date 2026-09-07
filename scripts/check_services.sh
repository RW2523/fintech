#!/usr/bin/env bash
# Every domain service answers /health *through the gateway* (T-008 acceptance).
# The gateway is the only published surface, so this also proves auth and routing.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
set -a; . "$ROOT/docker/.env"; set +a
GATEWAY="http://localhost:${GATEWAY_PORT:-8000}"

SERVICES=(application document member_intelligence policy feature risk fraud lmi
          committee core_stub agent_runtime decision execution notification audit
          governance llm_gateway)

# However this deployment signs people in: dev_token.sh tries the dev endpoint
# and signs in with an account when that is refused (docs/adr/0001).
TOKEN=$(GATEWAY_URL="${GATEWAY}" "$(dirname "${BASH_SOURCE[0]}")/dev_token.sh" system) || {
  echo "  could not obtain a token from ${GATEWAY}" >&2; exit 1; }

# an unauthenticated call must be refused (docs/13 §1)
code=$(curl -s -o /dev/null -w '%{http_code}' "${GATEWAY}/api/policy/health")
if [ "$code" != "403" ]; then
  echo "  gateway allowed an unauthenticated call (HTTP ${code})" >&2
  exit 1
fi

BAD=()
for svc in "${SERVICES[@]}"; do
  body=$(curl -fsS -H "Authorization: Bearer ${TOKEN}" "${GATEWAY}/api/${svc}/health" 2>/dev/null) || {
    BAD+=("${svc}:unreachable"); continue; }
  echo "$body" | grep -q '"status":"ok"' || BAD+=("${svc}:${body}")
  echo "$body" | grep -q "\"service\":\"${svc}\"" || BAD+=("${svc}:wrong-service")
done

if [ "${#BAD[@]}" -gt 0 ]; then
  printf '  services failing through the gateway: %s\n' "${BAD[*]}" >&2
  exit 1
fi
echo "  all ${#SERVICES[@]} services answer /health through the gateway"
