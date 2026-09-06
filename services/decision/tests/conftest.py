"""Fixtures: decision-service against the compose PostgreSQL."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
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
def _postgres_is_up() -> bool:
    try:
        with socket.create_connection(("localhost", 5432), 2):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def _database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not _postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up` to include decision tests")
    monkeypatch.setenv("DATABASE_URL", database_url())


@pytest_asyncio.fixture
async def db():  # type: ignore[no-untyped-def]
    """A session against an emptied ledger.

    TRUNCATE is used rather than DELETE: the append-only trigger fires on DELETE,
    which is exactly the protection being tested elsewhere.
    """
    from app.db import dispose, engine, session, sessions
    from app.schema import apply_ddl
    from app.settings import settings

    settings.cache_clear()
    engine.cache_clear()
    sessions.cache_clear()

    async with engine().begin() as conn:
        await apply_ddl(conn)
    async with session() as s:
        await s.execute(
            text("""
            TRUNCATE ledger.entry, ledger.token, ledger.sample_review,
                     app_decision.decision_record, app_decision.human_decision
            RESTART IDENTITY
        """)
        )
    async with session() as s:
        yield s
    await dispose()


@pytest_asyncio.fixture
async def client(db) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://decision") as http:
        yield http
