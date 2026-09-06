"""T-060 — materialising temporal features (docs/07 §4.2).

The materialiser reads a member's behaviour out of the core and reduces it. The
tests build that behaviour a payment at a time, so what the numbers mean can be
checked against a series somebody can read.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text

from tests.conftest import AS_OF, EMPLOYER, MEMBER, give_history


async def run(client: AsyncClient, **body: Any) -> dict[str, Any]:
    return (await client.post("/lmi/materialise", json={"as_of": AS_OF.isoformat(), **body})).json()


async def features_of(client: AsyncClient, member_id: str = MEMBER) -> dict[str, Any]:
    response = await client.get(f"/lmi/features/{member_id}", params={"as_of": AS_OF.isoformat()})
    return dict(response.json())


# ---------------------------------------------------------------------------
# a member's history becomes a feature set
# ---------------------------------------------------------------------------
async def test_a_steady_payer_has_no_departure(client: AsyncClient, db: Any) -> None:
    await give_history(db, timings=[1] * 18)
    await run(client)

    body = await features_of(client)
    assert body["due_events"] == 18
    assert body["features"]["days_to_pay_median_365d"] == 1.0
    assert abs(body["features"]["days_to_pay_median_30d_robust_z"]) < 1.0


async def test_a_member_who_slipped_shows_a_departure(client: AsyncClient, db: Any) -> None:
    """The point of the whole engine: not that they are late, but that they are
    later than they have ever been."""
    await give_history(db, timings=[0] * 15 + [7, 9, 11])
    await run(client)

    body = await features_of(client)
    assert body["features"]["days_to_pay_median_30d_robust_z"] > 2.0
    assert body["features"]["due_to_pay_slope_180d"] > 0


async def test_a_habitually_late_payer_is_not_flagged_for_being_late(client: AsyncClient, db: Any) -> None:
    """A member who has always paid five days on has not changed when they pay
    five days on, and an engine that says otherwise is measuring lateness
    rather than change."""
    await give_history(db, timings=[5] * 18)
    await run(client)

    body = await features_of(client)
    assert body["features"]["late_streak"] == 18, "they are late, and that is true"
    assert body["features"]["streak_beyond_habit"] == 0, "but nothing has changed"
    assert abs(body["features"]["days_to_pay_median_30d_robust_z"]) < 1.0


async def test_an_early_payer_paying_on_the_day_is_beyond_habit_but_not_late(
    client: AsyncClient, db: Any
) -> None:
    await give_history(db, timings=[-3] * 15 + [0, 0, 0])
    await run(client)

    body = await features_of(client)
    assert body["features"]["late_streak"] == 0
    assert body["features"]["streak_beyond_habit"] == 3


async def test_an_unpaid_instalment_is_counted_separately(client: AsyncClient, db: Any) -> None:
    await give_history(db, timings=[0] * 10 + [None, None])
    await run(client)

    body = await features_of(client)
    assert body["features"]["unpaid_365d"] == 2


# ---------------------------------------------------------------------------
# the other families
# ---------------------------------------------------------------------------
async def test_missed_deductions_are_read_from_the_core(client: AsyncClient, db: Any) -> None:
    await give_history(db, timings=[0] * 12, deductions=[250.0] * 10 + [0.0, 0.0])
    await run(client)

    body = await features_of(client)
    assert body["features"]["deduction_missed_count_90d"] >= 1


async def test_a_paused_savings_balance_is_visible(client: AsyncClient, db: Any) -> None:
    await give_history(db, timings=[0] * 12, savings=[100.0 * i for i in range(1, 9)] + [800.0] * 4)
    await run(client)

    body = await features_of(client)
    assert body["features"]["savings_paused_months"] >= 3


async def test_an_employer_wide_gap_is_not_the_members_fault(client: AsyncClient, db: Any) -> None:
    """Treating it as one raises an alert on everybody at that employer at
    once, which is the fastest way to make the platform useless."""
    for index in range(4):
        await give_history(
            db,
            member_id=f"M-EMP{index:03d}",
            timings=[0] * 12,
            deductions=[250.0] * 10 + [0.0, 0.0],
        )
    await run(client)

    body = await features_of(client, "M-EMP000")
    assert body["features"]["employer_gap_flag"] == 1.0


# ---------------------------------------------------------------------------
# what the run itself reports
# ---------------------------------------------------------------------------
async def test_a_member_with_no_history_is_skipped_not_stored_empty(client: AsyncClient, db: Any) -> None:
    """Storing an empty feature set would let a member with no facility look
    identical to one whose behaviour was measured and found unremarkable."""
    await db.execute(
        text(
            "INSERT INTO core.employer (employer_id, name, sector) "
            "VALUES (:e, 'Test Employer', 'PUBLIC_ADMIN') ON CONFLICT DO NOTHING"
        ),
        {"e": EMPLOYER},
    )
    await db.execute(
        text("""INSERT INTO core.member (member_id, name_token, joined_at, employer_id)
                VALUES ('M-EMPTY', '«M-EMPTY»', '2024-01-01', :e)"""),
        {"e": EMPLOYER},
    )
    await db.commit()

    body = await run(client)
    assert body["computed"] == 0
    assert (await client.get("/lmi/features/M-EMPTY")).status_code == 404


async def test_a_run_records_what_it_covered(client: AsyncClient, db: Any) -> None:
    """A run that covered half the book is visible as such rather than as a
    quiet night."""
    await give_history(db, timings=[0] * 12)
    await run(client)

    runs = (await client.get("/lmi/runs")).json()
    assert runs["count"] == 1
    assert runs["runs"][0]["computed"] == 1
    assert runs["runs"][0]["seconds"] >= 0


async def test_recomputing_a_day_replaces_it(client: AsyncClient, db: Any) -> None:
    await give_history(db, timings=[0] * 12)
    await run(client)
    await run(client)

    stored = (
        await db.execute(
            text("SELECT count(*) FROM app_lmi.temporal_features WHERE member_id = :m"),
            {"m": MEMBER},
        )
    ).scalar_one()
    assert stored == 1


async def test_a_scoped_run_touches_only_those_members(client: AsyncClient, db: Any) -> None:
    await give_history(db, timings=[0] * 12)
    await give_history(db, member_id="M-OTHER", timings=[0] * 12)

    body = await run(client, member_ids=[MEMBER])
    assert body["members"] == 1
    assert (await client.get("/lmi/features/M-OTHER")).status_code == 404


# ---------------------------------------------------------------------------
# reading them back
# ---------------------------------------------------------------------------
async def test_features_are_returned_from_at_or_before_the_asked_day(client: AsyncClient, db: Any) -> None:
    """A member whose features were last computed on Tuesday has features on
    Wednesday, and refusing to answer would make every reader implement this
    fallback themselves."""
    await give_history(db, timings=[0] * 12)
    await run(client)

    later = (AS_OF + timedelta(days=3)).isoformat()
    response = await client.get(f"/lmi/features/{MEMBER}", params={"as_of": later})
    assert response.status_code == 200
    assert response.json()["stale_days"] == 3


async def test_a_member_nobody_computed_is_a_not_found(client: AsyncClient) -> None:
    assert (await client.get("/lmi/features/M-999999")).status_code == 404


async def test_the_seasonal_block_says_why_it_did_not_adjust(client: AsyncClient, db: Any) -> None:
    """Adjusting a short series invents a season out of noise and subtracts it,
    so a series too short to adjust says so rather than looking adjusted."""
    await give_history(db, timings=[0] * 12)
    await run(client)

    body = await features_of(client)
    assert body["seasonal"]["adjusted"] is False
    assert "STL needs" in body["seasonal"]["reason"]
