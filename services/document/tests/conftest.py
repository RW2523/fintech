"""Fixtures for document-service."""

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
CORPUS = ROOT / "synthetic" / "out" / "documents"


@lru_cache(maxsize=1)
def database_url() -> str:
    """The test database, never the demo one.

    Truncating the tables a test exercises is the only way to test a chain or
    a queue from a known start, and doing that to the demo database empties the
    population, the loaded documents and the decisions in front of the
    workbench without saying so.
    """
    return prepared_test_database_url(ROOT)


def _reachable(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), 2):
            return True
    except OSError:
        return False


@lru_cache(maxsize=1)
def postgres_is_up() -> bool:
    return _reachable(5432)


@lru_cache(maxsize=1)
def minio_is_up() -> bool:
    return _reachable(9000)


@pytest.fixture(autouse=True)
def _database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up`")
    monkeypatch.setenv("DATABASE_URL", database_url())
    env = ROOT / "docker" / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if line.startswith(("MINIO_ROOT_USER=", "MINIO_ROOT_PASSWORD=")):
                key, _, value = line.partition("=")
                monkeypatch.setenv(key, value.strip())
    monkeypatch.setenv("MINIO_ENDPOINT", "localhost:9000")


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from app.db import dispose, engine, session, sessions
    from app.schema import apply_ddl
    from app.settings import settings
    from app.storage import storage

    settings.cache_clear()
    engine.cache_clear()
    sessions.cache_clear()
    storage.cache_clear()

    async with engine().begin() as conn:
        await apply_ddl(conn)
    async with session() as db:
        await db.execute(
            text("""
            TRUNCATE app_document.finding, app_document.extraction,
                     app_document.ground_truth, app_document.document CASCADE
        """)
        )

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://document") as http:
        yield http
    await dispose()


@pytest.fixture(scope="session")
def sample_documents() -> dict[str, Path]:
    """One generated file per type, from the corpus on disk."""
    import json

    if not (CORPUS / "documents.jsonl").is_file():
        pytest.skip("no document corpus; run `synthetic.cli documents` first")

    chosen: dict[str, Path] = {}
    for line in (CORPUS / "documents.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row["type"] not in chosen:
            chosen[row["type"]] = CORPUS / "files" / row["filename"]
    return chosen
