"""Fixtures for feature-service."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cio_common.testing import seeded_database_url

ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def database_url() -> str:
    """The demo database, which is the exception rather than the rule.

    Every other service's tests use `cio_test`, because they truncate. These
    do not: they compute features over a member with a twenty-event history,
    which no fixture can build without rebuilding the generator, so they read
    the population the generator made and delete exactly the snapshots they
    created (`_leave_no_trace` below).

    Pointed at `cio_test` these fail on every test, because a database with no
    `core.member` rows has no member to compute features for.
    """
    return seeded_database_url(ROOT)


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
    from app.db import dispose, engine, session, sessions
    from app.schema import apply_ddl
    from app.settings import settings

    settings.cache_clear()
    engine.cache_clear()
    sessions.cache_clear()

    async with engine().begin() as conn:
        await apply_ddl(conn)
    async with session() as s:
        yield s
    await dispose()


@pytest_asyncio.fixture(autouse=True)
async def _leave_no_trace(db):  # type: ignore[no-untyped-def]
    """Remove snapshots this test created.

    These tests read the seeded population, so they run against the demo
    database rather than `cio_test`. Truncating would destroy demo data, so
    each test instead deletes only the snapshots that appeared while it ran.
    """
    from sqlalchemy import bindparam

    query = text("SELECT snapshot_id FROM app_feature.feature_snapshot")
    before = list((await db.execute(query)).scalars())
    yield
    await db.execute(
        text("DELETE FROM app_feature.feature_snapshot WHERE snapshot_id NOT IN :keep").bindparams(
            bindparam("keep", expanding=True)
        ),
        {"keep": before or [""]},
    )
    await db.commit()


@pytest_asyncio.fixture
async def client(db) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://feature") as http:
        yield http


@pytest_asyncio.fixture
async def seeded_member(db) -> str:  # type: ignore[no-untyped-def]
    """A member with a timeline, from the loaded synthetic population."""
    row = (
        await db.execute(
            text("""
        SELECT member_id FROM app_member.member_event
        GROUP BY member_id HAVING count(*) > 20 ORDER BY member_id LIMIT 1
    """)
        )
    ).scalar_one_or_none()
    if row is None:
        pytest.skip("no member timelines; run the T-025 import first")
    return str(row)
