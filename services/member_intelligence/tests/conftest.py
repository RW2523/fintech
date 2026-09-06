"""Fixtures for member-intelligence-service."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from datetime import date
from functools import lru_cache
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cio_common.testing import prepared_test_database_url

ROOT = Path(__file__).resolve().parents[3]


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
def postgres_is_up() -> bool:
    try:
        with socket.create_connection(("localhost", 5432), 2):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def _database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up`")
    monkeypatch.setenv("DATABASE_URL", database_url())


@pytest_asyncio.fixture
async def db():  # type: ignore[no-untyped-def]
    """A session with the schema present. The seeded core data is left alone."""
    from app.db import dispose, engine, session, sessions
    from app.schema import apply_ddl
    from app.settings import settings

    settings.cache_clear()
    engine.cache_clear()
    sessions.cache_clear()

    async with engine().begin() as conn:
        await apply_ddl(conn, partitions_from=date(2023, 9, 1), months=60)
    async with session() as s:
        yield s
    await dispose()


@pytest_asyncio.fixture
async def client(db) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://member") as http:
        yield http


@pytest_asyncio.fixture
async def seeded_member(db) -> str:  # type: ignore[no-untyped-def]
    """A member from the loaded synthetic population, with a timeline."""
    row = (
        await db.execute(
            text("""
        SELECT m.member_id FROM core.member m
        JOIN core.account a ON a.member_id = m.member_id
        GROUP BY m.member_id HAVING count(a.account_id) >= 1
        ORDER BY m.member_id LIMIT 1
    """)
        )
    ).scalar_one_or_none()
    if row is None:
        pytest.skip("no synthetic population loaded; run `synthetic.cli load`")

    from app.importer import import_members

    await import_members(db, [row])
    return str(row)
