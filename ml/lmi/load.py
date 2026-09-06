"""Reading a member's history out of the core for training (docs/07 §4.4).

The service materialises features nightly; training needs the raw series over
two years for every member, which is a different read. One query per family
over the whole book rather than one per member: the same reduction, done once.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from ml.lmi.families import Deduction, SavingsPoint
from ml.lmi.temporal import DueEvent

__all__ = ["History", "database_url", "load_book"]

History = tuple[list[DueEvent], list[Deduction], list[SavingsPoint]]


def database_url(root: Path | None = None) -> str:
    """The demo database, from the environment or docker/.env."""
    if url := os.environ.get("DATABASE_URL"):
        return url
    values: dict[str, str] = {}
    env = (root or Path.cwd()) / "docker" / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return (
        f"postgresql+asyncpg://{values.get('POSTGRES_USER', 'cio')}:"
        f"{values.get('POSTGRES_PASSWORD', '')}@localhost:"
        f"{values.get('POSTGRES_PORT', '5432')}/{values.get('POSTGRES_DB', 'cio')}"
    )


async def load_book(url: str) -> tuple[dict[str, History], date, date]:
    """Every member's history, and the span the data covers.

    The span matters as much as the histories: a horizon whose window runs past
    the last observed day cannot be labelled, and a trainer that does not know
    where the data stops will label those rows clean.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    events: dict[str, list[DueEvent]] = defaultdict(list)
    deductions: dict[str, list[Deduction]] = defaultdict(list)
    savings: dict[str, list[SavingsPoint]] = defaultdict(list)

    async with engine.connect() as connection:
        rows = await connection.stream(
            text("""
        SELECT a.member_id, s.due_date, s.amount_due,
               min(p.paid_at) FILTER (WHERE NOT p.reversed) AS paid_at,
               coalesce(sum(p.amount_paid) FILTER (WHERE NOT p.reversed), 0) AS amount_paid
          FROM core.schedule s
          JOIN core.account a ON a.account_id = s.account_id
          LEFT JOIN core.payment p ON p.schedule_id = s.schedule_id
         GROUP BY a.member_id, s.schedule_id, s.due_date, s.amount_due
         ORDER BY a.member_id, s.due_date
    """)
        )
        async for row in rows.mappings():
            paid_at = row["paid_at"]
            events[row["member_id"]].append(
                DueEvent(
                    due_date=row["due_date"],
                    amount_due=float(row["amount_due"]),
                    paid_at=paid_at.date() if paid_at else None,
                    amount_paid=float(row["amount_paid"]),
                )
            )

        rows = await connection.stream(
            text("""
        SELECT member_id, cycle, expected_amount, received_amount, employer_id
          FROM core.deduction ORDER BY member_id, cycle
    """)
        )
        async for row in rows.mappings():
            year, month = (int(part) for part in str(row["cycle"]).split("-"))
            deductions[row["member_id"]].append(
                Deduction(
                    cycle=date(year, month, 1),
                    expected=float(row["expected_amount"] or 0),
                    actual=float(row["received_amount"] or 0),
                    employer_id=row["employer_id"] or "",
                )
            )

        rows = await connection.stream(
            text("""
        SELECT member_id, as_of, balance,
               balance - lag(balance) OVER (PARTITION BY member_id ORDER BY as_of) AS delta
          FROM core.savings ORDER BY member_id, as_of
    """)
        )
        async for row in rows.mappings():
            savings[row["member_id"]].append(
                SavingsPoint(
                    balance=float(row["balance"]),
                    at=row["as_of"],
                    contribution=max(float(row["delta"] or 0), 0.0),
                )
            )

        span = (
            (
                await connection.execute(
                    text("SELECT min(due_date) AS first, max(due_date) AS last FROM core.schedule")
                )
            )
            .mappings()
            .first()
        )

    await engine.dispose()

    book: dict[str, History] = {
        member_id: (member_events, deductions.get(member_id, []), savings.get(member_id, []))
        for member_id, member_events in events.items()
    }
    assert span is not None
    return book, span["first"], span["last"]


def observed_end(book: dict[str, History]) -> date:
    """The last day anything was actually settled.

    Distinct from the last scheduled due date: a schedule runs years into the
    future, and labelling against it would mark every unpaid future instalment
    as an outcome that has not happened yet.
    """
    latest: date | None = None
    for events, _, _ in book.values():
        for event in events:
            if event.paid_at and (latest is None or event.paid_at > latest):
                latest = event.paid_at
    return latest or date.today()


def as_dict(book: dict[str, History]) -> dict[str, Any]:
    return {
        "members": len(book),
        "due_events": sum(len(events) for events, _, _ in book.values()),
    }
