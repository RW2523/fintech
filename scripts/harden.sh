#!/usr/bin/env bash
# Put the platform into the state a reachable deployment needs (docs/adr/0001).
#
#     scripts/harden.sh          # check, and say what is missing
#     scripts/harden.sh --apply  # set what can be set
#
# Four things have to be true before this is reachable from outside the machine
# it runs on:
#
#   1. AUTH_MODE=password, so `/api/auth/dev-token` refuses. That endpoint
#      mints a head_of_credit token for whoever asks, and the token it hands
#      out can pull the kill switch and approve a financing.
#   2. Real signing secrets. The example env ships placeholders that are in git
#      and in every copy of this repository.
#   3. At least one account in docker/users.yaml.
#   4. The stack restarted, so every service reads the new values.
#
# The check runs by default and changes nothing. `--apply` sets the first, and
# tells you about the rest, because generating secrets and creating accounts
# are both things somebody should do deliberately.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-docker/.env}"
STORE="${USER_STORE:-docker/users.yaml}"
APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --help|-h) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "  unknown option: $arg" >&2; exit 2 ;;
  esac
done

[ -f "$ENV_FILE" ] || { echo "  no $ENV_FILE. Run 'make env' first." >&2; exit 1; }

READY=1
say() { printf '  %-6s %s\n' "$1" "$2"; }
bad() { READY=0; say "NO" "$2"; }

printf '\n  Checking whether this is safe to expose\n\n'

# 1. sign-in mode
mode="$(grep -E '^AUTH_MODE=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
if [ "$mode" = "password" ]; then
  say "yes" "AUTH_MODE=password, so dev tokens are refused"
elif [ "$APPLY" -eq 1 ]; then
  if grep -qE '^AUTH_MODE=' "$ENV_FILE"; then
    tmp="$(mktemp)"; chmod 600 "$tmp"
    while IFS= read -r line; do
      case "$line" in AUTH_MODE=*) printf 'AUTH_MODE=password\n' ;; *) printf '%s\n' "$line" ;; esac
    done < "$ENV_FILE" > "$tmp"
    mv "$tmp" "$ENV_FILE"
  else
    printf '\n# Reachable from outside this machine: an account is required and\n# /api/auth/dev-token refuses (docs/adr/0001).\nAUTH_MODE=password\n' >> "$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"
  say "set" "AUTH_MODE=password"
else
  bad "" "AUTH_MODE is '${mode:-unset}'. The role picker and /api/auth/dev-token are open: run with --apply"
fi

# 2. secrets
placeholder=0
for key in JWT_SECRET TOKEN_SECRET INTERNAL_KEY; do
  value="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
  case "$value" in
    *change-me*|*dev-only-insecure*|"") placeholder=1 ;;
  esac
  [ "${#value}" -ge 32 ] || placeholder=1
done
if [ "$placeholder" -eq 0 ]; then
  say "yes" "the signing secrets are real"
else
  bad "" "the signing secrets are placeholders: run scripts/generate_secrets.sh"
fi

# 3. accounts
if [ -s "$STORE" ] && grep -q 'password_hash' "$STORE" 2>/dev/null; then
  count="$(grep -c 'password_hash' "$STORE" || echo 0)"
  say "yes" "$count account(s) in $STORE"
else
  bad "" "no accounts in $STORE: run scripts/add_user.py <email> <role>"
fi

# 4. what the browser will reach
if grep -qE '^BIND_ADDR=127\.0\.0\.1' "$ENV_FILE"; then
  say "note" "BIND_ADDR is loopback: a tunnel on this machine reaches it, nothing on the LAN does"
fi

printf '\n'
if [ "$READY" -eq 1 ]; then
  cat <<'READY'
  Ready. Restart so every service reads it:

    make down && make up && make up-ai-local

  Then point a tunnel at the web container, not the gateway: it serves the app
  and proxies /api, so one hostname covers both.

    cloudflared tunnel --url http://localhost:8080

READY
else
  printf '  Not ready. Fix the lines marked NO above.\n\n'
  exit 1
fi
