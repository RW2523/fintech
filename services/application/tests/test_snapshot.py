"""T-015 — applications, cases and the CaseSnapshot freeze (docs/03 §1)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import cio_contracts

APPLICATION = {
    "member_id": "M-000042",
    "product_code": "PF-STD",
    "amount": "8000.00",
    "tenor_months": 24,
    "purpose": "EDUCATION",
}


async def create(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/applications", json={**APPLICATION, **overrides})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------
async def test_creating_an_application_opens_a_case(client: AsyncClient) -> None:
    body = await create(client)
    assert body["application_id"].startswith("app_")
    assert body["case_id"].startswith("case_")
    assert body["status"] == "DRAFT"


@pytest.mark.parametrize(
    "field,value",
    [
        ("member_id", "nope"),
        ("product_code", "wizard"),
        ("amount", "-5"),
        ("tenor_months", 3),
        ("tenor_months", 200),
        ("purpose", ""),
    ],
)
async def test_a_malformed_application_is_refused(client: AsyncClient, field: str, value: Any) -> None:
    response = await client.post("/applications", json={**APPLICATION, field: value})
    assert response.status_code == 422


async def test_creation_emits_an_event(client: AsyncClient) -> None:
    from app.db import session

    body = await create(client)
    async with session() as db:
        row = (
            (
                await db.execute(
                    text("""
            SELECT name, case_id FROM events.outbox WHERE name = 'application.created'
        """)
                )
            )
            .mappings()
            .one()
        )
    assert row["case_id"] == body["case_id"]


# ---------------------------------------------------------------------------
# the freeze
# ---------------------------------------------------------------------------
async def test_submitting_freezes_a_snapshot_with_every_version_field(client: AsyncClient) -> None:
    """docs/03 §1 — a snapshot names everything the case was judged against."""
    created = await create(client)
    submitted = (
        await client.post(
            f"/applications/{created['application_id']}/submit", json={"document_ids": ["doc_a", "doc_b"]}
        )
    ).json()

    snapshot = (await client.get(f"/cases/{created['case_id']}/snapshot")).json()

    assert snapshot["snapshot_id"] == submitted["snapshot_id"]
    for field in (
        "member_snapshot_ver",
        "document_bundle_ver",
        "feature_snapshot_id",
        "policy_version",
        "dff_version",
        "autonomy_version",
        "evidence_index_ver",
        "hash",
        "created_by",
    ):
        assert snapshot[field], f"{field} is not stamped"

    assert set(snapshot["model_versions"]) == {"risk", "fraud", "delinquency", "document_ai", "embed"}
    assert snapshot["model_versions"]["risk"] == "credit_risk/1.2.0"
    cio_contracts.validate(snapshot, "CaseSnapshot")


async def test_the_snapshot_hash_covers_its_own_content(client: AsyncClient) -> None:
    from cio_common.hashing import canonical_json, sha256

    created = await create(client)
    await client.post(f"/applications/{created['application_id']}/submit", json={})
    snapshot = (await client.get(f"/cases/{created['case_id']}/snapshot")).json()

    recomputed = sha256(
        canonical_json({k: v for k, v in snapshot.items() if k not in ("hash", "snapshot_version")})
    )
    assert recomputed == snapshot["hash"]


async def test_the_document_bundle_version_follows_the_documents(client: AsyncClient) -> None:
    first = await create(client)
    await client.post(
        f"/applications/{first['application_id']}/submit", json={"document_ids": ["doc_a", "doc_b"]}
    )
    second = await create(client)
    await client.post(
        f"/applications/{second['application_id']}/submit", json={"document_ids": ["doc_a", "doc_c"]}
    )

    one = (await client.get(f"/cases/{first['case_id']}/snapshot")).json()
    two = (await client.get(f"/cases/{second['case_id']}/snapshot")).json()
    assert one["document_bundle_ver"] != two["document_bundle_ver"]


async def test_the_bundle_version_ignores_document_order(client: AsyncClient) -> None:
    first = await create(client)
    await client.post(
        f"/applications/{first['application_id']}/submit", json={"document_ids": ["doc_a", "doc_b"]}
    )
    second = await create(client)
    await client.post(
        f"/applications/{second['application_id']}/submit", json={"document_ids": ["doc_b", "doc_a"]}
    )

    one = (await client.get(f"/cases/{first['case_id']}/snapshot")).json()
    two = (await client.get(f"/cases/{second['case_id']}/snapshot")).json()
    assert one["document_bundle_ver"] == two["document_bundle_ver"]


async def test_submitting_again_writes_a_new_version_rather_than_editing(client: AsyncClient) -> None:
    """The earlier decision must stay reconstructable (CLAUDE.md §2.3)."""
    created = await create(client)
    first = (
        await client.post(
            f"/applications/{created['application_id']}/submit", json={"document_ids": ["doc_a"]}
        )
    ).json()
    second = (
        await client.post(
            f"/applications/{created['application_id']}/submit", json={"document_ids": ["doc_a", "doc_b"]}
        )
    ).json()

    assert first["snapshot_id"] != second["snapshot_id"]
    assert (first["snapshot_version"], second["snapshot_version"]) == (1, 2)

    history = (await client.get(f"/applications/{created['application_id']}")).json()
    assert [s["version"] for s in history["snapshots"]] == [1, 2]
    assert history["current_snapshot_id"] == second["snapshot_id"]

    original = (await client.get(f"/cases/{created['case_id']}/snapshot?version=1")).json()
    assert original["snapshot_id"] == first["snapshot_id"]


async def test_a_stored_snapshot_cannot_be_edited(client: AsyncClient) -> None:
    from app.db import session

    created = await create(client)
    submitted = (await client.post(f"/applications/{created['application_id']}/submit", json={})).json()

    async with session() as db:
        with pytest.raises(DBAPIError, match="immutable"):
            await db.execute(
                text("""
                UPDATE app_application.case_snapshot SET hash = 'x'
                WHERE snapshot_id = :s
            """),
                {"s": submitted["snapshot_id"]},
            )


async def test_a_stored_snapshot_cannot_be_deleted(client: AsyncClient) -> None:
    from app.db import session

    created = await create(client)
    submitted = (await client.post(f"/applications/{created['application_id']}/submit", json={})).json()

    async with session() as db:
        with pytest.raises(DBAPIError, match="immutable"):
            await db.execute(
                text("DELETE FROM app_application.case_snapshot WHERE snapshot_id = :s"),
                {"s": submitted["snapshot_id"]},
            )


async def test_submitting_emits_the_event_the_workflow_waits_for(client: AsyncClient) -> None:
    from app.db import session

    created = await create(client)
    submitted = (await client.post(f"/applications/{created['application_id']}/submit", json={})).json()

    async with session() as db:
        row = (
            (
                await db.execute(
                    text("""
            SELECT name, key, case_id, payload, dispatched_at FROM events.outbox
            WHERE name = 'application.submitted'
        """)
                )
            )
            .mappings()
            .one()
        )

    import json

    payload = row["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert row["case_id"] == created["case_id"]
    assert row["key"] == created["case_id"]
    assert payload["snapshot_id"] == submitted["snapshot_id"]
    assert payload["product_code"] == "PF-STD"
    assert row["dispatched_at"] is None, "the dispatcher has not run yet"


async def test_submitting_marks_the_application_submitted(client: AsyncClient) -> None:
    created = await create(client)
    await client.post(f"/applications/{created['application_id']}/submit", json={})
    body = (await client.get(f"/applications/{created['application_id']}")).json()
    assert body["status"] == "SUBMITTED"


async def test_submitting_an_unknown_application_is_not_found(client: AsyncClient) -> None:
    response = await client.post("/applications/app_01JQZK7M8N9P0Q1R2S3T4V5W6Z/submit", json={})
    assert response.status_code == 404


async def test_a_case_without_a_snapshot_reports_not_found(client: AsyncClient) -> None:
    created = await create(client)
    assert (await client.get(f"/cases/{created['case_id']}/snapshot")).status_code == 404


async def test_the_case_view_lists_its_snapshots(client: AsyncClient) -> None:
    created = await create(client)
    await client.post(f"/applications/{created['application_id']}/submit", json={})
    body = (await client.get(f"/cases/{created['case_id']}")).json()
    assert body["case_type"] == "ORIGINATION"
    assert len(body["snapshots"]) == 1
