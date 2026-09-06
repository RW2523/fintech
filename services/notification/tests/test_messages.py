"""T-065 — messages to members (docs/07 §5, docs/08 §5).

Every message is stored, because a member who says "nobody told me" deserves an
answer and the answer is a row. Nothing is sent that nobody approved.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from httpx import AsyncClient

from tests.conftest import ACCOUNT, MEMBER, OUTREACH_VARIABLES, REMINDER_VARIABLES


async def draft(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    body = {
        "member_id": MEMBER,
        "template_id": "OFFICER_OUTREACH",
        "variables": OUTREACH_VARIABLES,
    }
    body.update(overrides)
    return (await client.post("/notifications", json=body)).json()


# ---------------------------------------------------------------------------
# drafting
# ---------------------------------------------------------------------------
async def test_a_draft_is_rendered_and_stored(client: AsyncClient) -> None:
    body = await draft(client)
    assert body["state"] == "DRAFT"
    assert "the member" in body["body"]
    assert "{member_name}" not in body["body"]


async def test_a_missing_variable_is_refused_not_rendered(client: AsyncClient) -> None:
    """A member reading "your instalment of {amount}" has been shown a broken
    system by the organisation asking them for money."""
    response = await client.post(
        "/notifications",
        json={"member_id": MEMBER, "template_id": "OFFICER_OUTREACH", "variables": {}},
    )
    assert response.status_code == 422
    assert "member_name" in response.json()["error"]["message"]


async def test_an_unknown_template_is_a_not_found(client: AsyncClient) -> None:
    response = await client.post(
        "/notifications", json={"member_id": MEMBER, "template_id": "NO_SUCH", "variables": {}}
    )
    assert response.status_code == 404


async def test_an_unknown_channel_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/notifications",
        json={
            "member_id": MEMBER,
            "template_id": "OFFICER_OUTREACH",
            "variables": OUTREACH_VARIABLES,
            "channel": "CARRIER_PIGEON",
        },
    )
    assert response.status_code == 422


async def test_a_language_with_no_translation_falls_back_and_says_so(
    client: AsyncClient,
) -> None:
    """A member whose language has no translation still needs the message. What
    must not happen silently is the fallback."""
    body = await draft(client, language="lang_b")
    assert body["language"] == "en"
    assert body["requested_language"] == "lang_b"
    assert body["language_fallback"] is True


async def test_a_translated_template_is_served_in_that_language(client: AsyncClient) -> None:
    body = await draft(client, template_id="REM_14", language="lang_b", variables=REMINDER_VARIABLES)
    assert body["language"] == "lang_b"
    assert body["language_fallback"] is False
    assert body["body"].startswith("[lang_b]")


# ---------------------------------------------------------------------------
# approval
# ---------------------------------------------------------------------------
async def test_a_draft_is_not_in_the_members_inbox(client: AsyncClient) -> None:
    """A suggestion an officer is still writing is not something the member has
    been told."""
    body = await draft(client)
    inbox = (await client.get(f"/members/{MEMBER}/inbox")).json()
    assert body["message_id"] not in [m["message_id"] for m in inbox["messages"]]


async def test_approving_sends_it_and_it_appears(client: AsyncClient) -> None:
    body = await draft(client)
    approved = await client.post(f"/notifications/{body['message_id']}/approve", json={"actor_id": "u-1"})
    assert approved.status_code == 200

    inbox = (await client.get(f"/members/{MEMBER}/inbox")).json()
    sent = [m for m in inbox["messages"] if m["message_id"] == body["message_id"]]
    assert sent and sent[0]["state"] == "SENT"


async def test_an_edited_message_is_recorded_as_edited(client: AsyncClient, db: Any) -> None:
    """An edited message is no longer the template somebody reviewed, and a
    compliance reader needs to know which ones to read individually."""
    from sqlalchemy import text

    body = await draft(client)
    await client.post(
        f"/notifications/{body['message_id']}/approve",
        json={"actor_id": "u-1", "body": "Something the officer wrote instead."},
    )

    payload = (
        await db.execute(
            text(
                "SELECT payload FROM events.outbox WHERE name = 'outreach.sent' "
                "ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).scalar_one()
    assert payload["edited"] is True


async def test_a_message_cannot_be_sent_twice(client: AsyncClient) -> None:
    body = await draft(client)
    await client.post(f"/notifications/{body['message_id']}/approve", json={"actor_id": "u-1"})
    again = await client.post(f"/notifications/{body['message_id']}/approve", json={"actor_id": "u-2"})
    assert again.status_code == 409


async def test_sending_emits_an_event(client: AsyncClient, db: Any) -> None:
    from sqlalchemy import text

    body = await draft(client)
    await client.post(f"/notifications/{body['message_id']}/approve", json={"actor_id": "u-1"})

    name = (
        await db.execute(text("SELECT name FROM events.outbox ORDER BY created_at DESC LIMIT 1"))
    ).scalar_one()
    assert name == "outreach.sent"


# ---------------------------------------------------------------------------
# the cadence
# ---------------------------------------------------------------------------
async def schedule(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    due = date.today() + timedelta(days=20)
    body = {
        "member_id": MEMBER,
        "account_id": ACCOUNT,
        "due_date": due.isoformat(),
        "amount": "262.22",
        "account_ref": ACCOUNT,
        "member_name": "the member",
        "cooperative_name": "the cooperative",
    }
    body.update(overrides)
    return (await client.post("/notifications/schedule", json=body)).json()


async def test_the_cadence_covers_every_documented_offset(client: AsyncClient) -> None:
    body = await schedule(client)
    assert [r["offset_days"] for r in body["reminders"]] == [-14, -7, -3, 0, 1]


async def test_an_offset_already_past_is_not_sent_late(client: AsyncClient) -> None:
    """A member told on the due date that their instalment is due in a
    fortnight has been sent noise."""
    due = date.today() + timedelta(days=5)
    body = await schedule(client, due_date=due.isoformat())
    assert -14 not in [r["offset_days"] for r in body["reminders"]]
    assert 0 in [r["offset_days"] for r in body["reminders"]]


async def test_rebuilding_a_schedule_does_not_double_it(client: AsyncClient) -> None:
    """Two identical messages a day apart is how a member learns to ignore all
    of them."""
    first = await schedule(client)
    await schedule(client)

    inbox = (await client.get(f"/members/{MEMBER}/inbox")).json()
    queued = [m for m in inbox["messages"] if m["state"] == "QUEUED"]
    assert len(queued) == first["scheduled"]


async def test_payment_calls_off_the_rest(client: AsyncClient) -> None:
    """A member who paid on the due date and receives an overdue notice the
    next morning has been told the platform is not paying attention."""
    await schedule(client)
    cancelled = await client.post(
        "/notifications/cancel-schedule",
        params={"account_id": ACCOUNT, "event": "PAYMENT_RECEIVED"},
    )
    assert cancelled.json()["cancelled"] > 0
    assert cancelled.json()["reason"] == "the instalment was paid"


async def test_a_cancelled_reminder_is_kept_not_deleted(client: AsyncClient) -> None:
    """A member asking why they stopped hearing from the cooperative deserves
    an answer."""
    await schedule(client)
    await client.post(
        "/notifications/cancel-schedule", params={"account_id": ACCOUNT, "event": "PAYMENT_RECEIVED"}
    )

    inbox = (await client.get(f"/members/{MEMBER}/inbox")).json()
    cancelled = [m for m in inbox["messages"] if m["state"] == "CANCELLED"]
    assert cancelled
    assert cancelled[0]["cancel_reason"] == "the instalment was paid"


async def test_what_is_due_is_sent(client: AsyncClient) -> None:
    due = date.today() + timedelta(days=20)
    await schedule(client, due_date=due.isoformat())

    body = (
        await client.post("/notifications/due", params={"as_of": (due + timedelta(days=2)).isoformat()})
    ).json()
    assert body["sent"] == 5


async def test_nothing_is_sent_before_its_day(client: AsyncClient) -> None:
    due = date.today() + timedelta(days=20)
    await schedule(client, due_date=due.isoformat())
    body = (await client.post("/notifications/due", params={"as_of": date.today().isoformat()})).json()
    assert body["sent"] == 0


# ---------------------------------------------------------------------------
# what came back
# ---------------------------------------------------------------------------
async def test_an_outcome_is_recorded(client: AsyncClient) -> None:
    """A platform that records what it sent but not what came back cannot tell
    whether any of it works."""
    response = await client.post(
        "/outcomes",
        json={"member_id": MEMBER, "kind": "CONTACTED", "recorded_by": "u-1"},
    )
    assert response.status_code == 200

    outcomes = (await client.get(f"/members/{MEMBER}/outcomes")).json()
    assert outcomes["count"] == 1


async def test_a_promise_needs_the_date_it_was_made_for(client: AsyncClient) -> None:
    response = await client.post(
        "/outcomes",
        json={"member_id": MEMBER, "kind": "PROMISE_TO_PAY", "recorded_by": "u-1"},
    )
    assert response.status_code == 422


async def test_whether_a_promise_was_kept_can_be_recorded_later(client: AsyncClient) -> None:
    body = {
        "member_id": MEMBER,
        "kind": "PROMISE_TO_PAY",
        "promise_at": (date.today() + timedelta(days=5)).isoformat(),
        "recorded_by": "u-1",
    }
    await client.post("/outcomes", json=body)
    await client.post("/outcomes", json={**body, "promise_kept": True})

    outcomes = (await client.get(f"/members/{MEMBER}/outcomes")).json()
    assert outcomes["count"] == 1, "the same promise became two records"
    assert outcomes["outcomes"][0]["promise_kept"] is True


async def test_every_template_is_listed_for_review(client: AsyncClient) -> None:
    """So a compliance reviewer can read them all at once, before any member
    does."""
    body = (await client.get("/templates")).json()
    assert body["count"] >= 7
    assert any(t["template_id"] == "OVERDUE_1" for t in body["templates"])
