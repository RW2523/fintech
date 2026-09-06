"""T-032 — `POST /risk/score` (docs/07 §2.3)."""

from __future__ import annotations

import time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

#: docs/00 T-032 acceptance.
LATENCY_BUDGET_MS = 200.0


# ---------------------------------------------------------------------------
# the contract
# ---------------------------------------------------------------------------
async def test_the_response_carries_every_field_the_contract_names(
    client: AsyncClient, snapshot: Any
) -> None:
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()

    assert set(body) >= {
        "model_run_id",
        "champion",
        "challenger",
        "conduct_score",
        "conduct_calc_id",
        "reason_codes",
        "drivers",
        "evidence_refs",
    }
    for side in ("champion", "challenger"):
        assert set(body[side]) >= {"model", "version", "pd_12m", "grade", "calibration"}
    assert "ood_score" in body["champion"]


async def test_the_probability_and_grade_agree(client: AsyncClient, snapshot: Any) -> None:
    from ml.credit_risk.calibration import grade_of

    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    assert body["champion"]["grade"] == grade_of(body["champion"]["pd_12m"])
    assert 0.0 <= body["champion"]["pd_12m"] <= 1.0


async def test_a_troubled_member_scores_worse(client: AsyncClient, snapshot: Any, troubled: Any) -> None:
    good = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    bad = (await client.post("/risk/score", json={"snapshot_id": troubled.snapshot_id})).json()
    assert bad["champion"]["pd_12m"] > good["champion"]["pd_12m"]


async def test_the_conduct_score_is_a_recorded_calculation(client: AsyncClient, snapshot: Any) -> None:
    """A number a decision rests on carries the arithmetic that produced it."""
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    assert 0 <= body["conduct_score"] <= 100
    assert body["conduct_calc_id"].startswith("calc_")
    factor = body["conduct_factor"]
    assert factor["family"] == "CONDUCT"
    assert factor["tool"] == "risk.score"
    assert factor["inputs_digest"]


async def test_reason_codes_come_from_the_approved_vocabulary(client: AsyncClient, troubled: Any) -> None:
    import yaml

    from ml.credit_risk.explain import reason_map

    approved = {c for entry in reason_map().values() for c in entry.values()}
    body = (await client.post("/risk/score", json={"snapshot_id": troubled.snapshot_id})).json()
    assert set(body["reason_codes"]) <= approved
    assert yaml is not None


async def test_evidence_is_minted_for_what_the_score_rested_on(client: AsyncClient, snapshot: Any) -> None:
    """A claim without evidence is an opinion (docs/01 §2)."""
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    refs = body["evidence_refs"]
    assert refs
    cited = {r["locator"]["field_path"] for r in refs}
    assert {d["feature"] for d in body["drivers"]} <= cited
    for ref in refs:
        assert ref["evidence_id"].startswith("ev_")
        assert ref["source_record_id"] == snapshot.snapshot_id
        assert ref["source_system"]


async def test_evidence_does_not_claim_features_the_model_never_read(
    client: AsyncClient, snapshot: Any
) -> None:
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    cited = {r["locator"]["field_path"] for r in body["evidence_refs"]}
    assert len(cited) < len(snapshot.values)


# ---------------------------------------------------------------------------
# reproducibility
# ---------------------------------------------------------------------------
async def test_the_same_snapshot_gives_the_same_run_id(client: AsyncClient, snapshot: Any) -> None:
    """T-032 acceptance: reproducible run ids."""
    first = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    second = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    assert first["model_run_id"] == second["model_run_id"]
    assert first["champion"]["pd_12m"] == second["champion"]["pd_12m"]
    assert first["conduct_calc_id"] == second["conduct_calc_id"]


async def test_different_snapshots_give_different_run_ids(
    client: AsyncClient, snapshot: Any, troubled: Any
) -> None:
    first = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    second = (await client.post("/risk/score", json={"snapshot_id": troubled.snapshot_id})).json()
    assert first["model_run_id"] != second["model_run_id"]


async def test_rescoring_does_not_write_a_second_run(client: AsyncClient, snapshot: Any, db: Any) -> None:
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})
    count = (
        await db.execute(
            text("SELECT count(*) FROM app_risk.model_run WHERE model_run_id = :id"),
            {"id": body["model_run_id"]},
        )
    ).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# latency
# ---------------------------------------------------------------------------
@pytest.mark.slow
async def test_scoring_stays_inside_the_latency_budget(client: AsyncClient, snapshot: Any) -> None:
    """T-032 acceptance: under 200 ms per call.

    The first call loads the artifacts, so it is excluded: what matters is the
    cost of a score once the service is warm, which is how it will be called.
    """
    await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})

    timings: list[float] = []
    for _ in range(25):
        started = time.perf_counter()
        response = await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})
        timings.append((time.perf_counter() - started) * 1000.0)
        assert response.status_code == 200

    timings.sort()
    median = timings[len(timings) // 2]
    worst = timings[-1]
    assert median < LATENCY_BUDGET_MS, f"median {median:.1f} ms"
    assert worst < LATENCY_BUDGET_MS * 2, f"worst {worst:.1f} ms"


async def test_the_response_reports_the_model_time(client: AsyncClient, snapshot: Any) -> None:
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    assert 0.0 < body["latency_ms"] < LATENCY_BUDGET_MS


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------
async def test_the_run_is_recorded_and_retrievable(client: AsyncClient, snapshot: Any) -> None:
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    run = (await client.get(f"/risk/runs/{body['model_run_id']}")).json()

    assert run["snapshot_id"] == snapshot.snapshot_id
    assert run["member_id"] == snapshot.member_id
    assert run["champion_grade"] == body["champion"]["grade"]
    assert run["reason_codes"] == body["reason_codes"]
    assert run["model_version"] == body["version"]


async def test_a_recorded_run_cannot_be_edited(client: AsyncClient, snapshot: Any, db: Any) -> None:
    """A DecisionRecord cites this run, so it is frozen once written."""
    body = (await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})).json()
    with pytest.raises(DBAPIError, match="immutable"):
        await db.execute(
            text("UPDATE app_risk.model_run SET champion_pd = 0 WHERE model_run_id = :id"),
            {"id": body["model_run_id"]},
        )
    await db.rollback()


async def test_runs_for_a_member_are_listed_newest_first(client: AsyncClient, snapshot: Any) -> None:
    await client.post("/risk/score", json={"snapshot_id": snapshot.snapshot_id})
    body = (await client.get(f"/risk/members/{snapshot.member_id}/runs")).json()
    assert body["count"] >= 1
    assert all(r["member_id"] == snapshot.member_id for r in body["runs"])


async def test_an_unknown_run_is_not_found(client: AsyncClient) -> None:
    assert (await client.get("/risk/runs/mr_NOPE")).status_code == 404


# ---------------------------------------------------------------------------
# failure paths
# ---------------------------------------------------------------------------
async def test_an_unknown_snapshot_is_not_found(client: AsyncClient) -> None:
    response = await client.post("/risk/score", json={"snapshot_id": "fs_missing"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_a_malformed_request_is_refused(client: AsyncClient) -> None:
    assert (await client.post("/risk/score", json={})).status_code == 422
    assert (await client.post("/risk/score", json={"snapshot_id": "x", "extra": 1})).status_code == 422


async def test_an_unreachable_feature_service_fails_closed(db: Any, trained: None) -> None:
    """No inputs means no score. Inventing one would be worse than stopping."""
    from app.main import app
    from app.routes import set_snapshot_source
    from app.snapshots import HttpSnapshotSource

    set_snapshot_source(HttpSnapshotSource("http://127.0.0.1:9"))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://risk") as http:
            response = await http.post("/risk/score", json={"snapshot_id": "fs_any"})
        assert response.status_code == 500
        assert "feature service unreachable" in response.json()["error"]["message"]
    finally:
        set_snapshot_source(None)


async def test_an_unknown_model_version_is_refused(client: AsyncClient, snapshot: Any) -> None:
    response = await client.post(
        "/risk/score", json={"snapshot_id": snapshot.snapshot_id, "model_version": "1999.01.1"}
    )
    assert response.status_code >= 400
