"""T-032 — the risk response against the published contracts (docs/03)."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from cio_contracts import validate


async def test_every_evidence_ref_satisfies_the_contract(client: AsyncClient, snapshot: Any) -> None:
    """An agent may only cite evidence a tool minted, so the shape is fixed."""
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    assert body["evidence_refs"]
    for ref in body["evidence_refs"]:
        validate(ref, "EvidenceRef")


async def test_the_conduct_factor_satisfies_the_contract(client: AsyncClient, snapshot: Any) -> None:
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    validate(body["conduct_factor"], "FactorScore")


async def test_the_conduct_factor_cites_the_evidence_the_score_used(
    client: AsyncClient, snapshot: Any
) -> None:
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    minted = {ref["evidence_id"] for ref in body["evidence_refs"]}
    assert set(body["conduct_factor"]["evidence_refs"]) == minted


async def test_evidence_ids_are_derived_from_the_snapshot(client: AsyncClient, snapshot: Any) -> None:
    """Re-scoring cites the same evidence rather than a parallel set."""
    first = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    second = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    assert [r["evidence_id"] for r in first["evidence_refs"]] == [
        r["evidence_id"] for r in second["evidence_refs"]
    ]


async def test_evidence_names_the_snapshot_it_came_from(client: AsyncClient, snapshot: Any) -> None:
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    for ref in body["evidence_refs"]:
        assert ref["source_record_id"] == snapshot.snapshot_id
        assert ref["locator"]["field_path"] in snapshot.values
        assert ref["permitted_uses"] == ["UNDERWRITING"]


async def test_evidence_is_not_marked_uncertain(client: AsyncClient, snapshot: Any) -> None:
    """A feature snapshot is a deterministic computation over stored records.

    The uncertainty a decision weighs belongs in the model's probability, not
    in a hedge on the reference to the input.
    """
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    assert all(ref["confidence"] == 1.0 for ref in body["evidence_refs"])
