#!/usr/bin/env bash
# Run `alembic upgrade head` for every service that has migrations.
# Reads docker/.env so the host and the containers agree on the database.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
. scripts/database_url.sh

TARGET="${1:-}"
FAILED=()

for dir in services/*/; do
  service="$(basename "$dir")"
  [ -n "$TARGET" ] && [ "$service" != "$TARGET" ] && continue
  compgen -G "${dir}alembic/versions/*.py" > /dev/null || continue

  printf '  %-22s ' "$service"
  if out=$(cd "$dir" && uv run alembic upgrade head 2>&1); then
    revision=$(cd "$dir" && uv run alembic current 2>/dev/null | tail -1 | awk '{print $1}')
    echo "-> ${revision:-head}"
  else
    echo "FAILED"
    echo "$out" | tail -12 >&2
    FAILED+=("$service")
  fi
done

if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "  migrations failed: ${FAILED[*]}" >&2
  exit 1
fi
echo "  migrations up to date"
