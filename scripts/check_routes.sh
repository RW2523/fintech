#!/usr/bin/env bash
# Every service that has routes in the source must be serving them.
#
# A service whose module fails to import still answers /health, because the
# health route is registered by the scaffold before the routers are included.
# The container then reports healthy while serving nothing, which is how five
# services ran for eleven hours with no routes at all (T-046). This compares
# what each service's source declares against what it actually serves.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GATEWAY="${GATEWAY_URL:-http://localhost:8000}"

TOKEN=$(curl -fsS -X POST "$GATEWAY/api/auth/dev-token" \
          -H 'content-type: application/json' -d '{"role":"system"}' \
        | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])') || {
  echo "  cannot reach the gateway at $GATEWAY" >&2; exit 1; }

FAIL=0
for dir in services/*/; do
  service=$(basename "$dir")
  # The gateway is the thing doing the asking; it has no /api/<name> of its own.
  [ "$service" = "gateway" ] && continue
  # Only services that declare routes are expected to serve any. A scaffold
  # waiting for its build task is not a failure.
  declared=$(grep -rhoE '^@router\.(get|post|put|patch|delete)' "$dir/app" 2>/dev/null | wc -l)
  [ "$declared" -eq 0 ] && { printf '  %-22s scaffold, no routes declared\n' "$service"; continue; }

  served=$(curl -fsS "$GATEWAY/api/$service/openapi.json" -H "authorization: Bearer $TOKEN" 2>/dev/null \
           | python3 -c 'import sys,json
try:
    paths = json.load(sys.stdin)["paths"]
except Exception:
    print(-1); raise SystemExit
# /health and /version come from the scaffold, not from the service.
print(len([p for p in paths if p not in ("/health", "/version")]))')

  if [ "${served:-0}" -le 0 ]; then
    printf '  %-22s declares %s routes and serves none — its module did not import\n' \
      "$service" "$declared" >&2
    FAIL=1
  else
    printf '  %-22s %s routes\n' "$service" "$served"
  fi
done

[ "$FAIL" -eq 0 ] || echo "  a service is healthy but serving nothing; check its logs" >&2
exit "$FAIL"
