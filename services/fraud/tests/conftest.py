"""Fixtures for fraud-service."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from cio_common.testing import prepared_test_database_url

ROOT = Path(__file__).resolve().parents[3]

RING = tuple(f"M-{i:06d}" for i in range(1, 8))
APPLIED = date(2026, 7, 27)


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
        yield s
    await dispose()


def _bundle(**overrides: Any):  # type: ignore[no-untyped-def]
    from app.facts import CaseBundle
    from app.graph import EntityGraph
    from app.rules import CaseFacts

    graph = overrides.pop("graph", None) or EntityGraph()
    base: dict[str, Any] = {
        "case_id": "case_clean",
        "member_id": RING[0],
        "applied_at": APPLIED,
        "employer_id": "E-019",
        "branch_id": "BR-01",
    }
    return CaseBundle(
        facts=CaseFacts(**{**base, **overrides}),
        graph=graph,
        sources={"member": "test", "guarantees": "test", "documents": "test"},
    )


@pytest.fixture
def source():  # type: ignore[no-untyped-def]
    """Three cases: clean, a guarantee ring, and a duplicate applicant."""
    from app.facts import StaticFactSource
    from app.graph import EntityGraph

    ring_graph = EntityGraph()
    for index, member in enumerate(RING):
        ring_graph.add_guarantee(member, RING[(index + 1) % len(RING)])
    applications = {m: APPLIED - timedelta(days=i * 12) for i, m in enumerate(RING[:4])}

    static = StaticFactSource()
    static.add("case_clean", _bundle())
    static.add(
        "case_ring", _bundle(case_id="case_ring", graph=ring_graph, applications_by_member=applications)
    )
    static.add("case_duplicate", _bundle(case_id="case_duplicate", duplicate_identities=("M-000099",)))
    static.add(
        "case_identity",
        _bundle(
            case_id="case_identity",
            document_findings=(
                {
                    "code": "INT-08",
                    "severity": "HIGH",
                    "document_id": "DOC-1",
                    "detail": {"check": "identity_match"},
                },
            ),
        ),
    )
    return static


@pytest_asyncio.fixture
async def client(db, source) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    from app.main import app
    from app.routes import set_fact_source

    set_fact_source(source)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fraud") as http:
            yield http
    finally:
        set_fact_source(None)
