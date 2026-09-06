"""Fixtures for execution-service. Every upstream is a fake."""

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

ACTION_ID = "act_01JQZK7M8N9P0Q1R2S3T4V5W6X"
CASE_ID = "case_01JQZK7M8N9P0Q1R2S3T4V5W6X"
TOKEN_ID = "tok_01JQZK7M8N9P0Q1R2S3T4V5W6X"


def database_url() -> str:
    """The test database, never the demo one."""
    return prepared_test_database_url(ROOT)


def proposal(**overrides: Any) -> dict[str, Any]:
    """An approved financing, ready to be carried out."""
    body: dict[str, Any] = {
        "action_id": ACTION_ID,
        "case_id": CASE_ID,
        "member_id": "M-000042",
        "decision_record_id": "dr_01JQZK7M8N9P0Q1R2S3T4V5W6X",
        "level": "L3",
        "type": "APPROVE_FINANCING",
        "parameters": {
            "product_code": "PF-STD",
            "amount": "8000.00",
            "tenor_months": 36,
            "instalment": "262.22",
            "profit_rate": "0.09",
            "due_day": 1,
        },
        "rationale": {"text": "within policy and affordable", "evidence_refs": []},
        "requires": "OFFICER",
        "proposed_by": "policy",
    }
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def _database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up` to include execution tests")
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
        await s.execute(text("TRUNCATE app_execution.saga_step, app_execution.action CASCADE"))
        await s.commit()
    async with session() as s:
        yield s
    await dispose()


@pytest.fixture
def fake():  # type: ignore[no-untyped-def]
    from app.clients import FakeClients

    return FakeClients()


@pytest_asyncio.fixture
async def client(db, fake) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    from app.main import app
    from app.routes import set_clients

    set_clients(fake)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://execution") as http:
            yield http
    finally:
        set_clients(None)
