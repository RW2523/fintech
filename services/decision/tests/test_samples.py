"""T-051 — the sampling queue (docs/05 §6, docs/08 §6).

A share of what the platform decides alone is read by a person afterwards. The
point is not to catch a bad decision, though it might: it is to keep a human
eye on what autonomy is doing, at a rate the institution set.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import text

from tests.test_api import RECORD_ID, record


async def autonomous(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    body = {**record(), "route": "AUTONOMOUS", "sampled": True, **overrides}
    response = await client.post(
        "/recommendations",
        json={"decision_record": body, "case_id": body.get("case_id"), "sampling": {"sla_hours": 24}},
    )
    return dict(response.json())


async def test_a_sampled_autonomous_decision_joins_the_queue(client: AsyncClient) -> None:
    appended = await autonomous(client)
    assert appended["sample_id"]

    queued = (await client.get("/samples")).json()
    assert appended["sample_id"] in [s["sample_id"] for s in queued["samples"]]


async def test_an_unsampled_autonomous_decision_does_not(client: AsyncClient) -> None:
    """The rate is the rate. Queuing every autonomous decision would make the
    sample meaningless and the queue unreadable."""
    appended = await autonomous(client, sampled=False)
    assert appended["sample_id"] is None


async def test_a_decision_a_person_made_is_not_sampled(client: AsyncClient) -> None:
    """It has been reviewed by definition, and queuing it would dilute the
    sample with cases nobody needs to look at again."""
    appended = await autonomous(client, route="OFFICER_REVIEW")
    assert appended["sample_id"] is None


async def test_the_reviewing_role_comes_from_the_policy_pack(client: AsyncClient) -> None:
    body = {**record(), "route": "AUTONOMOUS", "sampled": True}
    await client.post(
        "/recommendations",
        json={
            "decision_record": body,
            "sampling": {"reviewer_role": "HEAD_OF_CREDIT", "sla_hours": 4},
        },
    )
    queued = (await client.get("/samples", params={"role": "HEAD_OF_CREDIT"})).json()
    assert queued["count"] == 1


async def test_a_verdict_is_recorded_and_joins_the_chain(client: AsyncClient) -> None:
    appended = await autonomous(client)
    response = await client.post(
        f"/samples/{appended['sample_id']}/review",
        json={"reviewer_id": "u-senior", "verdict": "AGREE", "notes": "affordability checks out"},
    )
    assert response.status_code == 200
    assert response.json()["entry_id"], "the verdict is part of the decision's history"

    open_queue = (await client.get("/samples")).json()
    assert appended["sample_id"] not in [s["sample_id"] for s in open_queue["samples"]]

    reviewed = (await client.get("/samples", params={"reviewed": True})).json()
    assert appended["sample_id"] in [s["sample_id"] for s in reviewed["samples"]]


async def test_a_verdict_is_written_once(client: AsyncClient) -> None:
    """Reviewing again would let a disagreement be quietly replaced by an
    agreement."""
    appended = await autonomous(client)
    first = {"reviewer_id": "u-senior", "verdict": "DISAGREE", "notes": "income is thin"}
    assert (await client.post(f"/samples/{appended['sample_id']}/review", json=first)).status_code == 200

    again = {"reviewer_id": "u-other", "verdict": "AGREE", "notes": "looks fine to me"}
    response = await client.post(f"/samples/{appended['sample_id']}/review", json=again)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["verdict"] == "DISAGREE"


async def test_reviewing_an_unknown_sample_is_a_not_found(client: AsyncClient) -> None:
    response = await client.post(
        "/samples/smp_01ARZ3NDEKTSV4RRFFQ69G5FAW/review",
        json={"reviewer_id": "u-1", "verdict": "AGREE"},
    )
    assert response.status_code == 404


async def test_replaying_the_same_decision_queues_one_review(client: AsyncClient) -> None:
    first = await autonomous(client)
    second = await autonomous(client)
    assert first["sample_id"] == second["sample_id"]

    queued = (await client.get("/samples")).json()
    assert [s["sample_id"] for s in queued["samples"]].count(first["sample_id"]) == 1


async def test_the_queue_shows_what_is_overdue(client: AsyncClient, db: Any) -> None:
    """Whether it is late is what decides what the reviewer does next, so the
    queue says so rather than leaving them to read a timestamp."""
    appended = await autonomous(client)
    await db.execute(
        text("UPDATE ledger.sample_review SET due_at = now() - interval '1 hour' WHERE sample_id = :id"),
        {"id": appended["sample_id"]},
    )
    await db.commit()

    queued = (await client.get("/samples")).json()
    late = next(s for s in queued["samples"] if s["sample_id"] == appended["sample_id"])
    assert late["overdue"] is True


async def test_the_queue_puts_the_nearest_deadline_first(client: AsyncClient, db: Any) -> None:
    urgent = await autonomous(client, decision_record_id=RECORD_ID)
    await db.execute(
        text("UPDATE ledger.sample_review SET due_at = now() + interval '1 hour' WHERE sample_id = :id"),
        {"id": urgent["sample_id"]},
    )
    await db.commit()

    queued = (await client.get("/samples")).json()
    assert queued["samples"][0]["sample_id"] == urgent["sample_id"]


# ---------------------------------------------------------------------------
# who may review
# ---------------------------------------------------------------------------
async def test_the_assigned_role_may_review(client: AsyncClient) -> None:
    """The queue names an authority and the header carries a sign-in role.
    Comparing them without mapping rejects the very person it was assigned
    to."""
    appended = await autonomous(client)
    response = await client.post(
        f"/samples/{appended['sample_id']}/review",
        json={"reviewer_id": "u-senior", "verdict": "AGREE"},
        headers={"x-principal-role": "senior_officer"},
    )
    assert response.status_code == 200


async def test_a_dial_owner_may_read_any_sample(client: AsyncClient) -> None:
    """The people accountable for the autonomy programme are the people who
    may look at what it did. The approval ladder is deliberately not used
    here: it orders who may approve how much financing, which is a different
    question from who may read a sample."""
    appended = await autonomous(client)
    response = await client.post(
        f"/samples/{appended['sample_id']}/review",
        json={"reviewer_id": "u-head", "verdict": "AGREE"},
        headers={"x-principal-role": "head_of_credit"},
    )
    assert response.status_code == 200


async def test_an_officer_may_not_review_a_senior_officers_sample(client: AsyncClient) -> None:
    appended = await autonomous(client)
    response = await client.post(
        f"/samples/{appended['sample_id']}/review",
        json={"reviewer_id": "u-1", "verdict": "AGREE"},
        headers={"x-principal-role": "officer"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["details"]["assigned_role"] == "SENIOR_OFFICER"
