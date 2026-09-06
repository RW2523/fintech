"""T-030 — computing and storing feature snapshots (docs/07 §2.1, docs/04 §4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.compute import SEVERITY_ORDINAL, compute_features
from app.registry import PermittedUse, names

AS_OF = datetime(2026, 4, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------
# computation
# ---------------------------------------------------------------------------
async def test_every_declared_feature_is_computed(db: Any, seeded_member: str) -> None:
    snapshot = await compute_features(db, seeded_member, as_of=AS_OF)
    assert set(snapshot.as_dict()) == set(names())


async def test_the_same_inputs_produce_the_same_values(db: Any, seeded_member: str) -> None:
    """T-030 acceptance: a snapshot must be reproducible."""
    first = await compute_features(db, seeded_member, as_of=AS_OF)
    second = await compute_features(db, seeded_member, as_of=AS_OF)
    assert first.as_dict() == second.as_dict()
    assert first.inputs_digest == second.inputs_digest


async def test_a_different_as_of_produces_a_different_snapshot(db: Any, seeded_member: str) -> None:
    early = await compute_features(db, seeded_member, as_of=datetime(2025, 6, 30, tzinfo=UTC))
    late = await compute_features(db, seeded_member, as_of=AS_OF)
    assert early.inputs_digest != late.inputs_digest
    assert (early.values["tenure_months"] or 0) < (late.values["tenure_months"] or 0)


async def test_a_snapshot_never_sees_the_future(db: Any, seeded_member: str) -> None:
    """A feature computed as of a date must not use anything after it."""
    early = await compute_features(db, seeded_member, as_of=datetime(2025, 1, 31, tzinfo=UTC))
    late = await compute_features(db, seeded_member, as_of=AS_OF)
    assert (early.values["employer_tenure_months"] or 0) <= (late.values["employer_tenure_months"] or 0)
    assert (early.values["facilities_open"] or 0) <= (late.values["facilities_open"] or 0)


async def test_every_value_carries_its_provenance(db: Any, seeded_member: str) -> None:
    snapshot = await compute_features(db, seeded_member, as_of=AS_OF)
    for name in snapshot.as_dict():
        assert snapshot.provenance[name]["source"], f"{name} has no source"


async def test_the_purpose_filter_removes_features(db: Any, seeded_member: str) -> None:
    """T-030 acceptance: the permitted-use filter."""
    underwriting = await compute_features(db, seeded_member, as_of=AS_OF, purpose=PermittedUse.UNDERWRITING)
    collections = await compute_features(db, seeded_member, as_of=AS_OF, purpose=PermittedUse.COLLECTIONS)

    assert set(collections.as_dict()) < set(underwriting.as_dict())
    assert "dsr_proposed" not in collections.as_dict()
    assert collections.provenance["_registry"]["purpose"] == "COLLECTIONS"


async def test_the_proposed_instalment_moves_the_debt_service_ratio(db: Any, seeded_member: str) -> None:
    without = await compute_features(db, seeded_member, as_of=AS_OF)
    with_loan = await compute_features(db, seeded_member, as_of=AS_OF, proposed_instalment=500.0)
    if without.values["dsr_proposed"] is not None:
        assert with_loan.values["dsr_proposed"] > without.values["dsr_proposed"]


async def test_an_unknown_member_is_refused(db: Any) -> None:
    with pytest.raises(KeyError, match="no member"):
        await compute_features(db, "M-999999", as_of=AS_OF)


async def test_rates_stay_inside_their_range(db: Any, seeded_member: str) -> None:
    snapshot = await compute_features(db, seeded_member, as_of=AS_OF)
    for name in ("ontime_rate_24m", "doc_min_conf", "income_source_variance"):
        value = snapshot.values.get(name)
        if value is not None:
            assert 0.0 <= value <= 1.0, f"{name} = {value}"


def test_severity_is_ordered() -> None:
    assert (
        SEVERITY_ORDINAL["CRITICAL"]
        > SEVERITY_ORDINAL["HIGH"]
        > SEVERITY_ORDINAL["MEDIUM"]
        > SEVERITY_ORDINAL["LOW"]
        > SEVERITY_ORDINAL["NONE"]
    )


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------
async def test_a_snapshot_is_stored_and_retrievable(client: AsyncClient, seeded_member: str) -> None:
    created = (
        await client.post(
            "/features/snapshot",
            json={
                "member_id": seeded_member,
                "as_of": AS_OF.isoformat(),
                "reuse_equivalent": False,
            },
        )
    ).json()

    assert created["snapshot_id"].startswith("fs_")
    assert created["reused"] is False
    assert len(created["features"]) == len(names())

    fetched = (await client.get(f"/features/{created['snapshot_id']}")).json()
    assert fetched["features"] == created["features"]
    assert fetched["inputs_digest"] == created["inputs_digest"]


async def test_an_equivalent_snapshot_is_reused(client: AsyncClient, seeded_member: str) -> None:
    body = {"member_id": seeded_member, "as_of": AS_OF.isoformat()}
    first = (await client.post("/features/snapshot", json=body)).json()
    second = (await client.post("/features/snapshot", json=body)).json()

    assert second["reused"] is True
    assert second["snapshot_id"] == first["snapshot_id"]


async def test_reuse_can_be_declined(client: AsyncClient, seeded_member: str) -> None:
    body = {"member_id": seeded_member, "as_of": AS_OF.isoformat()}
    first = (await client.post("/features/snapshot", json=body)).json()
    second = (await client.post("/features/snapshot", json={**body, "reuse_equivalent": False})).json()
    assert second["snapshot_id"] != first["snapshot_id"]
    assert second["features"] == first["features"]


async def test_a_stored_value_cannot_be_edited(client: AsyncClient, seeded_member: str, db: Any) -> None:
    """A model run cites a snapshot, so the snapshot is frozen."""
    created = (
        await client.post("/features/snapshot", json={"member_id": seeded_member, "as_of": AS_OF.isoformat()})
    ).json()

    with pytest.raises(DBAPIError, match="immutable"):
        await db.execute(
            text("""
            UPDATE app_feature.feature_value SET value = 0
            WHERE snapshot_id = :s
        """),
            {"s": created["snapshot_id"]},
        )
    await db.rollback()


async def test_an_unknown_snapshot_is_not_found(client: AsyncClient) -> None:
    response = await client.get("/features/fs_01JQZK7M8N9P0Q1R2S3T4V5W6Z")
    assert response.status_code == 404


async def test_an_unknown_member_is_not_found(client: AsyncClient) -> None:
    response = await client.post("/features/snapshot", json={"member_id": "M-999999"})
    assert response.status_code == 404


async def test_a_malformed_member_id_is_refused(client: AsyncClient) -> None:
    response = await client.post("/features/snapshot", json={"member_id": "nope"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# the published registry
# ---------------------------------------------------------------------------
async def test_the_registry_is_served(client: AsyncClient) -> None:
    body = (await client.get("/features/registry")).json()
    assert body["count"] == len(names())
    assert set(body["families"]) == {"CAPACITY", "COMMITMENT", "CONDITIONS", "CONDUCT", "INTEGRITY"}


async def test_the_registry_can_be_filtered_by_purpose(client: AsyncClient) -> None:
    everything = (await client.get("/features/registry")).json()["count"]
    collections = (await client.get("/features/registry?purpose=COLLECTIONS")).json()["count"]
    assert collections < everything


async def test_an_unknown_purpose_is_refused(client: AsyncClient) -> None:
    response = await client.get("/features/registry?purpose=WIZARDRY")
    assert response.status_code == 422


async def test_the_registry_publishes_to_the_database(client: AsyncClient, db: Any) -> None:
    body = (await client.post("/features/registry/publish")).json()
    assert body["published"] == len(names())

    stored = (await db.execute(text("SELECT count(*) FROM app_feature.feature_def"))).scalar_one()
    assert stored == len(names())
