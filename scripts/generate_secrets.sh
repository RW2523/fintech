#!/usr/bin/env bash
# Replace the placeholder secrets in docker/.env with real ones (docs/13 §1).
#
#     scripts/generate_secrets.sh
#
# The example env ships `change-me` and `dev-only-insecure-...` so a first run
# works on a bench without anybody choosing anything. Those values are in git,
# in the README, and in every copy of this repository: a platform reachable
# from outside itself and still signing tokens with one is a platform anybody
# can mint a head_of_credit token for.
#
# This rewrites JWT_SECRET, TOKEN_SECRET, INTERNAL_KEY, POSTGRES_PASSWORD and
# MINIO_ROOT_PASSWORD with 48 random bytes each, keeps a timestamped backup,
# and leaves everything else alone.
#
# Changing POSTGRES_PASSWORD does not change the password Postgres already has:
# that is set when its volume is initialised. Either run this before the first
# `make up`, or run `make reset` afterwards, and the script says which applies.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-docker/.env}"
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force|-f) FORCE=1 ;;
    --help|-h)  sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "  unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ ! -f "$ENV_FILE" ]; then
  echo "  no $ENV_FILE. Run 'make env' first." >&2
  exit 1
fi

secret() { openssl rand -base64 48 | tr -d '\n=+/' | cut -c1-48; }

already_real=1
while IFS= read -r key; do
  value="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2-)"
  case "$value" in
    *change-me*|*dev-only-insecure*|"") already_real=0 ;;
  esac
done <<< "JWT_SECRET
TOKEN_SECRET
INTERNAL_KEY"

if [ "$already_real" -eq 1 ] && [ "$FORCE" -eq 0 ]; then
  cat <<'DONE'

  The signing secrets in this .env are already real.

  Rotating them signs out everybody holding a token and invalidates every
  approval token that has not been redeemed, so it is not something to do by
  accident. Pass --force if you meant to.

DONE
  exit 0
fi

backup="${ENV_FILE}.$(date +%Y%m%dT%H%M%S).bak"
cp "$ENV_FILE" "$backup"
chmod 600 "$backup"

replace() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    # A temporary file rather than sed -i, so a secret never becomes an
    # argument that shows up in ps output.
    local tmp; tmp="$(mktemp)"; chmod 600 "$tmp"
    while IFS= read -r line; do
      case "$line" in
        "${key}="*) printf '%s=%s\n' "$key" "$value" ;;
        *) printf '%s\n' "$line" ;;
      esac
    done < "$ENV_FILE" > "$tmp"
    mv "$tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

postgres_changed=0
for key in JWT_SECRET TOKEN_SECRET INTERNAL_KEY MINIO_ROOT_PASSWORD; do
  replace "$key" "$(secret)"
done

current_pg="$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
case "$current_pg" in
  *change-me*|"") replace POSTGRES_PASSWORD "$(secret)"; postgres_changed=1 ;;
esac

chmod 600 "$ENV_FILE"

cat <<SUMMARY

  Rewrote the secrets in $ENV_FILE.
  The previous file is $backup — keep it until the stack is up, then delete it.

SUMMARY

if [ "$postgres_changed" -eq 1 ]; then
  cat <<'PGWARN'
  POSTGRES_PASSWORD changed. Postgres set its password when its volume was
  first created and does not read this again, so one of these applies:

    the stack has never run   nothing to do; 'make up' will use it
    the stack has run before  'make reset' rebuilds the volume with it

PGWARN
fi

cat <<'NEXT'
  Then restart so every service picks the new values up:

    make down && make up

NEXT
