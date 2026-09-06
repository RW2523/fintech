"""Fixtures for risk-service."""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

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


@pytest.fixture(scope="session")
def trained() -> None:
    """Skip when no model has been trained into the artifact store."""
    from app.scoring import model
    from ml.common.registry import ArtifactError

    try:
        model()
    except (ArtifactError, FileNotFoundError) as exc:
        pytest.skip(f"no trained credit-risk artifacts: {exc}")


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


@pytest.fixture
def snapshot() -> Any:
    """A feature snapshot shaped exactly as the feature service returns one."""
    from app.snapshots import FeatureSnapshot

    return FeatureSnapshot(
        snapshot_id="fs_01JQZK7M8N9P0Q1R2S3T4V5W60",
        member_id="M-000042",
        as_of="2026-04-30T00:00:00+00:00",
        registry_version="features/1.0",
        inputs_digest="c44522463edf8af5",
        values={
            "ontime_rate_24m": 0.96,
            "arrears_events_12m": 0.0,
            "months_since_last_arrears": None,
            "restructures_36m": 0.0,
            "facilities_open": 1.0,
            "facilities_new_6m": 0.0,
            "utilisation": 0.28,
            "tenure_months": 96.0,
            "savings_balance": 7400.0,
            "savings_slope_180d": 8.4,
            "savings_paused_months": 0.0,
            "share_capital_units": 380.0,
            "share_capital_ratio": 0.07,
            "income_verified_monthly": 4800.0,
            "income_source_variance": 0.03,
            "dsr_proposed": 0.24,
            "commitments_monthly": 520.0,
            "employer_sector": "PUBLIC_ADMIN",
            "employer_tenure_months": 84.0,
            "application_count_12m": None,
            "contact_change_days": None,
            "doc_min_conf": None,
            "findings_max_severity": None,
        },
        provenance={
            "ontime_rate_24m": {"source": "app_member.member_event", "due_events": 24},
            "savings_slope_180d": {"source": "app_member.member_event", "points": 6},
            "employer_sector": {"source": "core.employer"},
        },
    )


@pytest.fixture
def troubled(snapshot: Any) -> Any:
    """The same shape, for a member in difficulty."""
    from dataclasses import replace

    return replace(
        snapshot,
        snapshot_id="fs_01JQZK7M8N9P0Q1R2S3T4V5W61",
        member_id="M-000043",
        inputs_digest="0000000000000001",
        values={
            **snapshot.values,
            "ontime_rate_24m": 0.42,
            "arrears_events_12m": 4.0,
            "months_since_last_arrears": 1.0,
            "restructures_36m": 1.0,
            "savings_slope_180d": -26.0,
            "savings_paused_months": 6.0,
            "savings_balance": 40.0,
            "dsr_proposed": 0.63,
            "employer_sector": "RETAIL",
        },
    )


@pytest_asyncio.fixture
async def client(db, trained, snapshot, troubled) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    from app.main import app
    from app.routes import set_snapshot_source
    from app.snapshots import StaticSnapshotSource

    source = StaticSnapshotSource()
    source.add(snapshot)
    source.add(troubled)
    set_snapshot_source(source)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://risk") as http:
            yield http
    finally:
        set_snapshot_source(None)
