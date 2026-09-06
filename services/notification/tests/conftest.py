"""Fixtures for notification-service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cio_common.testing import postgres_is_up, prepared_test_database_url

ROOT = Path(__file__).resolve().parents[3]

MEMBER = "M-000042"
ACCOUNT = "A-000042"

REMINDER_VARIABLES = {
    "member_name": "the member",
    "amount": "262.22",
    "account_ref": ACCOUNT,
    "due_date": "2026-10-01",
    "cooperative_name": "the cooperative",
}

OUTREACH_VARIABLES = {
    "member_name": "the member",
    "message": "We noticed your instalments arriving later than usual.",
    "officer_name": "A. Officer",
    "cooperative_name": "the cooperative",
}


def database_url() -> str:
    """The test database, never the demo one."""
    return prepared_test_database_url(ROOT)


@pytest.fixture(autouse=True)
def _database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up` to include notification tests")
    monkeypatch.setenv("DATABASE_URL", database_url())


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
    async with session() as s:
        await s.execute(text("TRUNCATE app_notification.message, app_notification.outcome"))
        await s.commit()
    async with session() as s:
        yield s
    await dispose()


@pytest_asyncio.fixture
async def client(db) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://notification") as http:
        yield http
