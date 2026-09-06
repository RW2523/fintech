#!/usr/bin/env bash
# Export DATABASE_URL from docker/.env unless one is already set.
# Sourced by the scripts that run repository tooling against the compose stack.
if [ -z "${DATABASE_URL:-}" ]; then
  _env="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/docker/.env"
  if [ ! -f "$_env" ]; then
    echo "  docker/.env missing - run 'make env' first" >&2
    return 1 2>/dev/null || exit 1
  fi
  # shellcheck disable=SC1090
  set -a; . "$_env"; set +a
  export DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-localhost}:${POSTGRES_PORT:-5432}/${POSTGRES_DB}"
fi
