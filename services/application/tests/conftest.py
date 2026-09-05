"""Fixtures: application-service against the compose PostgreSQL."""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def database_url() -> str:
    if url := os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL"):
        return url
    values: dict[str, str] = {}
    env = ROOT / "docker" / ".env"
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
