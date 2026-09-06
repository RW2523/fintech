#!/usr/bin/env bash
# Mint a demo token for a role. Used by the phase verification steps so a curl
# in a Makefile does not have to parse JSON inline.
set -euo pipefail
ROLE="${1:-system}"
GATEWAY="${GATEWAY_URL:-http://localhost:8000}"
curl -fsS -X POST "$GATEWAY/api/auth/dev-token" \
  -H 'content-type: application/json' -d "{\"role\":\"$ROLE\"}" \
| python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])'
