"""A token for a role, however this deployment signs people in.

    from scripts.token import token_for
    token = await token_for(client, "system")

The bash equivalent is `scripts/dev_token.sh`, and both do the same thing: try
`/api/auth/dev-token`, and when it refuses — which is what AUTH_MODE=password
means — sign in with an account from `docker/test-accounts.json`.

Every drill in this directory used to mint its own dev token inline. The first
deployment that required a password broke all of them at once, and a drill that
cannot start is not a drill that passed. This is the one place that knows how.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__all__ = ["ACCOUNTS_FILE", "MissingAccountsError", "token_for", "token_for_sync"]

ROOT = Path(__file__).resolve().parents[1]

#: Passwords for the demo accounts. Git-ignored, written by
#: scripts/write_test_accounts.py, and only read when the gateway asks for one.
ACCOUNTS_FILE = Path(os.environ.get("CIO_TEST_ACCOUNTS") or ROOT / "docker" / "test-accounts.json")


class MissingAccountsError(RuntimeError):
    """The gateway wants a password and there is nothing here to sign in with."""

    def __init__(self, role: str) -> None:
        super().__init__(
            f"this deployment requires a password and there is no account for {role!r} "
            f"in {ACCOUNTS_FILE}.\n"
            "  Run: uv run python scripts/write_test_accounts.py"
        )


def _account(role: str) -> dict[str, str]:
    try:
        accounts = json.loads(ACCOUNTS_FILE.read_text())
    except OSError as exc:
        raise MissingAccountsError(role) from exc
    account = accounts.get(role)
    if not account:
        raise MissingAccountsError(role)
    return account


async def token_for(client: Any, role: str = "system", *, base: str = "") -> str:
    """A bearer token for `role`, using whichever way in this gateway offers."""
    minted = await client.post(f"{base}/api/auth/dev-token", json={"role": role})
    if minted.status_code == 200:
        return str(minted.json()["access_token"])

    account = _account(role)
    signed = await client.post(
        f"{base}/api/auth/login",
        json={"email": account["email"], "password": account["password"]},
    )
    if signed.status_code != 200:
        raise RuntimeError(f"{account['email']} could not sign in: {signed.status_code}")
    return str(signed.json()["access_token"])


def token_for_sync(client: Any, role: str = "system", *, base: str = "") -> str:
    """The same, for the drills that use a synchronous client."""
    minted = client.post(f"{base}/api/auth/dev-token", json={"role": role})
    if minted.status_code == 200:
        return str(minted.json()["access_token"])

    account = _account(role)
    signed = client.post(
        f"{base}/api/auth/login",
        json={"email": account["email"], "password": account["password"]},
    )
    if signed.status_code != 200:
        raise RuntimeError(f"{account['email']} could not sign in: {signed.status_code}")
    return str(signed.json()["access_token"])
