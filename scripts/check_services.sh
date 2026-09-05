#!/usr/bin/env bash
# Every domain service answers /health inside the compose network.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose --env-file "$ROOT/docker/.env" -f "$ROOT/docker/compose.yaml")

declare -A PORTS=(
  [gateway]=8000 [application]=8001 [document]=8002 [member_intelligence]=8003
  [policy]=8004 [feature]=8005 [risk]=8006 [fraud]=8007 [lmi]=8008
  [committee]=8009 [core_stub]=8010 [agent_runtime]=8011 [decision]=8012
  [execution]=8013 [notification]=8014 [audit]=8015 [governance]=8016 [llm_gateway]=8020
)

BAD=()
for svc in "${!PORTS[@]}"; do
  if ! "${COMPOSE[@]}" exec -T "$svc" curl -fsS "http://localhost:${PORTS[$svc]}/health" >/dev/null 2>&1; then
    BAD+=("$svc")
  fi
done

if [ "${#BAD[@]}" -gt 0 ]; then
  printf '  services not answering /health: %s\n' "${BAD[*]}" >&2
  exit 1
fi
echo "  all ${#PORTS[@]} services answer /health"
