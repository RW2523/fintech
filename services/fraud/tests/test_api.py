"""T-033 — the fraud endpoints (docs/07 §3, docs/08 §5)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cio_contracts import validate
from tests.conftest import RING


# ---------------------------------------------------------------------------
# assessing
# ---------------------------------------------------------------------------
async def test_a_clean_case_raises_nothing_that_counts(client: AsyncClient) -> None:
    """The service scores its own anomaly, so a clean case can still carry an
    advisory note. What must hold is that nothing counted against the member.
    """
    body = (await client.post("/fraud/assess", json={"case_id": "case_clean", "member_id": RING[0]})).json()
    assert [f for f in body["findings"] if not f["advisory"]] == []
    assert body["level"] == "LOW"
    assert body["integrity_score"] == 100


async def test_the_ring_is_found_and_graded_high(client: AsyncClient) -> None:
    """T-033 acceptance: the seven-member ring is found as a cycle."""
    body = (await client.post("/fraud/assess", json={"case_id": "case_ring", "member_id": RING[0]})).json()

    cycle = [f for f in body["findings"] if f["rule"] == "guarantor_cycle"]
    assert cycle, body["findings"]
    assert cycle[0]["code"] == "INT-05"
    assert cycle[0]["severity"] == "HIGH"
    assert cycle[0]["detail"]["cycle_length"] == 7
    assert set(cycle[0]["members"]) == set(RING)
    assert body["level"] == "HIGH"


async def test_the_duplicate_applicant_is_found(client: AsyncClient) -> None:
    """T-033 acceptance: the duplicate applicant is found."""
    body = (
        await client.post("/fraud/assess", json={"case_id": "case_duplicate", "member_id": RING[0]})
    ).json()
    duplicate = [f for f in body["findings"] if f["rule"] == "duplicate_applicant"]
    assert duplicate and duplicate[0]["severity"] == "HIGH"
    assert body["level"] == "HIGH"


async def test_an_identity_mismatch_is_critical(client: AsyncClient) -> None:
    body = (
        await client.post("/fraud/assess", json={"case_id": "case_identity", "member_id": RING[0]})
    ).json()
    assert body["level"] == "CRITICAL"


async def test_the_integrity_score_falls_with_severity(client: AsyncClient) -> None:
    clean = (await client.post("/fraud/assess", json={"case_id": "case_clean", "member_id": RING[0]})).json()
    ring = (await client.post("/fraud/assess", json={"case_id": "case_ring", "member_id": RING[0]})).json()
    assert ring["integrity_score"] < clean["integrity_score"]


async def test_the_integrity_factor_satisfies_the_contract(client: AsyncClient) -> None:
    body = (await client.post("/fraud/assess", json={"case_id": "case_ring", "member_id": RING[0]})).json()
    validate(body["integrity_factor"], "FactorScore")
    assert body["integrity_factor"]["family"] == "INTEGRITY"
    assert body["integrity_factor"]["tool"] == "fraud.assess"
    assert body["calc_id"] == body["integrity_factor"]["calc_id"]


async def test_an_anomaly_score_is_advisory(client: AsyncClient) -> None:
    body = (
        await client.post(
            "/fraud/assess", json={"case_id": "case_clean", "member_id": RING[0], "anomaly_score": 0.9}
        )
    ).json()
    assert [f["rule"] for f in body["findings"]] == ["anomaly"]
    assert body["findings"][0]["advisory"] is True
    assert body["level"] == "LOW"
    assert body["integrity_score"] == 100


async def test_an_anomaly_score_outside_the_unit_range_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/fraud/assess", json={"case_id": "case_clean", "member_id": RING[0], "anomaly_score": 1.5}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# reproducibility and recording
# ---------------------------------------------------------------------------
async def test_the_same_case_gives_the_same_assessment_id(client: AsyncClient) -> None:
    first = (await client.post("/fraud/assess", json={"case_id": "case_ring", "member_id": RING[0]})).json()
    second = (await client.post("/fraud/assess", json={"case_id": "case_ring", "member_id": RING[0]})).json()
    assert first["assessment_id"] == second["assessment_id"]
    assert first["calc_id"] == second["calc_id"]
    assert first["graph_ref"] == second["graph_ref"]


async def test_reassessing_does_not_write_a_second_row(client: AsyncClient, db: Any) -> None:
    body = (await client.post("/fraud/assess", json={"case_id": "case_ring", "member_id": RING[0]})).json()
    await client.post("/fraud/assess", json={"case_id": "case_ring", "member_id": RING[0]})
    count = (
        await db.execute(
            text("SELECT count(*) FROM app_fraud.assessment WHERE assessment_id = :id"),
            {"id": body["assessment_id"]},
        )
    ).scalar_one()
    assert count == 1


async def test_a_recorded_assessment_cannot_be_edited(client: AsyncClient, db: Any) -> None:
    """A DecisionRecord cites the assessment, so it is frozen once written."""
    body = (await client.post("/fraud/assess", json={"case_id": "case_ring", "member_id": RING[0]})).json()
    with pytest.raises(DBAPIError, match="immutable"):
        await db.execute(
            text("UPDATE app_fraud.assessment SET level = 'LOW' WHERE assessment_id = :id"),
            {"id": body["assessment_id"]},
        )
    await db.rollback()


async def test_the_signals_endpoint_returns_the_findings(client: AsyncClient) -> None:
    body = (await client.post("/fraud/assess", json={"case_id": "case_ring", "member_id": RING[0]})).json()
    signals = (await client.get("/fraud/signals/case_ring")).json()
    assert signals["assessment_id"] == body["assessment_id"]
    assert signals["level"] == "HIGH"
    assert {f["rule"] for f in signals["findings"]} == {f["rule"] for f in body["findings"]}


async def test_an_assessment_can_be_fetched_by_id(client: AsyncClient) -> None:
    body = (await client.post("/fraud/assess", json={"case_id": "case_ring", "member_id": RING[0]})).json()
    found = (await client.get(f"/fraud/assessments/{body['assessment_id']}")).json()
    assert found["case_id"] == "case_ring"
    assert found["integrity_score"] == body["integrity_score"]


# ---------------------------------------------------------------------------
# the graph a person reads
# ---------------------------------------------------------------------------
async def test_the_graph_shows_the_whole_ring(client: AsyncClient) -> None:
    """A cycle finding is unreadable without the rest of the cycle."""
    await client.post("/fraud/assess", json={"case_id": "case_ring", "member_id": RING[0]})
    graph = (await client.get("/fraud/graph/case_ring")).json()
    refs = {n["ref"] for n in graph["nodes"]}
    assert set(RING) <= refs
    assert len(graph["edges"]) >= len(RING)


async def test_the_graph_reference_is_content_addressed(client: AsyncClient) -> None:
    body = (await client.post("/fraud/assess", json={"case_id": "case_ring", "member_id": RING[0]})).json()
    graph = (await client.get("/fraud/graph/case_ring")).json()
    assert graph["graph_ref"] == body["graph_ref"]


async def test_a_clean_case_still_has_a_graph(client: AsyncClient) -> None:
    await client.post("/fraud/assess", json={"case_id": "case_clean", "member_id": RING[0]})
    graph = (await client.get("/fraud/graph/case_clean")).json()
    assert graph["case_id"] == "case_clean"


# ---------------------------------------------------------------------------
# failure paths
# ---------------------------------------------------------------------------
async def test_an_unknown_case_is_not_found(client: AsyncClient) -> None:
    response = await client.post("/fraud/assess", json={"case_id": "case_missing", "member_id": RING[0]})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_signals_for_an_unassessed_case_are_not_found(client: AsyncClient) -> None:
    assert (await client.get("/fraud/signals/case_never")).status_code == 404


async def test_a_graph_for_an_unassessed_case_is_not_found(client: AsyncClient) -> None:
    assert (await client.get("/fraud/graph/case_never")).status_code == 404


async def test_a_malformed_member_id_is_refused(client: AsyncClient) -> None:
    response = await client.post("/fraud/assess", json={"case_id": "case_clean", "member_id": "nope"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------
async def test_version_names_the_rules_in_force(client: AsyncClient) -> None:
    body = (await client.get("/version")).json()
    assert body["version"].startswith("fraud-rules/")
    assert "guarantor_cycle" in body["rules"]
    assert body["thresholds"]["velocity"]["applications"] == 3


async def test_version_publishes_its_thresholds(client: AsyncClient) -> None:
    """An officer should be able to see the bar without reading the code."""
    thresholds = (await client.get("/version")).json()["thresholds"]
    assert set(thresholds) >= {"velocity", "contact_change_days", "anomaly_score", "guarantee_hops"}
