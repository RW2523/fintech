"""Database fixtures for the outbox tests.

They run against the compose PostgreSQL when it is reachable and skip with a
clear reason when it is not, so `make test` stays runnable without docker.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cio_common.outbox import apply_ddl


@lru_cache(maxsize=1)
def database_url() -> str:
    """Prefer an explicit URL, else read the compose environment."""
    if url := os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL"):
        return url

    env = Path(__file__).resolve().parents[3] / "docker" / ".env"
    values: dict[str, str] = {}
    if env.is_file():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()

    user = values.get("POSTGRES_USER", "cio")
    password = values.get("POSTGRES_PASSWORD", "change-me")
    port = values.get("POSTGRES_PORT", "5432")
    database = values.get("POSTGRES_DB", "cio")
    return f"postgresql+asyncpg://{user}:{password}@localhost:{port}/{database}"


@lru_cache(maxsize=1)
def _postgres_is_up() -> bool:
    parsed = urlparse(database_url().replace("+asyncpg", ""))
    try:
        with socket.create_connection((parsed.hostname or "localhost", parsed.port or 5432), 2):
            return True
    except OSError:
        return False


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A session against a freshly truncated outbox.

    Function scoped on purpose: pytest-asyncio gives each test its own event
    loop, and an asyncpg pool cannot outlive the loop that created it.
    """
    if not _postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up` to include the outbox tests")

    engine = create_async_engine(database_url(), poolclass=None, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await apply_ddl(conn)
            await conn.execute(text("TRUNCATE events.outbox, events.consumer_offsets"))

        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            yield session
            await session.rollback()
    finally:
        await engine.dispose()
