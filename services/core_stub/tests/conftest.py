"""Fixtures: an app wired to the compose PostgreSQL, seeded per test."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cio_common.testing import prepared_test_database_url

ROOT = Path(__file__).resolve().parents[3]


#: These fixtures truncate the whole `core` schema, so they run against a
#: separate database. Pointing them at the demo database would wipe the seeded
#: population every time the suite ran.
TEST_DATABASE = "cio_test"


@lru_cache(maxsize=1)
def database_url() -> str:
    """The test database, never the demo one.

    Truncating the tables a test exercises is the only way to test a chain or
    a queue from a known start, and doing that to the demo database empties the
    population, the loaded documents and the decisions in front of the
    workbench without saying so.
    """
    return prepared_test_database_url(ROOT)


@lru_cache(maxsize=1)
def _postgres_is_up() -> bool:
    parsed = urlparse(database_url().replace("+asyncpg", ""))
    try:
        with socket.create_connection((parsed.hostname or "localhost", parsed.port or 5432), 2):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def _database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not _postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up` to include core-stub tests")
    monkeypatch.setenv("DATABASE_URL", database_url())


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the app, with the core schema emptied first."""
    from app.db import dispose, engine, session, sessions
    from app.schema import apply_ddl
    from app.settings import settings

    settings.cache_clear()
    engine.cache_clear()
    sessions.cache_clear()

    async with engine().begin() as conn:
        await apply_ddl(conn)

    async with session() as db:
        await db.execute(
            text("""
            TRUNCATE core.write_log, core.change_feed, core.outcome, core.arrangement,
                     core.outage_window, core.bureau, core.guarantor, core.share_capital,
                     core.savings, core.deduction, core.payment, core.schedule,
                     core.account, core.application_ext, core.member, core.employer
            RESTART IDENTITY CASCADE
        """)
        )

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://core") as http:
        yield http

    await dispose()


@pytest_asyncio.fixture
async def seeded(client: AsyncClient) -> AsyncClient:
    """One employer, one member, one account with a schedule and a payment."""
    from app.db import session

    async with session() as db:
        await db.execute(
            text("""
            INSERT INTO core.employer (employer_id, name, sector, template_id, deduction_day)
            VALUES ('E-001', 'Northern Utilities', 'UTILITIES', 'tpl-03', 26)
        """)
        )
        await db.execute(
            text("""
            INSERT INTO core.member (member_id, name_token, dob, joined_at, status, branch_id,
                                     employer_id, salary_monthly, identity_verified, language)
            VALUES ('M-000042', 'MBR-4F2A', :dob, :joined, 'ACTIVE', 'B-01', 'E-001',
                    4200.00, true, 'en')
        """),
            {"dob": date(1985, 4, 2), "joined": date(2016, 6, 1)},
        )
        await db.execute(
            text("""
            INSERT INTO core.account (account_id, member_id, product_code, principal, profit_rate,
                                      tenor_months, instalment, due_day, opened_at, status)
            VALUES ('A-1001', 'M-000042', 'PF-STD', 24000.00, 0.0650, 24, 1130.00, 5,
                    :opened, 'ACTIVE')
        """),
            {"opened": date(2025, 1, 5)},
        )
        await db.execute(
            text("""
            INSERT INTO core.schedule (schedule_id, account_id, seq, due_date, amount_due)
            VALUES ('S-1001-001', 'A-1001', 1, :d1, 1130.00),
                   ('S-1001-002', 'A-1001', 2, :d2, 1130.00)
        """),
            {"d1": date(2025, 2, 5), "d2": date(2025, 3, 5)},
        )
        await db.execute(
            text("""
            INSERT INTO core.payment (payment_id, schedule_id, paid_at, amount_paid, channel)
            VALUES ('P-1', 'S-1001-001', :at, 1130.00, 'DEDUCTION')
        """),
            {"at": datetime(2025, 2, 4, 9, 0, tzinfo=UTC)},
        )
        await db.execute(
            text("""
            INSERT INTO core.deduction (deduction_id, member_id, employer_id, cycle,
                                        expected_amount, received_amount, received_at)
            VALUES ('D-1', 'M-000042', 'E-001', '2025-02', 1130.00, 1130.00, :at)
        """),
            {"at": datetime(2025, 2, 3, 9, 0, tzinfo=UTC)},
        )
        await db.execute(
            text("""
            INSERT INTO core.savings (member_id, as_of, balance)
            VALUES ('M-000042', :d, 8400.00)
        """),
            {"d": date(2025, 2, 28)},
        )
        await db.execute(
            text("""
            INSERT INTO core.share_capital (member_id, as_of, units, value)
            VALUES ('M-000042', :d, 150, 1500.00)
        """),
            {"d": date(2025, 2, 28)},
        )
        await db.execute(
            text("""
            INSERT INTO core.bureau (member_id, grade, adverse_flags, as_of)
            VALUES ('M-000042', 'B', '{}', :d)
        """),
            {"d": date(2025, 2, 28)},
        )
    return client
