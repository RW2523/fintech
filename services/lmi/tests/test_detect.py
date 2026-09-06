"""T-061 — detection over a member's real history (docs/07 §4.3).

The unit tests for CUSUM live beside it. These are about the service around it:
which signals it reads, what it emits, and what it does when a member has
nothing to look at.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text

from tests.conftest import AS_OF, MEMBER, give_history


async def detect(client: AsyncClient, **body: Any) -> dict[str, Any]:
    return (await client.post("/lmi/detect", json={"as_of": AS_OF.isoformat(), **body})).json()


# ---------------------------------------------------------------------------
# what it sees
# ---------------------------------------------------------------------------
async def test_a_steady_payer_produces_no_alarm(client: AsyncClient, db: Any) -> None:
    await give_history(db, timings=[0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1])
    body = await detect(client)
    assert body["alarms"] == 0


async def test_a_drifting_payer_is_seen(client: AsyncClient, db: Any) -> None:
    """The engine's whole purpose: each step is small and together they are
    unmistakable."""
    await give_history(db, timings=[0] * 10 + [3, 5, 7, 9, 11, 13, 15, 17])
    body = await detect(client)

    timing = [a for a in body["detections"] if a["signal"] == "days_to_pay"]
    assert timing, "a clear drift went undetected"
    assert timing[0]["member_id"] == MEMBER


async def test_the_change_point_precedes_the_detection(client: AsyncClient, db: Any) -> None:
    """An officer told the case changed the day the sum crossed a line would
    look at the wrong payslip."""
    await give_history(db, timings=[0] * 10 + [3, 5, 7, 9, 11, 13, 15, 17])
    body = await detect(client)

    alarm = next(a for a in body["detections"] if a["signal"] == "days_to_pay")
    assert alarm["cp_date"] < alarm["detected_at"]
    assert alarm["stats"]["detection_lag_days"] > 0


async def test_a_stopped_deduction_is_seen(client: AsyncClient, db: Any) -> None:
    """The earliest signal in a payroll-deduction book: the member does not
    choose to stop, their employer does."""
    await give_history(db, timings=[0] * 18, deductions=[250.0] * 12 + [0.0] * 6)
    body = await detect(client)

    assert [a for a in body["detections"] if a["signal"] == "deduction_received_ratio"]


async def test_a_short_history_is_not_judged(client: AsyncClient, db: Any) -> None:
    """A member with four payments has not been shown to be steady; they have
    not been looked at."""
    await give_history(db, timings=[0, 5, 10, 15])
    body = await detect(client)
    assert body["alarms"] == 0


# ---------------------------------------------------------------------------
# what it emits
# ---------------------------------------------------------------------------
async def test_a_change_becomes_an_event(client: AsyncClient, db: Any) -> None:
    await give_history(db, timings=[0] * 10 + [3, 5, 7, 9, 11, 13, 15, 17])
    await detect(client)

    row = (
        (
            await db.execute(
                text("""
        SELECT name, payload FROM events.outbox
         WHERE name = 'behaviour.change_point_detected' ORDER BY created_at DESC LIMIT 1
    """)
            )
        )
        .mappings()
        .first()
    )
    assert row is not None
    assert row["payload"]["member_id"] == MEMBER
    assert row["payload"]["signal"]
    assert row["payload"]["stats"]["threshold"] > 0


async def test_an_old_change_is_not_re_announced(client: AsyncClient, db: Any) -> None:
    """A nightly run that re-raised last year's drift would fill the queue with
    cases somebody already dealt with."""
    await give_history(db, timings=[0] * 10 + [3, 5, 7, 9, 11, 13, 15, 17])

    everything = await detect(client)
    assert everything["alarms"] > 0

    recent = await detect(client, since=(AS_OF - timedelta(days=1)).isoformat())
    assert recent["alarms"] == 0, "a change from last year was announced again"


async def test_the_detection_still_reads_the_whole_history(client: AsyncClient, db: Any) -> None:
    """`since` filters what is announced, not what is looked at: a change-point
    cannot be found from a fragment of the series it sits in."""
    await give_history(db, timings=[0] * 10 + [3, 5, 7, 9, 11, 13, 15, 17])
    body = await detect(client, since=(AS_OF - timedelta(days=1)).isoformat())
    assert body["members"] == 1
    assert body["with_history"] == 1, "the member was read even though nothing was announced"


# ---------------------------------------------------------------------------
# the configuration
# ---------------------------------------------------------------------------
async def test_the_thresholds_in_force_are_readable(client: AsyncClient) -> None:
    """So an operator can see them without reading the image."""
    body = (await client.get("/lmi/config")).json()
    assert body["signals"]["days_to_pay"]["threshold"] > 0
    assert body["signals"]["days_to_pay"]["min_scale"] == 2.0


async def test_what_the_thresholds_were_measured_to_do_travels_with_them(
    client: AsyncClient,
) -> None:
    """A later run can then be compared with a number rather than with
    somebody's memory."""
    body = (await client.get("/lmi/config")).json()
    measured = body["measured"]["days_to_pay"]
    assert measured["median_lead_days"] >= 21
    assert measured["steady_false_alarms_per_year"] <= 0.015


async def test_a_signal_nobody_measured_says_so(client: AsyncClient) -> None:
    """Rather than carrying a number that reads as evidence."""
    body = (await client.get("/lmi/config")).json()
    assert body["measured"]["savings_balance"]["measured"] is False
