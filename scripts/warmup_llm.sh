#!/usr/bin/env bash
# Send one small call down every LLM route (docs/06 §6, docs/02 §4).
# A model that has never been asked anything takes far longer on its first
# call than on its second, so the demo asks first.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
[ -f docker/.env ] && { set -a; . docker/.env; set +a; }
GATEWAY="${GATEWAY_URL:-http://localhost:${GATEWAY_PORT:-8000}}"

TOKEN=$(curl -fsS -X POST "${GATEWAY}/api/auth/dev-token" \
          -H 'content-type: application/json' -d '{"role":"system"}' \
        | python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])') || {
  echo "  could not obtain a dev token from ${GATEWAY}" >&2; exit 1; }

echo "  warming every route through ${GATEWAY} ..."
BODY=$(curl -fsS -X POST "${GATEWAY}/api/llm_gateway/llm/warmup" \
         -H "authorization: Bearer ${TOKEN}" -H 'content-type: application/json' \
         -d '{}') || { echo "  the gateway did not answer" >&2; exit 1; }

python3 - "$BODY" <<'PY'
import json, sys
body = json.loads(sys.argv[1])
for route in body["routes"]:
    mark = "ok  " if route["ready"] else "DOWN"
    detail = "" if route["ready"] else f"  {route.get('detail', '')}"
    print(f"    {mark} {route['route']}{detail}")
print(f"\n  {body['ready']} of {body['total']} routes ready")
sys.exit(0 if body["ready"] == body["total"] else 1)
PY
