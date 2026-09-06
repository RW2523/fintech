"""Fixtures: application-service against the compose PostgreSQL."""

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
        pytest.skip("PostgreSQL not reachable; run `make up` to include these tests")
    monkeypatch.setenv("DATABASE_URL", database_url())


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """A client with a clean schema and fixed version stamps."""
    from app.db import dispose, engine, session, sessions
    from app.routes import set_version_source
    from app.schema import apply_ddl
    from app.settings import settings
    from app.versions import StaticVersionSource

    settings.cache_clear()
    engine.cache_clear()
    sessions.cache_clear()

    async with engine().begin() as conn:
        await apply_ddl(conn)
    async with session() as db:
        await db.execute(
            text("""
            TRUNCATE app_application.case_snapshot, app_application.case,
                     app_application.application, events.outbox RESTART IDENTITY CASCADE
        """)
        )

    set_version_source(
        StaticVersionSource(
            model_versions={
                "risk": "credit_risk/1.2.0",
                "fraud": "fraud/0.9.1",
                "delinquency": "lmi/1.0.0",
                "document_ai": "docai/2.1.0",
                "embed": "bge-m3/1.0",
            }
        )
    )

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://application") as http:
        yield http

    set_version_source(None)
    await dispose()
