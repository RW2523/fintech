#!/usr/bin/env bash
# A token for a role, however this deployment signs people in.
#
#     scripts/dev_token.sh officer
#
# On a bench that is `/api/auth/dev-token`: press a role and you are it. Once
# AUTH_MODE=password the endpoint refuses, and every drill, harness run and
# browser test that called this would have stopped working — so this signs in
# with an account instead.
#
# The accounts come from `docker/test-accounts.json`, which is git-ignored and
# written by `scripts/write_test_accounts.py`. It holds the passwords for the
# demo accounts on this machine, next to the store it was made from and at the
# same trust level. A deployment nobody drills against does not need it.
set -euo pipefail

ROLE="${1:-system}"
GATEWAY="${GATEWAY_URL:-http://localhost:8000}"
ACCOUNTS="${CIO_TEST_ACCOUNTS:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/docker/test-accounts.json}"

token="$(
  curl -fsS -X POST "$GATEWAY/api/auth/dev-token" \
    -H 'content-type: application/json' -d "{\"role\":\"$ROLE\"}" 2>/dev/null \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null || true
)"

if [ -n "$token" ]; then
  printf '%s\n' "$token"
  exit 0
fi

if [ ! -f "$ACCOUNTS" ]; then
  cat >&2 <<MISSING
  Dev tokens are disabled here and there is no account file to sign in with.

  Either run scripts/write_test_accounts.py to make one, or point
  CIO_TEST_ACCOUNTS at it. Without it the drills and the browser tests cannot
  sign in, and a check that cannot run has not passed.
MISSING
  exit 1
fi

python3 - "$ROLE" "$GATEWAY" "$ACCOUNTS" <<'PY'
import json, sys, urllib.error, urllib.request

role, gateway, path = sys.argv[1], sys.argv[2], sys.argv[3]
accounts = json.load(open(path))
account = accounts.get(role)
if account is None:
    print(f"  no account for role {role!r} in {path}", file=sys.stderr)
    raise SystemExit(1)

request = urllib.request.Request(
    f"{gateway}/api/auth/login",
    data=json.dumps({"email": account["email"], "password": account["password"]}).encode(),
    headers={"content-type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        print(json.load(response)["access_token"])
except urllib.error.HTTPError as exc:
    print(f"  {account['email']} could not sign in: {exc.code}", file=sys.stderr)
    raise SystemExit(1) from exc
PY
