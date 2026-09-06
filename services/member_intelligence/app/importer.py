"""Importing core records into the member event store (docs/08 §3).

The import reads straight from the core stub's own tables in the shared
database. Events carry deterministic ids derived from the source record, so a
re-import updates rather than duplicates, which is what makes the change feed
safe to replay.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.events import MemberEvent, derive_events

__all__ = ["ImportSummary", "import_members", "member_ids"]

_BATCH = 500


@dataclass
class ImportSummary:
    members: int = 0
    events: int = 0
    by_type: dict[str, int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "members": self.members,
            "events": self.events,
            "by_type": dict(sorted((self.by_type or {}).items())),
        }


async def member_ids(db: AsyncSession, *, limit: int | None = None) -> list[str]:
    query = "SELECT member_id FROM core.member ORDER BY member_id"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = await db.execute(text(query))
    return [row[0] for row in rows]


async def _fetch(db: AsyncSession, query: str, members: list[str]) -> list[dict[str, Any]]:
    rows = (await db.execute(text(query), {"members": members})).mappings().all()
    return [dict(r) for r in rows]


async def _write(db: AsyncSession, events: list[MemberEvent]) -> None:
    """Upsert on the event's natural key, so a replay is idempotent."""
    for event in events:
        row = event.as_row()
        await db.execute(
            text("""
            INSERT INTO app_member.member_event
              (event_id, member_id, account_id, occurred_at, event_type,
               source_system, source_record_id, payload, data_quality,
               permitted_uses, evidence_refs)
            VALUES (:event_id, :member_id, :account_id, :occurred_at, :event_type,
                    :source_system, :source_record_id, CAST(:payload AS jsonb),
                    CAST(:data_quality AS jsonb), CAST(:permitted_uses AS text[]),
                    CAST(:evidence_refs AS jsonb))
            ON CONFLICT (member_id, occurred_at, event_id) DO UPDATE SET
              payload = EXCLUDED.payload, data_quality = EXCLUDED.data_quality
        """),
            {
                **row,
                "payload": json.dumps(row["payload"]),
                "data_quality": json.dumps(row["data_quality"]),
                "evidence_refs": json.dumps(row["evidence_refs"]),
            },
        )


async def import_members(
    db: AsyncSession, members: list[str] | None = None, *, batch: int = _BATCH
) -> ImportSummary:
    """Build the timeline for these members, or for everyone."""
    targets = members if members is not None else await member_ids(db)
    summary = ImportSummary(by_type=defaultdict(int))

    for start in range(0, len(targets), batch):
        chunk = targets[start : start + batch]

        accounts = await _fetch(
            db,
            """
            SELECT account_id, member_id FROM core.account
            WHERE member_id = ANY(:members)
        """,
            chunk,
        )
        owner = {a["account_id"]: a["member_id"] for a in accounts}
        account_ids = list(owner)

        schedules = (
            (
                await db.execute(
                    text("""
            SELECT schedule_id, account_id, due_date, amount_due
            FROM core.schedule WHERE account_id = ANY(:accounts)
        """),
                    {"accounts": account_ids},
                )
            )
            .mappings()
            .all()
        )
        payments = (
            (
                await db.execute(
                    text("""
            SELECT p.payment_id, p.schedule_id, p.paid_at, p.amount_paid
            FROM core.payment p
            JOIN core.schedule s ON s.schedule_id = p.schedule_id
            WHERE s.account_id = ANY(:accounts)
        """),
                    {"accounts": account_ids},
                )
            )
            .mappings()
            .all()
        )
        arrangements = (
            (
                await db.execute(
                    text("""
            SELECT arrangement_id, account_id, type, from_date, to_date
            FROM core.arrangement WHERE account_id = ANY(:accounts)
        """),
                    {"accounts": account_ids},
                )
            )
            .mappings()
            .all()
        )

        deductions = await _fetch(
            db,
            """
            SELECT deduction_id, member_id, employer_id, cycle, expected_amount,
                   received_amount, received_at, net_salary
            FROM core.deduction WHERE member_id = ANY(:members)
        """,
            chunk,
        )
        savings = await _fetch(
            db,
            """
            SELECT member_id, as_of, balance FROM core.savings
            WHERE member_id = ANY(:members)
        """,
            chunk,
        )
        shares = await _fetch(
            db,
            """
            SELECT member_id, as_of, units, value FROM core.share_capital
            WHERE member_id = ANY(:members)
        """,
            chunk,
        )

        by_member: dict[str, dict[str, list[dict[str, Any]]]] = {
            member: {
                "schedules": [],
                "payments": [],
                "deductions": [],
                "savings": [],
                "shares": [],
                "arrangements": [],
            }
            for member in chunk
        }
        schedule_owner: dict[str, str] = {}

        for row in schedules:
            member = owner[row["account_id"]]
            schedule_owner[row["schedule_id"]] = row["account_id"]
            by_member[member]["schedules"].append(dict(row))
        for row in payments:
            account = schedule_owner.get(row["schedule_id"])
            if account:
                by_member[owner[account]]["payments"].append(dict(row))
        for row in arrangements:
            by_member[owner[row["account_id"]]]["arrangements"].append(dict(row))
        for name, source in (
            ("deductions", deductions),
            ("savings", savings),
            ("shares", shares),
        ):
            for record in source:
                by_member[record["member_id"]][name].append(record)

        events: list[MemberEvent] = []
        for member, sources in by_member.items():
            events.extend(
                derive_events(
                    member_id=member,
                    schedules=sources["schedules"],
                    payments=sources["payments"],
                    deductions=sources["deductions"],
                    savings=sources["savings"],
                    shares=sources["shares"],
                    arrangements=sources["arrangements"],
                    account_of_schedule=schedule_owner,
                )
            )

        await _write(db, events)
        await db.commit()

        summary.members += len(chunk)
        summary.events += len(events)
        for event in events:
            summary.by_type[event.event_type] += 1  # type: ignore[index]

    return summary
