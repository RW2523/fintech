"""Who may sign in, and how their password is checked (docs/13 §1).

The demo signs in with a role picker: press "Officer" and you are one. That is
right for a machine on a bench and wrong for anything reachable from outside
it, because `/api/auth/dev-token` will mint a `head_of_credit` token for
whoever asks.

This is the smallest thing that is honestly better: a file of accounts with
Argon2id password hashes, read at startup. Keycloak is a pilot concern
(CLAUDE.md §4) and a database table for eight demo accounts would be a schema
to migrate for no gain, so the store is a file the operator owns and git never
sees.

What it deliberately does not do: password reset, lockout policy, sessions,
MFA. Those belong with the identity provider a pilot will bring, and pretending
otherwise here would build something nobody should carry into production.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from cio_common.auth import ROLES

__all__ = ["Account", "UserStoreError", "hash_password", "load_accounts", "verify"]

#: Where the accounts live. Git-ignored, mounted into the gateway.
DEFAULT_STORE = "docker/users.yaml"

_hasher = PasswordHasher()


class UserStoreError(RuntimeError):
    """The store is missing or unusable. Never a reason to let somebody in."""


@dataclass(frozen=True, slots=True)
class Account:
    """One person who may sign in."""

    email: str
    role: str
    password_hash: str
    name: str = ""
    branch: str | None = None
    member_id: str | None = None

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise UserStoreError(f"{self.email}: unknown role {self.role!r}")
        if self.role == "member" and not self.member_id:
            # A member account with no member id would sign in and see nothing,
            # because every member-facing tool reads the member from the token.
            raise UserStoreError(f"{self.email}: a member account needs a member_id")


def hash_password(password: str) -> str:
    """An Argon2id hash of one password.

    Used by `scripts/add_user.py`. The plaintext never leaves the process that
    called this and is never written anywhere.
    """
    if len(password) < 12:
        # Not a policy, a floor. A demo account with a four-character password
        # on a public URL is the whole hardening exercise undone.
        raise UserStoreError("a password must be at least 12 characters")
    return str(_hasher.hash(password))


def verify(account: Account, password: str) -> bool:
    """Whether this password belongs to this account.

    Returns False rather than raising on a mismatch: a caller must not be able
    to tell a bad password from a malformed hash by the shape of the failure.
    """
    try:
        return bool(_hasher.verify(account.password_hash, password))
    except (VerifyMismatchError, VerificationError):
        return False
    except Exception:
        return False


def _account_from(entry: dict[str, Any]) -> Account:
    missing = [key for key in ("email", "role", "password_hash") if not entry.get(key)]
    if missing:
        raise UserStoreError(f"an account is missing {', '.join(missing)}")
    return Account(
        email=str(entry["email"]).strip().lower(),
        role=str(entry["role"]),
        password_hash=str(entry["password_hash"]),
        name=str(entry.get("name") or ""),
        branch=entry.get("branch"),
        member_id=entry.get("member_id"),
    )


@lru_cache(maxsize=1)
def load_accounts(path: str | None = None) -> dict[str, Account]:
    """Every account, keyed by lowercased email.

    Cached: the store is read at startup and does not change under a running
    gateway. Adding an account is `scripts/add_user.py` followed by a restart,
    which is a deliberate friction on a file that decides who gets in.
    """
    # One name for this, matching the `user_store` setting. It had two, and a
    # gateway configured with the other one reported "sign-in is unavailable"
    # for every account in a store it was looking straight past.
    location = Path(path or os.environ.get("USER_STORE") or DEFAULT_STORE)
    if not location.is_file():
        raise UserStoreError(
            f"no user store at {location}. Run scripts/add_user.py to create one, or set CIO_USER_STORE"
        )

    body = yaml.safe_load(location.read_text()) or {}
    entries = body.get("accounts") if isinstance(body, dict) else body
    if not entries:
        raise UserStoreError(f"{location} has no accounts in it")

    accounts: dict[str, Account] = {}
    for entry in entries:
        account = _account_from(entry)
        if account.email in accounts:
            raise UserStoreError(f"{account.email} appears twice")
        accounts[account.email] = account
    return accounts
