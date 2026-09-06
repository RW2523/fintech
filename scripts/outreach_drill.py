"""The outreach drill: a reminder cadence, and one officer's message.

    uv run python scripts/outreach_drill.py

Two things (docs/00 T-065), both against the running stack.

A schedule is built around a due date, the money arrives, and the remaining
reminders are called off. A member who paid on the due date and receives an
overdue notice the next morning has been told the platform is not paying
attention, and everything else it says is worth less afterwards.

And an officer drafts an outreach about a member the early-warning engine
raised, approves it, and it appears in that member's inbox. Nothing is sent
that nobody approved.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import date, timedelta
from typing import Any

import httpx

BASE = "http://localhost:8000"
MEMBER = "M-000042"

#: A fresh account and due date each run. A reminder somebody cancelled stays
#: cancelled, which is the behaviour under test, so a drill that reused the
#: same instalment would schedule nothing on its second run and report the
#: service broken.
RUN = f"{int(time.time()) % 100_000:05d}"
ACCOUNT = f"A-DRILL{RUN}"


async def main() -> int:
    ok = True
    async with httpx.AsyncClient(timeout=60.0) as anon:
        token = (await anon.post(f"{BASE}/api/auth/dev-token", json={"role": "collections"})).json()[
            "access_token"
        ]

    async with httpx.AsyncClient(timeout=60.0, headers={"authorization": f"Bearer {token}"}) as c:
        due = date.today() + timedelta(days=20)

        # --- 1. the cadence -------------------------------------------------
        scheduled = await c.post(
            f"{BASE}/api/notification/notifications/schedule",
            json={
                "member_id": MEMBER,
                "account_id": ACCOUNT,
                "due_date": due.isoformat(),
                "amount": "262.22",
                "account_ref": ACCOUNT,
                "member_name": "the member",
                "cooperative_name": "the cooperative",
                "language": "en",
            },
        )
        body: dict[str, Any] = scheduled.json()
        print(f"  scheduled {body.get('scheduled')} reminders around {due}")
        for reminder in body.get("reminders", []):
            print(
                f"    {reminder['offset_days']:>+4}d  {reminder['template_id']:<16} "
                f"{reminder['scheduled_at']}"
            )
        ok &= scheduled.status_code == 200 and body.get("scheduled", 0) >= 3

        # Rebuilding must not double them.
        again = (
            await c.post(
                f"{BASE}/api/notification/notifications/schedule",
                json={
                    "member_id": MEMBER,
                    "account_id": ACCOUNT,
                    "due_date": due.isoformat(),
                    "amount": "262.22",
                    "account_ref": ACCOUNT,
                    "member_name": "the member",
                    "cooperative_name": "the cooperative",
                },
            )
        ).json()
        inbox = (await c.get(f"{BASE}/api/notification/members/{MEMBER}/inbox")).json()
        queued = [m for m in inbox["messages"] if m["state"] == "QUEUED"]
        print(
            f"  after a rebuild: {len(queued)} queued, "
            f"{again.get('already_scheduled')} already scheduled (not {len(queued) * 2})"
        )
        ok &= len(queued) == body.get("scheduled")
        ok &= again.get("scheduled") == 0
        ok &= again.get("already_scheduled") == len(queued)

        # --- 2. the money arrives ------------------------------------------
        cancelled = await c.post(
            f"{BASE}/api/notification/notifications/cancel-schedule",
            params={"account_id": ACCOUNT, "event": "PAYMENT_RECEIVED"},
        )
        print(
            f"  payment received -> {cancelled.json().get('cancelled')} reminders called off "
            f"({cancelled.json().get('reason')})"
        )
        ok &= cancelled.status_code == 200 and cancelled.json().get("cancelled", 0) > 0

        # --- 3. an officer's outreach ---------------------------------------
        drafted = await c.post(
            f"{BASE}/api/notification/notifications",
            json={
                "member_id": MEMBER,
                "template_id": "OFFICER_OUTREACH",
                "language": "en",
                "channel": "SMS",
                "variables": {
                    "member_name": "the member",
                    "message": "We noticed your instalments have been arriving a little later "
                    "than usual and wanted to check everything is alright.",
                    "officer_name": "A. Officer",
                    "cooperative_name": "the cooperative",
                },
            },
        )
        draft = drafted.json()
        print(f"  drafted {draft.get('message_id')} state {draft.get('state')}")
        ok &= drafted.status_code == 200 and draft.get("state") == "DRAFT"

        # A draft is not in the member's inbox until somebody approves it.
        before = (await c.get(f"{BASE}/api/notification/members/{MEMBER}/inbox")).json()
        assert draft["message_id"] not in [m["message_id"] for m in before["messages"]], (
            "an unapproved draft reached the member's inbox"
        )

        approved = await c.post(
            f"{BASE}/api/notification/notifications/{draft['message_id']}/approve",
            json={"actor_id": "u-collections"},
        )
        print(f"  approved -> {approved.status_code} {approved.json().get('state')}")
        ok &= approved.status_code == 200

        after = (await c.get(f"{BASE}/api/notification/members/{MEMBER}/inbox")).json()
        sent = [m for m in after["messages"] if m["message_id"] == draft["message_id"]]
        print(f"  inbox holds it: {bool(sent)} state {sent[0]['state'] if sent else '-'}")
        ok &= bool(sent) and sent[0]["state"] == "SENT"

        # --- 4. what came back ----------------------------------------------
        outcome = await c.post(
            f"{BASE}/api/notification/outcomes",
            json={
                "member_id": MEMBER,
                "kind": "PROMISE_TO_PAY",
                "promise_at": (date.today() + timedelta(days=5)).isoformat(),
                "message_id": draft["message_id"],
                "recorded_by": "u-collections",
                "note": "the member said their employer paid late and they will settle Friday",
            },
        )
        print(f"  outcome recorded -> {outcome.status_code} {outcome.json().get('kind')}")
        ok &= outcome.status_code == 200

        # --- 5. a language nobody translated --------------------------------
        other = await c.post(
            f"{BASE}/api/notification/notifications",
            json={
                "member_id": MEMBER,
                "template_id": "OFFICER_OUTREACH",
                "language": "lang_b",
                "variables": {
                    "member_name": "the member",
                    "message": "checking in",
                    "officer_name": "A. Officer",
                    "cooperative_name": "the cooperative",
                },
            },
        )
        fell_back = other.json()
        print(
            f"  lang_b outreach -> served in {fell_back.get('language')}, "
            f"fallback flagged {fell_back.get('language_fallback')}"
        )
        ok &= fell_back.get("language_fallback") is True

    print("\n  T-065 acceptance:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
