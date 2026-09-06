"""T-044 — the committee state machine (docs/06 §8)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.clients import UpstreamError
from app.orchestrate import COUNCIL


async def start(client: AsyncClient, snapshot: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body = {"snapshot": snapshot, "policy_result": POLICY_RESULT, **overrides}
    return (await client.post("/committee/runs", json=body)).json()


#: What `/policy/evaluate` actually returns. The tests used to send only
#: `blockers`, which the faked synthesizer accepted and the real one does
#: not: it reads `rules` as its first step and a run submitted without them
#: failed with a KeyError five services deep.
POLICY_RESULT = {"blockers": [], "rules": [], "evidence_coverage": 1.0}


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------
async def test_a_run_passes_through_the_documented_states(client: AsyncClient, snapshot: Any) -> None:
    body = await start(client, snapshot, top_band=True)
    assert body["state"] == "DONE"
    assert body["rounds"] == ["ASSESS", "CHALLENGE", "SYNTHESIZE", "NARRATE"]


async def test_the_council_is_invoked_in_parallel(client: AsyncClient, snapshot: Any, fake: Any) -> None:
    await start(client, snapshot, top_band=True)
    invoked = [c[1]["agent_id"] for c in fake.calls if c[0] == "invoke"]
    assert set(COUNCIL) <= set(invoked)
    assert "challenger" in invoked


async def test_the_challenger_sees_every_assess_opinion(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """Its duty is to test what the others said, so it needs what they said."""
    await start(client, snapshot, top_band=True)
    call = next(c[1] for c in fake.calls if c[0] == "invoke" and c[1]["agent_id"] == "challenger")
    assert len(call["prior_opinions"]) == len(COUNCIL)
    assert call["round"] == "CHALLENGE"


async def test_a_fast_case_skips_deliberation(client: AsyncClient, snapshot: Any, fake: Any) -> None:
    """docs/06 §8 — Tier 0 runs policy and the models, and no agents.

    A committee that adds nothing to a clean small case costs time nobody has.
    """
    body = await start(client, snapshot)
    assert body["tier"] == "FAST"
    assert body["rounds"] == ["SYNTHESIZE", "NARRATE"]
    assert not [c for c in fake.calls if c[0] == "invoke"]


async def test_the_factor_scores_reach_the_synthesizer(client: AsyncClient, snapshot: Any, fake: Any) -> None:
    await start(client, snapshot, top_band=True)
    call = next(c[1] for c in fake.calls if c[0] == "synthesize")
    families = {f["family"] for f in call["factor_scores"]}
    assert {"CAPACITY", "CONDUCT", "INTEGRITY", "COMMITMENT"} <= families


# ---------------------------------------------------------------------------
# failure, and the rule that the decision still happens
# ---------------------------------------------------------------------------
async def test_an_agent_that_fails_leaves_a_degraded_opinion(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """A gap in the record looks like an agent with no concerns."""
    fake.opinions["credit_risk"] = UpstreamError("agent runtime down")
    body = await start(client, snapshot, top_band=True)
    assert body["state"] == "DONE"
    assert "credit_risk" in body["degraded_agents"]


async def test_a_degraded_agent_contributes_no_factor_score(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """A missing factor is missing; it is not a zero."""
    fake.opinions["credit_risk"] = UpstreamError("down")
    await start(client, snapshot, top_band=True)
    call = next(c[1] for c in fake.calls if c[0] == "synthesize")
    assert "CONDUCT" not in {f["family"] for f in call["factor_scores"]}


async def test_the_synthesizer_runs_even_when_every_agent_failed(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """The deliberation may fail; the decision may not."""
    for agent in (*COUNCIL, "challenger"):
        fake.opinions[agent] = UpstreamError("down")
    body = await start(client, snapshot, top_band=True)
    assert body["state"] == "DONE"
    assert body["decision_record"]["recommendation"]
    assert len(body["degraded_agents"]) == len(COUNCIL) + 1


async def test_a_failed_synthesis_fails_the_run_rather_than_guessing(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    fake.synthesis = UpstreamError("policy service down")
    body = await start(client, snapshot, top_band=True)
    assert body["state"] == "FAILED"
    assert "synthesizer" in body["detail"]


async def test_a_narrator_that_cannot_be_reached_leaves_a_template(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """docs/06 §8 — a plain narrative beats a missing one, and both beat a
    wrong one."""
    fake.narration = UpstreamError("gateway down")
    body = await start(client, snapshot, top_band=True)
    narrative = body["decision_record"]["narrative"]
    assert narrative["status"] == "DEGRADED"
    assert narrative["member"] and narrative["officer"] and narrative["auditor"]


async def test_a_degraded_narrative_says_only_what_the_record_says(
    client: AsyncClient, snapshot: Any, fake: Any, record: Any
) -> None:
    fake.narration = UpstreamError("down")
    body = await start(client, snapshot, top_band=True)
    auditor = body["decision_record"]["narrative"]["auditor"]
    assert record["policy_version"] in auditor
    assert record["route"] in body["decision_record"]["narrative"]["officer"]


@pytest.mark.slow
async def test_a_run_that_exceeds_its_budget_still_produces_a_record(
    client: AsyncClient, snapshot: Any, fake: Any, monkeypatch: Any
) -> None:
    """docs/06 §8 — budget exceeded goes to SYNTHESIZE(partial), not to nothing."""
    from app import tiers

    monkeypatch.setitem(tiers.BUDGETS, "EXTENDED", {"seconds": 0, "tokens": 1000})
    body = await start(client, snapshot, top_band=True)
    assert body["tier"] == "EXTENDED"
    assert body["state"] == "DONE"
    assert body["timed_out"] is True
    assert body["decision_record"]["recommendation"]


async def test_a_timed_out_run_tells_the_synthesizer_the_model_is_unhealthy(
    client: AsyncClient, snapshot: Any, fake: Any, monkeypatch: Any
) -> None:
    """A partial deliberation must not be weighted as a complete one."""
    from app import tiers

    monkeypatch.setitem(tiers.BUDGETS, "EXTENDED", {"seconds": 0, "tokens": 1000})
    await start(client, snapshot, top_band=True)
    call = next(c[1] for c in fake.calls if c[0] == "synthesize")
    assert call["model_health"] == "RED"


# ---------------------------------------------------------------------------
# idempotency and persistence
# ---------------------------------------------------------------------------
async def test_a_second_submission_joins_the_run_that_exists(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """docs/06 §8 — the inputs are frozen, so a second deliberation would be a
    second answer to a settled question."""
    first = await start(client, snapshot, top_band=True)
    calls = len([c for c in fake.calls if c[0] == "invoke"])

    response = await client.post(
        "/committee/runs", json={"snapshot": snapshot, "policy_result": POLICY_RESULT, "top_band": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["idempotent"] is True
    assert body["run_id"] == first["run_id"]
    assert len([c for c in fake.calls if c[0] == "invoke"]) == calls


async def test_the_run_and_its_opinions_are_stored(client: AsyncClient, snapshot: Any) -> None:
    body = await start(client, snapshot, top_band=True)
    stored = (await client.get(f"/committee/runs/{body['run_id']}")).json()
    assert stored["state"] == "DONE"
    assert len(stored["opinions"]) == len(COUNCIL) + 1
    assert stored["decision_record_id"]


async def test_an_opinion_cannot_be_edited(client: AsyncClient, snapshot: Any, db: Any) -> None:
    """A DecisionRecord cites the opinions, so they are frozen once written."""
    body = await start(client, snapshot, top_band=True)
    with pytest.raises(DBAPIError, match="immutable"):
        await db.execute(
            text("UPDATE app_committee.opinion SET stance = 'OPPOSE' WHERE run_id = :run_id"),
            {"run_id": body["run_id"]},
        )
    await db.rollback()


async def test_the_opinions_endpoint_lists_them(client: AsyncClient, snapshot: Any) -> None:
    body = await start(client, snapshot, top_band=True)
    listed = (await client.get(f"/committee/runs/{body['run_id']}/opinions")).json()
    assert listed["count"] == len(COUNCIL) + 1
    assert {o["agent_id"] for o in listed["opinions"]} >= set(COUNCIL)


async def test_an_unknown_run_is_not_found(client: AsyncClient) -> None:
    assert (await client.get("/committee/runs/run_nope")).status_code == 404


async def test_a_snapshot_without_an_id_is_refused(client: AsyncClient) -> None:
    response = await client.post("/committee/runs", json={"snapshot": {"product_code": "PF-STD"}})
    assert response.status_code == 422


async def test_an_unknown_product_is_refused(client: AsyncClient, snapshot: Any) -> None:
    response = await client.post(
        "/committee/runs", json={"snapshot": {**snapshot, "product_code": "PF-WIZARDRY"}}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# reproducibility
# ---------------------------------------------------------------------------
async def test_the_same_snapshot_gives_the_same_opinions(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """T-044 acceptance: a run is reproducible from its snapshot."""
    first = await start(client, snapshot, top_band=True)
    stored = (await client.get(f"/committee/runs/{first['run_id']}")).json()
    stances = {o["agent_id"]: o["stance"] for o in stored["opinions"]}

    second = await client.post(
        "/committee/runs", json={"snapshot": snapshot, "policy_result": POLICY_RESULT, "top_band": True}
    )
    again = (await client.get(f"/committee/runs/{second.json()['run_id']}")).json()
    assert {o["agent_id"]: o["stance"] for o in again["opinions"]} == stances
