#!/usr/bin/env bash
# Block until every compose container with a healthcheck is healthy.
# Usage: scripts/wait_healthy.sh [timeout_seconds]
set -uo pipefail

TIMEOUT="${1:-300}"
PROJECT="${COMPOSE_PROJECT_NAME:-cio}"
DEADLINE=$(( $(date +%s) + TIMEOUT ))

echo "  waiting up to ${TIMEOUT}s for containers to become healthy..."

while :; do
  mapfile -t IDS < <(docker ps -q --filter "label=com.docker.compose.project=${PROJECT}")
  if [ "${#IDS[@]}" -eq 0 ]; then
    echo "  no containers running for project '${PROJECT}'" >&2
    exit 1
  fi

  PENDING=(); FAILED=()
  for id in "${IDS[@]}"; do
    name=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$id")
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id")
    state=$(docker inspect -f '{{.State.Status}}' "$id")
    case "$health" in
      healthy|none) ;;
      starting)   PENDING+=("$name") ;;
      unhealthy)  FAILED+=("$name") ;;
    esac
    [ "$state" = "running" ] || { [ "$state" = "exited" ] && continue; PENDING+=("$name:$state"); }
  done

  if [ "${#FAILED[@]}" -gt 0 ]; then
    echo "  UNHEALTHY: ${FAILED[*]}" >&2
    for f in "${FAILED[@]}"; do
      echo "  --- last health probe for $f ---" >&2
      docker inspect -f '{{range .State.Health.Log}}{{.Output}}{{end}}' \
        "$(docker ps -q --filter "label=com.docker.compose.project=${PROJECT}" --filter "label=com.docker.compose.service=$f")" \
        2>/dev/null | tail -5 >&2
    done
    exit 1
  fi

  if [ "${#PENDING[@]}" -eq 0 ]; then
    echo "  all containers healthy"
    exit 0
  fi

  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "  timed out after ${TIMEOUT}s; still starting: ${PENDING[*]}" >&2
    exit 1
  fi
  sleep 5
done
