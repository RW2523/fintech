"""Fixtures for lmi-service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cio_common.testing import postgres_is_up, prepared_test_database_url

ROOT = Path(__file__).resolve().parents[3]

MEMBER = "M-000001"
ACCOUNT = "A-000001"
EMPLOYER = "E-01"
AS_OF = date(2026, 9, 1)


def database_url() -> str:
    """The test database, never the demo one."""
    return prepared_test_database_url(ROOT)


@pytest.fixture(autouse=True)
def _database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up` to include lmi tests")
    monkeypatch.setenv("DATABASE_URL", database_url())


CORE_DDL = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE IF NOT EXISTS core.member (member_id text PRIMARY KEY, employer_id text);
CREATE TABLE IF NOT EXISTS core.account (
  account_id text PRIMARY KEY, member_id text NOT NULL, opened_at date
);
CREATE TABLE IF NOT EXISTS core.schedule (
  schedule_id text PRIMARY KEY, account_id text NOT NULL, seq int,
  due_date date NOT NULL, amount_due numeric(18,2) NOT NULL
);
CREATE TABLE IF NOT EXISTS core.payment (
  payment_id text PRIMARY KEY, schedule_id text NOT NULL,
  paid_at timestamptz NOT NULL, amount_paid numeric(18,2) NOT NULL,
  reversed boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS core.deduction (
  deduction_id text PRIMARY KEY, member_id text NOT NULL, employer_id text NOT NULL,
  cycle text NOT NULL, expected_amount numeric(18,2) NOT NULL,
  received_amount numeric(18,2), received_at timestamptz, net_salary numeric(18,2)
);
CREATE TABLE IF NOT EXISTS core.savings (
  member_id text NOT NULL, as_of date NOT NULL, balance numeric(18,2) NOT NULL,
  PRIMARY KEY (member_id, as_of)
);
"""


@pytest_asyncio.fixture
async def db(_database_env: None):  # type: ignore[no-untyped-def]
    from app.db import dispose, engine, session, sessions
    from app.schema import apply_ddl
    from app.settings import settings

    settings.cache_clear()
    engine.cache_clear()
    sessions.cache_clear()

    async with engine().begin() as conn:
        await apply_ddl(conn)
        # The core belongs to core-stub; the shape is recreated here so the
        # materialiser can be exercised against constructed behaviour without
        # standing up another service.
        for statement in [s.strip() for s in CORE_DDL.split(";") if s.strip()]:
            await conn.execute(text(statement))

    async with session() as s:
        await s.execute(
            text("""
            TRUNCATE app_lmi.temporal_features, app_lmi.materialisation,
                     core.payment, core.schedule, core.account,
                     core.deduction, core.savings, core.member, core.employer CASCADE
        """)
        )
        await s.commit()
    async with session() as s:
        yield s
    await dispose()


async def give_history(
    db: Any,
    *,
    member_id: str = MEMBER,
    timings: list[int | None],
    deductions: list[float] | None = None,
    savings: list[float] | None = None,
    start: date | None = None,
) -> None:
    """Write one member's behaviour into the core, one instalment a month."""
    opened = start or (AS_OF - timedelta(days=30 * len(timings)))
    # The employer has to exist first: core.member references it, and a member
    # with a dangling employer is not a case this engine has to handle.
    await db.execute(
        text("""INSERT INTO core.employer (employer_id, name, sector)
                VALUES (:e, 'Test Employer', 'PUBLIC_ADMIN') ON CONFLICT DO NOTHING"""),
        {"e": EMPLOYER},
    )
    await db.execute(
        text("""INSERT INTO core.member (member_id, name_token, joined_at, employer_id)
                VALUES (:m, :token, :joined, :e) ON CONFLICT DO NOTHING"""),
        {
            "m": member_id,
            "token": f"«{member_id}»",
            "joined": opened - timedelta(days=365),
            "e": EMPLOYER,
        },
    )
    await db.execute(
        text("""INSERT INTO core.account
                (account_id, member_id, product_code, principal, profit_rate,
                 tenor_months, instalment, due_day, opened_at, status)
                VALUES (:a, :m, 'PF-STD', 5000, 0.065, :n, 250, 1, :o, 'ACTIVE')
                ON CONFLICT DO NOTHING"""),
        {"a": f"A-{member_id}", "m": member_id, "n": len(timings), "o": opened},
    )
    for index, timing in enumerate(timings):
        due = opened + timedelta(days=30 * index)
        schedule_id = f"S-{member_id}-{index:03d}"
        await db.execute(
            text(
                "INSERT INTO core.schedule (schedule_id, account_id, seq, due_date, amount_due) "
                "VALUES (:s, :a, :seq, :d, 250) ON CONFLICT DO NOTHING"
            ),
            {"s": schedule_id, "a": f"A-{member_id}", "seq": index, "d": due},
        )
        if timing is None:
            continue
        await db.execute(
            text(
                "INSERT INTO core.payment (payment_id, schedule_id, paid_at, amount_paid) "
                "VALUES (:p, :s, :at, 250) ON CONFLICT DO NOTHING"
            ),
            {"p": f"P-{member_id}-{index:03d}", "s": schedule_id, "at": due + timedelta(days=timing)},
        )

    for index, amount in enumerate(deductions or []):
        cycle = opened + timedelta(days=30 * index)
        await db.execute(
            text("""INSERT INTO core.deduction
                    (deduction_id, member_id, employer_id, cycle, expected_amount, received_amount)
                    VALUES (:id, :m, :e, :c, 250, :got) ON CONFLICT DO NOTHING"""),
            {
                "id": f"D-{member_id}-{index:03d}",
                "m": member_id,
                "e": EMPLOYER,
                "c": f"{cycle:%Y-%m}",
                "got": amount,
            },
        )

    for index, balance in enumerate(savings or []):
        await db.execute(
            text(
                "INSERT INTO core.savings (member_id, as_of, balance) VALUES (:m, :d, :b) "
                "ON CONFLICT DO NOTHING"
            ),
            {"m": member_id, "d": opened + timedelta(days=30 * index), "b": balance},
        )
    await db.commit()


@pytest_asyncio.fixture
async def client(db) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://lmi") as http:
        yield http
