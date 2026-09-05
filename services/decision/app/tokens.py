"""Approval tokens: single-use, scoped, expiring, signed (docs/03 §8, docs/13 §3).

Only execution-service may write to the core, and only by presenting one of
these. A token is bound to one action, one case and a maximum amount, so a
token issued for a small approval cannot be replayed against a larger one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cio_common.errors import Conflict, TokenInvalid
from cio_common.hashing import hmac_sign, hmac_verify
from cio_common.ids import new_id

__all__ = ["MAX_TTL", "issue", "redeem", "validate"]

#: docs/03 §8 — a token may not outlive the day it was issued on.
MAX_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class IssuedToken:
    body: dict[str, Any]

    @property
    def token_id(self) -> str:
        return str(self.body["token_id"])


#: Fields the signature covers. `used_at` is deliberately excluded: it changes
#: when the token is consumed, and a signature that moved with it would be
#: worthless as a tamper check.
_SIGNED_FIELDS = (
    "token_id",
    "action_id",
    "decision_record_id",
    "human_decision_id",
    "issued_to",
    "scope",
    "idempotency_key",
    "issued_at",
    "expires_at",
)


def _signable(body: dict[str, Any]) -> dict[str, Any]:
    return {k: body.get(k) for k in _SIGNED_FIELDS}


def _sign(secret: str, body: dict[str, Any]) -> str:
    return hmac_sign(secret, _signable(body), field="signature")


def _signature_matches(secret: str, body: dict[str, Any]) -> bool:
    signed = {**_signable(body), "signature": body.get("signature")}
    return hmac_verify(secret, signed, field="signature")


async def issue(
    db: AsyncSession,
    *,
    secret: str,
    action_id: str,
    decision_record_id: str,
    issued_to: dict[str, Any],
    product_code: str,
    member_id: str,
    case_id: str,
    max_amount: Decimal,
    idempotency_key: str,
    human_decision_id: str | None = None,
    ttl: timedelta = MAX_TTL,
) -> dict[str, Any]:
    """Mint a token. Re-issuing with the same idempotency key returns the first."""
    if ttl > MAX_TTL:
        raise TokenInvalid(f"token lifetime may not exceed {MAX_TTL}")

    existing = (
        (
            await db.execute(
                text("SELECT * FROM ledger.token WHERE idempotency_key = :k"), {"k": idempotency_key}
            )
        )
        .mappings()
        .first()
    )
    if existing:
        return _to_contract(existing)

    now = datetime.now(UTC)
    body: dict[str, Any] = {
        "schema": "approval_token/1.0",
        "token_id": new_id("tok"),
        "action_id": action_id,
        "decision_record_id": decision_record_id,
        "human_decision_id": human_decision_id,
        "issued_to": issued_to,
        "scope": {
            "product_code": product_code,
            "member_id": member_id,
            "case_id": case_id,
            "max_amount": f"{max_amount:.2f}",
        },
        "idempotency_key": idempotency_key,
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + ttl).isoformat().replace("+00:00", "Z"),
        "used_at": None,
    }
    body["signature"] = _sign(secret, body)

    await db.execute(
        text("""
        INSERT INTO ledger.token
          (token_id, action_id, decision_record_id, human_decision_id, issued_to, scope,
           idempotency_key, issued_at, expires_at, signature)
        VALUES (:token_id, :action_id, :decision_record_id, :human_decision_id,
                CAST(:issued_to AS jsonb), CAST(:scope AS jsonb), :idempotency_key,
                :issued_at, :expires_at, :signature)
    """),
        {
            "token_id": body["token_id"],
            "action_id": action_id,
            "decision_record_id": decision_record_id,
            "human_decision_id": human_decision_id,
            "issued_to": json.dumps(issued_to),
            "scope": json.dumps(body["scope"]),
            "idempotency_key": idempotency_key,
            "issued_at": now,
            "expires_at": now + ttl,
            "signature": body["signature"],
        },
    )
    return body


def _to_contract(row: Any) -> dict[str, Any]:
    def _load(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    return {
        "schema": "approval_token/1.0",
        "token_id": row["token_id"],
        "action_id": row["action_id"],
        "decision_record_id": row["decision_record_id"],
        "human_decision_id": row["human_decision_id"],
        "issued_to": _load(row["issued_to"]),
        "scope": _load(row["scope"]),
        "idempotency_key": row["idempotency_key"],
        "issued_at": row["issued_at"].isoformat().replace("+00:00", "Z"),
        "expires_at": row["expires_at"].isoformat().replace("+00:00", "Z"),
        "used_at": row["used_at"].isoformat().replace("+00:00", "Z") if row["used_at"] else None,
        "signature": row["signature"],
    }


async def validate(
    db: AsyncSession,
    *,
    secret: str,
    token_id: str,
    case_id: str | None = None,
    amount: Decimal | None = None,
) -> dict[str, Any]:
    """Check signature, expiry, single use and scope. Does not consume it."""
    row = (
        (await db.execute(text("SELECT * FROM ledger.token WHERE token_id = :t"), {"t": token_id}))
        .mappings()
        .first()
    )
    if row is None:
        raise TokenInvalid(f"no such token {token_id!r}")

    body = _to_contract(row)
    if not _signature_matches(secret, body):
        raise TokenInvalid("token signature does not match its contents")
    if row["used_at"] is not None:
        raise TokenInvalid("token has already been used", token_id=token_id)
    if row["expires_at"] <= datetime.now(UTC):
        raise TokenInvalid("token has expired", expires_at=body["expires_at"])
    if case_id is not None and body["scope"]["case_id"] != case_id:
        raise TokenInvalid("token is scoped to a different case", scoped_to=body["scope"]["case_id"])
    if amount is not None and amount > Decimal(body["scope"]["max_amount"]):
        raise TokenInvalid("amount exceeds the token's scope", max_amount=body["scope"]["max_amount"])
    return body


async def redeem(
    db: AsyncSession,
    *,
    secret: str,
    token_id: str,
    case_id: str | None = None,
    amount: Decimal | None = None,
) -> dict[str, Any]:
    """Validate and consume. A second redemption is refused."""
    body = await validate(db, secret=secret, token_id=token_id, case_id=case_id, amount=amount)
    used = (
        await db.execute(
            text("""
        UPDATE ledger.token SET used_at = now()
        WHERE token_id = :t AND used_at IS NULL
        RETURNING token_id
    """),
            {"t": token_id},
        )
    ).first()
    if used is None:
        raise Conflict("token was consumed concurrently", token_id=token_id)
    return body
