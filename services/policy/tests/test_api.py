"""T-011 — the policy-service HTTP surface (docs/08 §4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import cio_contracts
from app.main import app

ROOT = Path(__file__).resolve().parents[3]

CLEAN: dict[str, Any] = {
    "member_tenure_months": 110,
    "member_age": 41,
    "member_total_exposure": "0",
    "member_grade": "B",
    "requested_amount": "8000",
    "requested_tenor": 24,
    "requested_purpose": "EDUCATION",
    "documents_present": ["IDENTITY", "PAYSLIP_LATEST_3", "EMPLOYMENT_CONFIRMATION"],
    "documents_min_critical_confidence": 0.94,
    "income_verified_monthly": "4200",
    "commitments_monthly": "600",
}


@pytest.fixture(autouse=True)
def _database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/evaluate` freezes a replay case, so it needs a database.

    It did not before T-073: it was pure, and the sandbox could only replay
    cases a fixture had seeded, so no case the platform actually decided was
    ever replayable. Freezing the inputs at evaluation is what fixed that, and
    the cost is that this endpoint now touches the database.
    """
    from cio_common.testing import postgres_is_up, prepared_test_database_url

    if not postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up`")
    monkeypatch.setenv("DATABASE_URL", prepared_test_database_url(ROOT))

    from app.db import engine, sessions
    from app.settings import settings

    settings.cache_clear()
    engine.cache_clear()
    sessions.cache_clear()
    _apply_schema()


def _apply_schema() -> None:
    """Create this service's tables in the test database.

    Synchronous, because the fixture is: an async fixture would run inside the
    loop the test is about to use, and the engine is cached per loop.
    """
    import asyncio

    from app.db import dispose, engine
    from app.schema import apply_ddl

    async def create() -> None:
        async with engine().begin() as connection:
            await apply_ddl(connection)
        await dispose()

    asyncio.run(create())


@pytest.fixture
async def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://policy")


async def test_versions_lists_what_is_on_file(client: AsyncClient) -> None:
    """The newest version on disk is the active one.

    Asserted as a property rather than as the literal "2026.09.1": adopting a
    sandbox candidate writes 2026.09.2, which is the point of T-073, and a test
    naming today's version fails the first time the Board changes the policy.
    """
    async with client as http:
        body = (await http.get("/policy/PF-STD/versions")).json()
    assert "2026.09.1" in body["versions"]
    assert body["active"] == sorted(body["versions"])[-1]


async def test_an_unknown_product_is_not_found(client: AsyncClient) -> None:
    async with client as http:
        response = await http.get("/policy/PF-WIZARD/versions")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_a_pack_can_be_fetched_in_full(client: AsyncClient) -> None:
    async with client as http:
        body = (await http.get("/policy/PF-STD/2026.09.1")).json()
    assert body["policy_version"] == "policy/PF-STD/2026.09.1"
    assert body["dff"]["weights"]["CAPACITY"] == 0.35
    assert body["autonomy"]["setting"] == "ASSIST"


async def test_evaluate_returns_a_valid_policy_result(client: AsyncClient) -> None:
    async with client as http:
        response = await http.post("/policy/evaluate", json={"product_code": "PF-STD", "inputs": CLEAN})
    assert response.status_code == 200

    body = response.json()
    assert body["blockers"] == []
    contract = {k: v for k, v in body.items() if k not in ("snapshot_id", "capacity_score")}
    cio_contracts.validate(contract, "PolicyResult")


async def test_evaluate_carries_the_snapshot_id_through(client: AsyncClient) -> None:
    async with client as http:
        body = (
            await http.post(
                "/policy/evaluate",
                json={
                    "product_code": "PF-STD",
                    "snapshot_id": "snap_01JQZK7M8N9P0Q1R2S3T4V5W6X",
                    "inputs": CLEAN,
                },
            )
        ).json()
    assert body["snapshot_id"] == "snap_01JQZK7M8N9P0Q1R2S3T4V5W6X"


async def test_evaluate_rejects_an_unknown_input_field(client: AsyncClient) -> None:
    async with client as http:
        response = await http.post(
            "/policy/evaluate", json={"product_code": "PF-STD", "inputs": {**CLEAN, "surprise": 1}}
        )
    assert response.status_code == 422


async def test_evaluate_rejects_a_malformed_grade(client: AsyncClient) -> None:
    async with client as http:
        response = await http.post(
            "/policy/evaluate", json={"product_code": "PF-STD", "inputs": {**CLEAN, "member_grade": "Z"}}
        )
    assert response.status_code == 422


async def test_affordability_returns_the_calculation_and_its_calc_id(client: AsyncClient) -> None:
    async with client as http:
        body = (
            await http.post("/policy/affordability", json={"product_code": "PF-STD", "inputs": CLEAN})
        ).json()
    assert body["calc_id"].startswith("calc_")
    assert body["dsr"] == 0.2325
    assert body["instalment"] == "376.67"
    assert body["capacity_score"] == 100
    assert len(body["inputs_digest"]) == 64


async def test_an_affordability_override_changes_the_result(client: AsyncClient) -> None:
    """The Challenger may ask for a substitution (docs/06 §5.3)."""
    async with client as http:
        baseline = (
            await http.post("/policy/affordability", json={"product_code": "PF-STD", "inputs": CLEAN})
        ).json()
        substituted = (
            await http.post(
                "/policy/affordability",
                json={
                    "product_code": "PF-STD",
                    "inputs": CLEAN,
                    "overrides": {"income_verified_monthly": "3000"},
                },
            )
        ).json()

    assert substituted["dsr"] > baseline["dsr"]
    assert substituted["overrides_applied"] == ["income_verified_monthly"]


async def test_an_unsupported_override_is_refused(client: AsyncClient) -> None:
    async with client as http:
        response = await http.post(
            "/policy/affordability",
            json={"product_code": "PF-STD", "inputs": CLEAN, "overrides": {"dsr_limit": 0.99}},
        )
    assert response.status_code == 422
    assert "dsr_limit" in response.json()["error"]["message"]


async def test_shariah_and_standard_products_evaluate_differently(client: AsyncClient) -> None:
    consolidation = {**CLEAN, "requested_purpose": "DEBT_CONSOLIDATION"}
    async with client as http:
        std = (
            await http.post("/policy/evaluate", json={"product_code": "PF-STD", "inputs": consolidation})
        ).json()
        shariah = (
            await http.post("/policy/evaluate", json={"product_code": "PF-SHARIAH", "inputs": consolidation})
        ).json()

    assert std["blockers"] == []
    assert "SHR-01" in shariah["blockers"]
