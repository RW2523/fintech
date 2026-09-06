"""Appending to and verifying the Decision Ledger (docs/04 §5, docs/14 §4).

Every append takes an advisory lock so the chain is built in a single order
under concurrency. Verification recomputes each link from the stored payload,
so a row that was altered out of band shows up as a break.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cio_common.hashing import GENESIS_HASH, chain_hash
from cio_common.ids import new_id

__all__ = ["ChainBreak", "LedgerEntry", "append", "chain_for", "head", "verify"]

#: Serialises appends so two writers cannot both extend the same head.
_LOCK_KEY = 4_812_337


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    seq: int
    entry_id: str
    kind: str
    case_id: str | None
    member_id: str | None
    payload: dict[str, Any]
    hash: str
    prev_hash: str
    created_at: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "entry_id": self.entry_id,
            "kind": self.kind,
            "case_id": self.case_id,
            "member_id": self.member_id,
            "payload": self.payload,
            "hash": self.hash,
            "prev_hash": self.prev_hash,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True, slots=True)
class ChainBreak:
    """Where verification stopped believing the chain."""

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


def _row_to_entry(row: Any) -> LedgerEntry:
    payload = row["payload"]
    return LedgerEntry(
        seq=row["seq"],
        entry_id=row["entry_id"],
        kind=row["kind"],
        case_id=row["case_id"],
        member_id=row["member_id"],
        payload=json.loads(payload) if isinstance(payload, str) else payload,
        hash=row["hash"],
        prev_hash=row["prev_hash"],
        created_at=row["created_at"],
    )


async def head(db: AsyncSession) -> str:
    """The hash of the last entry, or the genesis value for an empty ledger."""
    row = (
        await db.execute(text("SELECT hash FROM ledger.entry ORDER BY seq DESC LIMIT 1"))
    ).scalar_one_or_none()
    return row or GENESIS_HASH


async def append(
    db: AsyncSession,
    kind: str,
    payload: dict[str, Any],
    *,
    case_id: str | None = None,
    member_id: str | None = None,
) -> LedgerEntry:
    """Append one entry, linking it to the current head."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})

    prev_hash = await head(db)
    entry_hash = chain_hash(prev_hash, payload)
    entry_id = new_id("ent")

    row = (
        (
            await db.execute(
                text("""
        INSERT INTO ledger.entry
          (entry_id, kind, case_id, member_id, payload, hash, prev_hash)
        VALUES (:entry_id, :kind, :case_id, :member_id, CAST(:payload AS jsonb),
                :hash, :prev_hash)
        RETURNING seq, entry_id, kind, case_id, member_id, payload, hash, prev_hash, created_at
    """),
                {
                    "entry_id": entry_id,
                    "kind": kind,
                    "case_id": case_id,
                    "member_id": member_id,
                    "payload": json.dumps(payload, default=str, sort_keys=True),
                    "hash": entry_hash,
                    "prev_hash": prev_hash,
                },
            )
        )
        .mappings()
        .one()
    )

    return _row_to_entry(row)


async def chain_for(db: AsyncSession, *, case_id: str | None = None, limit: int = 1000) -> list[LedgerEntry]:
    """Every entry, or every entry for one case, in order."""
    if case_id:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT * FROM ledger.entry WHERE case_id = :case_id ORDER BY seq LIMIT :limit
        """),
                    {"case_id": case_id, "limit": limit},
                )
            )
            .mappings()
            .all()
        )
    else:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT * FROM ledger.entry ORDER BY seq LIMIT :limit
        """),
                    {"limit": limit},
                )
            )
            .mappings()
            .all()
        )
    return [_row_to_entry(r) for r in rows]


async def verify(
    db: AsyncSession, *, from_seq: int = 0, to_seq: int | None = None
) -> tuple[bool, list[ChainBreak], int]:
    """Recompute the chain. Returns (intact, breaks, entries checked).

    The whole chain is verified, not each case's slice: an entry's link depends
    on every entry before it, whatever case it belonged to.
    """
    rows = (
        (
            await db.execute(
                text("""
        SELECT * FROM ledger.entry
        WHERE seq > :from_seq AND (CAST(:to_seq AS bigint) IS NULL
                                   OR seq <= CAST(:to_seq AS bigint))
        ORDER BY seq
    """),
                {"from_seq": from_seq, "to_seq": to_seq},
            )
        )
        .mappings()
        .all()
    )

    if not rows:
        return True, [], 0

    breaks: list[ChainBreak] = []
    first = _row_to_entry(rows[0])
    expected_prev = GENESIS_HASH if first.seq == 1 else first.prev_hash

    for row in rows:
        entry = _row_to_entry(row)
        if entry.prev_hash != expected_prev:
            breaks.append(
                ChainBreak(
                    seq=entry.seq,
                    entry_id=entry.entry_id,
                    reason="BROKEN_LINK",
                    expected=expected_prev,
                    found=entry.prev_hash,
                )
            )
        recomputed = chain_hash(entry.prev_hash, entry.payload)
        if recomputed != entry.hash:
            breaks.append(
                ChainBreak(
                    seq=entry.seq,
                    entry_id=entry.entry_id,
                    reason="PAYLOAD_ALTERED",
                    expected=recomputed,
                    found=entry.hash,
                )
            )
        expected_prev = entry.hash

    return not breaks, breaks, len(rows)


async def decision_records(
    db: AsyncSession, *, limit: int = 200, decided_kinds: tuple[str, ...] = ("HUMAN_DECISION",)
) -> list[dict[str, Any]]:
    """The current decision on each case, newest first, and whether a person
    has answered it.

    Read from the ledger rather than from a work table beside it. A queue kept
    separately can disagree with what was actually decided, and the ledger is
    the record.

    One row per case, not one per entry. A case that is re-assessed appends a
    new record without removing the old one, and a queue that listed both would
    show the same case twice and invite an officer to act on a superseded
    recommendation. The whole chain is still readable through `/ledger`.
    """
    rows = (
        (
            await db.execute(
                text("""
        WITH latest AS (
          SELECT DISTINCT ON (COALESCE(case_id, entry_id))
                 seq, entry_id, case_id, member_id, payload, created_at
            FROM ledger.entry
           WHERE kind = 'DECISION_RECORD'
           ORDER BY COALESCE(case_id, entry_id), seq DESC
        ),
        records AS (
          SELECT * FROM latest ORDER BY seq DESC LIMIT :limit
        )
        SELECT r.*,
               EXISTS (
                 SELECT 1 FROM ledger.entry d
                  WHERE d.case_id = r.case_id
                    AND d.kind = ANY(:decided_kinds)
                    AND d.seq > r.seq
               ) AS decided
          FROM records r
         ORDER BY r.seq DESC
    """),
                {"limit": limit, "decided_kinds": list(decided_kinds)},
            )
        )
        .mappings()
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        body = dict(row)
        payload = body.get("payload")
        body["payload"] = json.loads(payload) if isinstance(payload, str) else payload
        out.append(body)
    return out
