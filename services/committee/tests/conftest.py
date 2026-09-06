"""Fixtures for the committee orchestrator."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from cio_common.testing import prepared_test_database_url

ROOT = Path(__file__).resolve().parents[3]

COUNCIL = (
    "document_evidence",
    "policy_affordability",
    "credit_risk",
    "fraud_integrity",
    "member_relationship",
)
OWNS = {
    "policy_affordability": ("CAPACITY", 62),
    "credit_risk": ("CONDUCT", 79),
    "fraud_integrity": ("INTEGRITY", 80),
    "member_relationship": ("COMMITMENT", 70),
}


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


def opinion(
    agent_id: str,
    *,
    stance: str = "SUPPORT",
    confidence: float = 0.9,
    unresolved: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """An answer from one agent, in the shape the runtime returns."""
    from cio_common.ids import new_id

    body: dict[str, Any] = {
        "schema": "agent_opinion/1.3",
        # Fresh each time: the id is the primary key, and a fixture reusing one
        # made every insert after the first a silent no-op.
        "opinion_id": new_id("op"),
        "committee_run_id": "run_placeholder",
        "snapshot_id": "snap_placeholder",
        "agent_id": agent_id,
        "agent_version": "v1",
        "round": "ASSESS",
        "stance": stance,
        "confidence": confidence,
        "reason_codes": [],
        "claims": [],
        "contradictions": [],
        "unresolved": unresolved or [],
        "proposed_actions": [],
        "tool_calls": [],
        "signature": "sha256:test",
        "created_at": "2026-09-06T00:00:00+00:00",
    }
    if agent_id in OWNS:
        family, score = OWNS[agent_id]
        body["factor_scores"] = {
            family: {
                "score": score,
                "weight": 0.3,
                "weighted": round(score * 0.3, 2),
                "decisive": False,
                "calc_id": f"calc_{family}",
            }
        }
    return {"opinion": body, "degraded": False, "attempts": 1, "latency_ms": 12.0}


@pytest.fixture
def snapshot(request: pytest.FixtureRequest) -> dict[str, Any]:
    """A snapshot id unique to this test.

    A run is idempotent on (snapshot_id, tier), so tests sharing one id would
    have every run after the first join the first, which is the behaviour under
    test in exactly one of them.
    """
    from cio_common.ids import derived_id

    return {
        "snapshot_id": derived_id("snap", request.node.name),
        "case_id": "case_01ARZ3NDEKTSV4RRFFQ69G5FAW",
        "product_code": "PF-STD",
        "amount": "8000.00",
        "tenor_months": 36,
        "policy_version": "policy/PF-STD/2026.09.1",
        "model_versions": {"risk": "2026.09.1"},
        "member": {"member_ref": "«MEMBER_1»", "tenure_months": 108},
    }


@pytest.fixture
def record() -> dict[str, Any]:
    return {
        "decision_record_id": "dr_01ARZ3NDEKTSV4RRFFQ69G5FAX",
        "recommendation": "APPROVE",
        "route": "OFFICER_REVIEW",
        "route_reasons": ["SETTING_ASSIST"],
        "tier": "STANDARD",
        "weighted_score": 84.2,
        "confidence": 0.91,
        "factor_scores": {"CAPACITY": {"score": 84, "decisive": True}},
        "hard_gates": [{"rule_id": "ELG-02", "result": "PASS"}],
        "policy_version": "policy/PF-STD/2026.09.1",
        "dff_version": "dff/PF-STD/2026.09.1",
    }


@pytest.fixture
def fake(record: dict[str, Any]):  # type: ignore[no-untyped-def]
    from app.clients import FakeClients

    return FakeClients(
        opinions={agent: opinion(agent) for agent in (*COUNCIL, "challenger")},
        synthesis=record,
        narration={"json": {"member": "m", "officer": "o", "auditor": "a"}},
    )


@pytest_asyncio.fixture(autouse=True)
async def _fresh_snapshot(db, snapshot) -> None:  # type: ignore[no-untyped-def]
    """Remove any run this test's snapshot already has.

    A run is idempotent on (snapshot_id, tier) and the table outlives the
    suite, so without this a test joins the run its own previous execution
    left behind, and asserts against that instead of what it just did.
    """
    from sqlalchemy import text

    await db.execute(
        text("DELETE FROM app_committee.run WHERE snapshot_id = :snapshot_id"),
        {"snapshot_id": snapshot["snapshot_id"]},
    )
    await db.commit()


@pytest_asyncio.fixture
async def client(db, fake) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    from app.main import app
    from app.routes import set_clients

    set_clients(fake)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://committee") as http:
            yield http
    finally:
        set_clients(None)
