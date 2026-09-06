"""Fixtures for audit-service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cio_common.testing import postgres_is_up, prepared_test_database_url

ROOT = Path(__file__).resolve().parents[3]

CASE_ID = "case_01JQZK7M8N9P0Q1R2S3T4V5W6X"


def database_url() -> str:
    """The test database, never the demo one."""
    return prepared_test_database_url(ROOT)


def entry(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "actor": {"id": "u-officer", "role": "CREDIT_OFFICER", "kind": "PERSON"},
        "action": "decision.approved",
        "service": "decision",
        "case_id": CASE_ID,
        "object_ref": {"decision_record_id": "dr_01JQZK7M8N9P0Q1R2S3T4V5W6X"},
        "before": {"state": "OFFICER_REVIEW"},
        "after": {"state": "APPROVED"},
        "policy_version": "policy/PF-STD/2026.09.1",
    }
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def _database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up` to include audit tests")
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
        # TRUNCATE, not DELETE: the append-only trigger fires on DELETE, which
        # is the protection being tested.
        await s.execute(text("TRUNCATE audit.entry, audit.export RESTART IDENTITY"))
        await s.commit()
    async with session() as s:
        yield s
    await dispose()


@pytest_asyncio.fixture
async def client(db) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://audit") as http:
        yield http
