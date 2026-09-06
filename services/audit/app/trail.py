"""Appending to and verifying the audit trail (docs/04 §6).

Deliberately the same construction as the Decision Ledger: one advisory lock so
the chain is built in a single order, and verification that recomputes every
link from the stored row rather than trusting the stored hash.

Kept as its own module rather than shared with the ledger because the two
chains must be independent. A single implementation would be a single place to
subvert both, and an auditor comparing them would be comparing one thing with
itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cio_common.hashing import GENESIS_HASH, chain_hash
from cio_common.ids import new_id

__all__ = ["AuditBreak", "Entry", "append", "entries_for", "head", "verify"]

#: Distinct from the ledger's, so an audit write never waits on a ledger write.
_LOCK_KEY = 4_812_338

#: The fields the hash covers. Listed rather than hashing the whole row so that
#: adding a column later cannot silently invalidate every earlier entry.
_SIGNED = (
    "entry_id",
    "actor",
    "action",
    "service",
    "case_id",
    "run_id",
    "object_ref",
    "before",
    "after",
    "policy_version",
    "model_versions",
    "trace_id",
)


@dataclass(frozen=True, slots=True)
class Entry:
    seq: int
    entry_id: str
    actor: dict[str, Any]
    action: str
    service: str
    case_id: str | None
    run_id: str | None
    object_ref: dict[str, Any]
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    policy_version: str | None
    model_versions: dict[str, Any]
    trace_id: str | None
    hash: str
    prev_hash: str
    created_at: Any

    def as_dict(self) -> dict[str, Any]:
        body = {
            "seq": self.seq,
            "entry_id": self.entry_id,
            "actor": self.actor,
            "action": self.action,
            "service": self.service,
            "case_id": self.case_id,
            "run_id": self.run_id,
            "object_ref": self.object_ref,
            "before": self.before,
            "after": self.after,
            "policy_version": self.policy_version,
            "model_versions": self.model_versions,
            "trace_id": self.trace_id,
            "hash": self.hash,
            "prev_hash": self.prev_hash,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        return body


@dataclass(frozen=True, slots=True)
class AuditBreak:
    seq: int
    entry_id: str
    reason: str
    expected: str
    found: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "entry_id": self.entry_id,
            "reason": self.reason,
            "expected": self.expected,
            "found": self.found,
        }


#: The jsonb columns. Only these are decoded: asyncpg returns jsonb as a
#: string on some paths and as a dict on others, while `action` and the rest
#: are plain text that json.loads would refuse.
_JSON_FIELDS = frozenset({"actor", "object_ref", "before", "after", "model_versions"})


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def signed_body(row: Any) -> dict[str, Any]:
    """Exactly what the hash covers, in a fixed order."""
    return {field: (_load(row[field]) if field in _JSON_FIELDS else row[field]) for field in _SIGNED}


def _row_to_entry(row: Any) -> Entry:
    return Entry(
        seq=row["seq"],
        entry_id=row["entry_id"],
        actor=_load(row["actor"]) or {},
        action=row["action"],
        service=row["service"],
        case_id=row["case_id"],
        run_id=row["run_id"],
        object_ref=_load(row["object_ref"]) or {},
        before=_load(row["before"]),
        after=_load(row["after"]),
        policy_version=row["policy_version"],
        model_versions=_load(row["model_versions"]) or {},
        trace_id=row["trace_id"],
        hash=row["hash"],
        prev_hash=row["prev_hash"],
        created_at=row["created_at"],
    )


async def head(db: AsyncSession) -> str:
    row = (
        await db.execute(text("SELECT hash FROM audit.entry ORDER BY seq DESC LIMIT 1"))
    ).scalar_one_or_none()
    return row or GENESIS_HASH


async def append(db: AsyncSession, body: dict[str, Any]) -> Entry:
    """Append one entry, linked to the current head."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})

    entry_id = new_id("aud")
    prev_hash = await head(db)
    signed = {
        "entry_id": entry_id,
        "actor": body.get("actor") or {},
        "action": body["action"],
        "service": body["service"],
        "case_id": body.get("case_id"),
        "run_id": body.get("run_id"),
        "object_ref": body.get("object_ref") or {},
        "before": body.get("before"),
        "after": body.get("after"),
        "policy_version": body.get("policy_version"),
        "model_versions": body.get("model_versions") or {},
        "trace_id": body.get("trace_id"),
    }
    entry_hash = chain_hash(prev_hash, signed)

    row = (
        (
            await db.execute(
                text("""
        INSERT INTO audit.entry
          (entry_id, actor, action, service, case_id, run_id, object_ref, before, after,
           policy_version, model_versions, trace_id, ip, hash, prev_hash)
        VALUES (:entry_id, CAST(:actor AS jsonb), :action, :service, :case_id, :run_id,
                CAST(:object_ref AS jsonb), CAST(:before AS jsonb), CAST(:after AS jsonb),
                :policy_version, CAST(:model_versions AS jsonb), :trace_id, :ip,
                :hash, :prev_hash)
        RETURNING *
    """),
                {
                    "entry_id": entry_id,
                    "actor": json.dumps(signed["actor"], default=str),
                    "action": signed["action"],
                    "service": signed["service"],
                    "case_id": signed["case_id"],
                    "run_id": signed["run_id"],
                    "object_ref": json.dumps(signed["object_ref"], default=str),
                    "before": json.dumps(signed["before"], default=str)
                    if signed["before"] is not None
                    else None,
                    "after": json.dumps(signed["after"], default=str)
                    if signed["after"] is not None
                    else None,
                    "policy_version": signed["policy_version"],
                    "model_versions": json.dumps(signed["model_versions"], default=str),
                    "trace_id": signed["trace_id"],
                    "ip": body.get("ip"),
                    "hash": entry_hash,
                    "prev_hash": prev_hash,
                },
            )
        )
        .mappings()
        .first()
    )
    assert row is not None
    return _row_to_entry(row)


async def entries_for(
    db: AsyncSession,
    *,
    case_id: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    limit: int = 500,
) -> list[Entry]:
    rows = (
        (
            await db.execute(
                text("""
        SELECT * FROM audit.entry
         WHERE (CAST(:case_id AS text) IS NULL OR case_id = CAST(:case_id AS text))
           AND (CAST(:actor_id AS text) IS NULL OR actor ->> 'id' = CAST(:actor_id AS text))
           AND (CAST(:action AS text) IS NULL OR action = CAST(:action AS text))
         ORDER BY seq ASC
         LIMIT :limit
    """),
                {"case_id": case_id, "actor_id": actor_id, "action": action, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [_row_to_entry(row) for row in rows]


async def verify(
    db: AsyncSession, *, start: int | None = None, end: int | None = None
) -> tuple[int, list[AuditBreak]]:
    """Recompute every link. Returns how many were checked and what broke.

    Recomputed from the stored fields rather than compared against the stored
    hash: an alteration that also updated the hash would pass the second check
    and fail this one, which is the check worth having.
    """
    rows = (
        (
            await db.execute(
                text("""
        SELECT * FROM audit.entry
         WHERE (CAST(:start AS bigint) IS NULL OR seq >= CAST(:start AS bigint))
           AND (CAST(:end AS bigint) IS NULL OR seq <= CAST(:end AS bigint))
         ORDER BY seq ASC
    """),
                {"start": start, "end": end},
            )
        )
        .mappings()
        .all()
    )

    breaks: list[AuditBreak] = []
    expected_prev = GENESIS_HASH if not rows or rows[0]["seq"] == 1 else rows[0]["prev_hash"]
    for row in rows:
        if row["prev_hash"] != expected_prev:
            breaks.append(
                AuditBreak(
                    seq=row["seq"],
                    entry_id=row["entry_id"],
                    reason="prev_hash does not match the entry before it",
                    expected=expected_prev,
                    found=row["prev_hash"],
                )
            )
        recomputed = chain_hash(row["prev_hash"], signed_body(row))
        if recomputed != row["hash"]:
            breaks.append(
                AuditBreak(
                    seq=row["seq"],
                    entry_id=row["entry_id"],
                    reason="the entry does not hash to its stored hash",
                    expected=recomputed,
                    found=row["hash"],
                )
            )
        expected_prev = row["hash"]

    return len(rows), breaks
