"""notification-service endpoints (docs/07 §5, docs/08 §5).

Every message to a member passes through here, and every one is stored. A
member who says "nobody told me" gets an answer, and it is a row rather than a
recollection.

Channels are stubs in this build. They log and they land in the member's inbox,
which is what a demo needs and what a real integration would replace. What is
not a stub is the record: a message marked SENT here is one the cooperative
would stand behind.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.cadence import DEFAULT_CADENCE, cancel_reason, schedule_for
from app.db import session
from app.templates import TEMPLATES, missing_variables, render, template_for
from cio_common.errors import Conflict, NotFound, ValidationFailed
from cio_common.ids import derived_id, new_id
from cio_common.outbox import emit

router = APIRouter(tags=["notification"])

#: What a channel stub does. Named so the demo cannot be mistaken for a
#: platform that is actually texting anybody.
CHANNELS = ("SMS", "EMAIL", "APP")


def _now() -> datetime:
    return datetime.now(UTC)


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: str = Field(min_length=1, max_length=64)
    template_id: str = Field(min_length=2, max_length=48)
    variables: dict[str, Any] = Field(default_factory=dict)
    language: str = Field(default="en", max_length=16)
    channel: str = Field(default="SMS", max_length=16)
    account_id: str | None = None
    case_id: str | None = None
    #: A draft is a suggestion until somebody approves it. Set only by an
    #: automated cadence, which is approved by the policy that scheduled it.
    auto_approve: bool = False


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1, max_length=64)
    #: What the officer actually wants sent, when they edited the draft. The
    #: template is still recorded, so a message nobody can trace to one is
    #: visible as such.
    body: str | None = None
    subject: str | None = None


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: str = Field(min_length=1, max_length=64)
    account_id: str = Field(min_length=1, max_length=64)
    due_date: str
    amount: str = "0.00"
    account_ref: str | None = None
    member_name: str = "member"
    cooperative_name: str = "the cooperative"
    language: str = "en"
    channel: str = "SMS"
    cadence: list[int] | None = None
    as_of: str | None = None


class HandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=3, max_length=400)
    #: ROUTINE, or one of the four the assistant must never handle alone.
    signal: str = Field(
        default="ROUTINE",
        pattern="^(ROUTINE|HARDSHIP|COMPLAINT|BEREAVEMENT|VULNERABILITY)$",
    )
    urgency: str = Field(default="ROUTINE", pattern="^(ROUTINE|PRIORITY)$")
    case_id: str | None = None
    #: What the member wrote. A paraphrase of a hardship disclosure loses the
    #: part a person needs to read, so the words are kept as they were said.
    said: str | None = Field(default=None, max_length=4000)
    raised_by: str = Field(default="member_assistant", min_length=1, max_length=64)


class OutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: str = Field(min_length=1, max_length=64)
    kind: str = Field(pattern="^(CONTACTED|NO_ANSWER|PROMISE_TO_PAY|REFUSED|RESOLVED)$")
    recorded_by: str = Field(min_length=1, max_length=64)
    message_id: str | None = None
    case_id: str | None = None
    promise_at: str | None = None
    promise_kept: bool | None = None
    note: str = Field(default="", max_length=1000)


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------
@router.get("/templates", summary="Every message the platform can send")
async def templates(language: str | None = None) -> dict[str, Any]:
    """Listed so an officer can see what a member would receive before it is
    sent, and so a compliance reviewer can read them all at once."""
    found = [
        template.as_dict()
        for (_, lang), template in sorted(TEMPLATES.items())
        if language is None or lang == language
    ]
    return {"count": len(found), "templates": found}


# ---------------------------------------------------------------------------
# one message
# ---------------------------------------------------------------------------
@router.post("/notifications", summary="Draft a message to a member")
async def draft(body: DraftRequest) -> dict[str, Any]:
    """Render a template into a draft. Nothing is sent by this."""
    if body.channel not in CHANNELS:
        raise ValidationFailed(f"unknown channel {body.channel!r}", channels=list(CHANNELS))

    try:
        template = template_for(body.template_id, body.language)
    except KeyError as exc:
        raise NotFound(str(exc), template_id=body.template_id) from exc

    absent = missing_variables(template, body.variables)
    if absent:
        # Refused rather than rendered with a gap: a member reading "your
        # instalment of {amount}" has been shown a broken system by the
        # organisation asking them for money.
        raise ValidationFailed(
            f"{body.template_id} needs {', '.join(absent)}",
            template_id=body.template_id,
            missing=absent,
        )

    subject, rendered = render(template, body.variables)
    message_id = new_id("msg")
    state = "QUEUED" if body.auto_approve else "DRAFT"

    async with session() as db:
        await db.execute(
            text("""
            INSERT INTO app_notification.message
              (message_id, member_id, account_id, case_id, template_id, language, channel,
               subject, body, variables, state, created_by)
            VALUES (:message_id, :member_id, :account_id, :case_id, :template_id, :language,
                    :channel, :subject, :body, CAST(:variables AS jsonb), :state, :created_by)
        """),
            {
                "message_id": message_id,
                "member_id": body.member_id,
                "account_id": body.account_id,
                "case_id": body.case_id,
                "template_id": body.template_id,
                # The language actually used, which may not be the one asked
                # for. A silent fallback is how a member receives English for
                # a year while their record says otherwise.
                "language": template.language,
                "channel": body.channel,
                "subject": subject,
                "body": rendered,
                "variables": json.dumps(body.variables, default=str),
                "state": state,
                "created_by": "cadence" if body.auto_approve else "officer",
            },
        )
        await db.commit()

    return {
        "message_id": message_id,
        "state": state,
        "template_id": body.template_id,
        "language": template.language,
        "requested_language": body.language,
        "language_fallback": template.language != body.language,
        "channel": body.channel,
        "subject": subject,
        "body": rendered,
    }


@router.post("/notifications/{message_id}/approve", summary="Approve and send a draft")
async def approve(message_id: str, body: ApproveRequest, request: Request) -> dict[str, Any]:
    """docs/09 §4 — an officer approves an outreach, and it goes.

    Approval and sending are one step on purpose. A message approved and then
    stuck in a queue is one an officer believes was sent, and they will not
    check.
    """
    async with session() as db:
        row = (
            (
                await db.execute(
                    text("SELECT * FROM app_notification.message WHERE message_id = :id"),
                    {"id": message_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound(f"no message {message_id!r}")
        if row["state"] == "SENT":
            raise Conflict(f"message {message_id!r} was already sent", sent_at=str(row["sent_at"]))
        if row["cancelled_at"] is not None:
            raise Conflict(f"message {message_id!r} was cancelled", reason=row["cancel_reason"])

        subject = body.subject if body.subject is not None else row["subject"]
        rendered = body.body if body.body is not None else row["body"]
        edited = body.body is not None or body.subject is not None

        await db.execute(
            text("""
            UPDATE app_notification.message
               SET state = 'SENT', approved_by = :actor, approved_at = now(),
                   sent_at = now(), subject = :subject, body = :body
             WHERE message_id = :id
        """),
            {"id": message_id, "actor": body.actor_id, "subject": subject, "body": rendered},
        )
        await emit(
            db,
            "outreach.sent",
            {
                "message_id": message_id,
                "member_id": row["member_id"],
                "account_id": row["account_id"],
                "case_id": row["case_id"],
                "template_id": row["template_id"],
                "language": row["language"],
                "channel": row["channel"],
                "approved_by": body.actor_id,
                # Recorded because an edited message is no longer the template
                # somebody reviewed, and a compliance reader needs to know
                # which ones to read individually.
                "edited": edited,
            },
            key=str(row["member_id"]),
            producer="notification",
            case_id=row["case_id"],
            trace_id=request.headers.get("x-trace-id"),
        )
        await db.commit()

    return {
        "message_id": message_id,
        "state": "SENT",
        "approved_by": body.actor_id,
        "edited": edited,
        "channel": row["channel"],
    }


@router.post("/notifications/{message_id}/cancel", summary="Call off a queued message")
async def cancel(message_id: str, event: str = "PAYMENT_RECEIVED") -> dict[str, Any]:
    async with session() as db:
        result = await db.execute(
            text("""
            UPDATE app_notification.message
               SET cancelled_at = now(), cancel_reason = :reason, state = 'CANCELLED'
             WHERE message_id = :id AND sent_at IS NULL AND cancelled_at IS NULL
            RETURNING message_id
        """),
            {"id": message_id, "reason": cancel_reason(event)},
        )
        cancelled = result.first() is not None
        await db.commit()
    if not cancelled:
        raise Conflict(f"message {message_id!r} is not cancellable", message_id=message_id)
    return {"message_id": message_id, "state": "CANCELLED", "reason": cancel_reason(event)}


# ---------------------------------------------------------------------------
# the cadence
# ---------------------------------------------------------------------------
@router.post("/notifications/schedule", summary="Schedule the reminders for one instalment")
async def schedule(body: ScheduleRequest) -> dict[str, Any]:
    """docs/07 §5 — the cadence from policy, around one due date.

    Idempotent: rebuilding a schedule lands on the same rows rather than
    doubling the reminders, because two identical messages a day apart is how a
    member learns to ignore all of them.
    """
    due = date.fromisoformat(body.due_date)
    as_of = date.fromisoformat(body.as_of) if body.as_of else datetime.now(UTC).date()

    variables = {
        "member_name": body.member_name,
        "amount": body.amount,
        "account_ref": body.account_ref or body.account_id,
        "due_date": due.isoformat(),
        "cooperative_name": body.cooperative_name,
    }
    reminders = schedule_for(
        member_id=body.member_id,
        account_id=body.account_id,
        due_date=due,
        cadence=tuple(body.cadence) if body.cadence else DEFAULT_CADENCE,
        language=body.language,
        channel=body.channel,
        variables=variables,
        as_of=as_of,
    )

    written: list[dict[str, Any]] = []
    async with session() as db:
        for reminder in reminders:
            template = template_for(reminder.template_id, reminder.language)
            absent = missing_variables(template, variables)
            if absent:
                # Skipped rather than scheduled to fail on the day: a reminder
                # that cannot be rendered is better caught now.
                written.append({**reminder.as_dict(), "skipped": f"needs {', '.join(absent)}"})
                continue
            subject, rendered = render(template, variables)
            # ON CONFLICT DO NOTHING, deliberately. A reminder somebody
            # cancelled stays cancelled: re-running the scheduler must not
            # quietly undo a decision not to contact a member. The result says
            # which ones already existed, so a caller can see it happened
            # rather than wonder why nothing was queued.
            existing = (
                await db.execute(
                    text("SELECT state FROM app_notification.message WHERE message_id = :id"),
                    {"id": reminder.message_id},
                )
            ).scalar_one_or_none()
            if existing is not None:
                written.append({**reminder.as_dict(), "existing": existing})
                continue

            await db.execute(
                text("""
                INSERT INTO app_notification.message
                  (message_id, member_id, account_id, template_id, language, channel,
                   subject, body, variables, state, scheduled_at, created_by)
                VALUES (:message_id, :member_id, :account_id, :template_id, :language,
                        :channel, :subject, :body, CAST(:variables AS jsonb), 'QUEUED',
                        :scheduled_at, 'cadence')
                ON CONFLICT (message_id) DO NOTHING
            """),
                {
                    "message_id": reminder.message_id,
                    "member_id": reminder.member_id,
                    "account_id": reminder.account_id,
                    "template_id": reminder.template_id,
                    "language": template.language,
                    "channel": reminder.channel,
                    "subject": subject,
                    "body": rendered,
                    "variables": json.dumps(variables, default=str),
                    "scheduled_at": datetime.combine(reminder.scheduled_at, datetime.min.time(), tzinfo=UTC),
                },
            )
            written.append(reminder.as_dict())
        await db.commit()

    return {
        "member_id": body.member_id,
        "account_id": body.account_id,
        "due_date": due.isoformat(),
        "scheduled": len([r for r in written if "skipped" not in r and "existing" not in r]),
        "already_scheduled": len([r for r in written if "existing" in r]),
        "skipped": len([r for r in written if "skipped" in r]),
        "reminders": written,
    }


@router.post("/notifications/cancel-schedule", summary="Call off a member's remaining reminders")
async def cancel_schedule(
    account_id: str, event: str = "PAYMENT_RECEIVED", due_date: str | None = None
) -> dict[str, Any]:
    """docs/07 §5 — the money arrived, so the reminders stop.

    A member who paid on the due date and receives an overdue notice the next
    morning has been told the platform is not paying attention, and everything
    else it says is worth less afterwards.
    """
    reason = cancel_reason(event)
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            UPDATE app_notification.message
               SET cancelled_at = now(), cancel_reason = :reason, state = 'CANCELLED'
             WHERE account_id = :account_id
               AND state = 'QUEUED' AND sent_at IS NULL AND cancelled_at IS NULL
               AND (CAST(:due_date AS date) IS NULL
                    OR (variables ->> 'due_date') = CAST(:due_date AS text))
            RETURNING message_id, template_id
        """),
                    {"account_id": account_id, "reason": reason, "due_date": due_date},
                )
            )
            .mappings()
            .all()
        )
        await db.commit()

    return {
        "account_id": account_id,
        "cancelled": len(rows),
        "reason": reason,
        "messages": [dict(row) for row in rows],
    }


@router.post("/notifications/due", summary="Send what is due now")
async def send_due(
    as_of: str | None = None, limit: int = Query(default=500, ge=1, le=5000)
) -> dict[str, Any]:
    """The cadence's own tick. Sends every queued message whose day has come."""
    when = datetime.combine(date.fromisoformat(as_of), datetime.max.time(), tzinfo=UTC) if as_of else _now()

    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT * FROM app_notification.message
             WHERE state = 'QUEUED' AND sent_at IS NULL AND cancelled_at IS NULL
               AND scheduled_at <= :when
             ORDER BY scheduled_at LIMIT :limit
        """),
                    {"when": when, "limit": limit},
                )
            )
            .mappings()
            .all()
        )

        for row in rows:
            await db.execute(
                text("""
                UPDATE app_notification.message SET state = 'SENT', sent_at = now()
                 WHERE message_id = :id
            """),
                {"id": row["message_id"]},
            )
            await emit(
                db,
                "outreach.sent",
                {
                    "message_id": row["message_id"],
                    "member_id": row["member_id"],
                    "account_id": row["account_id"],
                    "template_id": row["template_id"],
                    "language": row["language"],
                    "channel": row["channel"],
                    "approved_by": "cadence",
                    "edited": False,
                },
                key=str(row["member_id"]),
                producer="notification",
            )
        await db.commit()

    return {
        "as_of": when.isoformat(),
        "sent": len(rows),
        "messages": [
            {
                "message_id": row["message_id"],
                "member_id": row["member_id"],
                "template_id": row["template_id"],
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# the member's side
# ---------------------------------------------------------------------------
@router.get("/members/{member_id}/inbox", summary="What a member has been sent")
async def inbox(
    member_id: str, include_drafts: bool = False, limit: int = Query(default=100, ge=1, le=1000)
) -> dict[str, Any]:
    """What the member would see, in the order they received it.

    Drafts are excluded by default: a suggestion an officer is still writing is
    not something the member has been told, and showing it here would make the
    inbox a poor answer to "what did you send me".
    """
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT * FROM app_notification.message
             WHERE member_id = :member_id
               AND (:include_drafts OR state IN ('SENT', 'QUEUED', 'CANCELLED'))
             ORDER BY coalesce(sent_at, scheduled_at, created_at) DESC
             LIMIT :limit
        """),
                    {"member_id": member_id, "include_drafts": include_drafts, "limit": limit},
                )
            )
            .mappings()
            .all()
        )

    return {
        "member_id": member_id,
        "count": len(rows),
        "messages": [
            {
                "message_id": row["message_id"],
                "template_id": row["template_id"],
                "language": row["language"],
                "channel": row["channel"],
                "state": row["state"],
                "subject": row["subject"],
                "body": row["body"],
                "sent_at": row["sent_at"].isoformat() if row["sent_at"] else None,
                "scheduled_at": row["scheduled_at"].isoformat() if row["scheduled_at"] else None,
                "cancelled_at": row["cancelled_at"].isoformat() if row["cancelled_at"] else None,
                "cancel_reason": row["cancel_reason"],
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# what came back
# ---------------------------------------------------------------------------
@router.post("/outcomes", summary="Record what a member did about a contact")
async def record_outcome(body: OutcomeRequest) -> dict[str, Any]:
    """docs/09 §4 — the point of an outreach is the answer.

    A platform that records what it sent but not what came back cannot tell
    whether any of it works, and an officer deciding whether to call again is
    left guessing.
    """
    if body.kind == "PROMISE_TO_PAY" and not body.promise_at:
        raise ValidationFailed("a promise to pay needs the date it was made for")

    outcome_id = derived_id("evt", body.member_id, body.kind, body.message_id or "", body.recorded_by)
    async with session() as db:
        await db.execute(
            text("""
            INSERT INTO app_notification.outcome
              (outcome_id, member_id, message_id, case_id, kind, promise_at, promise_kept,
               note, recorded_by)
            VALUES (:outcome_id, :member_id, :message_id, :case_id, :kind, :promise_at,
                    :promise_kept, :note, :recorded_by)
            ON CONFLICT (outcome_id) DO UPDATE SET
              promise_kept = EXCLUDED.promise_kept, note = EXCLUDED.note,
              recorded_at = now()
        """),
            {
                "outcome_id": outcome_id,
                "member_id": body.member_id,
                "message_id": body.message_id,
                "case_id": body.case_id,
                "kind": body.kind,
                "promise_at": date.fromisoformat(body.promise_at) if body.promise_at else None,
                "promise_kept": body.promise_kept,
                "note": body.note,
                "recorded_by": body.recorded_by,
            },
        )
        await emit(
            db,
            "outreach.outcome_recorded",
            {
                "outcome_id": outcome_id,
                "member_id": body.member_id,
                "kind": body.kind,
                "promise_at": body.promise_at,
                "promise_kept": body.promise_kept,
                "recorded_by": body.recorded_by,
            },
            key=body.member_id,
            producer="notification",
            case_id=body.case_id,
        )
        await db.commit()

    return {"outcome_id": outcome_id, "member_id": body.member_id, "kind": body.kind}


# ---------------------------------------------------------------------------
# handoffs to a person (T-071)
# ---------------------------------------------------------------------------
@router.post("/handoffs", summary="Ask a person to pick this member up")
async def raise_handoff(body: HandoffRequest) -> dict[str, Any]:
    """docs/06 §2.3 — where the member assistant stops.

    The row is written before the member is told anybody will call. An
    assistant that says "someone will be in touch" and leaves no trace has lied
    to somebody who was probably already having a bad week.

    No event is emitted here. `member.hardship_signal.v1` is produced by the
    agent runtime, which is where the disclosure was heard, and this service is
    one of its consumers (contracts/events.yaml). Emitting it here as well
    would put the same signal on the bus twice under two producers.
    """
    handoff_id = new_id("hnd")
    async with session() as db:
        await db.execute(
            text("""
            INSERT INTO app_notification.handoff
              (handoff_id, member_id, case_id, signal, urgency, reason, said, raised_by)
            VALUES (:handoff_id, :member_id, :case_id, :signal, :urgency, :reason,
                    :said, :raised_by)
        """),
            {
                "handoff_id": handoff_id,
                "member_id": body.member_id,
                "case_id": body.case_id,
                "signal": body.signal,
                "urgency": body.urgency,
                "reason": body.reason,
                "said": body.said,
                "raised_by": body.raised_by,
            },
        )
        await db.commit()

    return {
        "handoff_id": handoff_id,
        "member_id": body.member_id,
        "signal": body.signal,
        "urgency": body.urgency,
        "state": "OPEN",
    }


@router.get("/handoffs", summary="Handoffs waiting for a person")
async def handoffs(
    member_id: str | None = Query(default=None),
    state: str = Query(default="OPEN"),
) -> dict[str, Any]:
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT * FROM app_notification.handoff
             WHERE (CAST(:member_id AS text) IS NULL OR member_id = CAST(:member_id AS text))
               AND (CAST(:state AS text) = 'ALL' OR state = CAST(:state AS text))
             ORDER BY created_at DESC LIMIT 200
        """),
                    {"member_id": member_id, "state": state},
                )
            )
            .mappings()
            .all()
        )
    entries = [dict(row) for row in rows]
    return {"handoffs": entries, "count": len(entries)}


@router.post("/handoffs/{handoff_id}/close", summary="A person picked it up")
async def close_handoff(handoff_id: str, closed_by: str = Query(min_length=1)) -> dict[str, Any]:
    async with session() as db:
        row = (
            (
                await db.execute(
                    text("""
            UPDATE app_notification.handoff
               SET state = 'CLOSED', closed_by = :closed_by, closed_at = now()
             WHERE handoff_id = :handoff_id AND state = 'OPEN'
         RETURNING handoff_id, member_id, state, closed_by, closed_at
        """),
                    {"handoff_id": handoff_id, "closed_by": closed_by},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound(f"no open handoff {handoff_id!r}")
        await db.commit()
    return dict(row)


@router.get("/members/{member_id}/outcomes", summary="What this member has said")
async def outcomes(member_id: str) -> dict[str, Any]:
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT * FROM app_notification.outcome
             WHERE member_id = :m ORDER BY recorded_at DESC LIMIT 100
        """),
                    {"m": member_id},
                )
            )
            .mappings()
            .all()
        )
    return {
        "member_id": member_id,
        "count": len(rows),
        "outcomes": [
            {
                **dict(row),
                "promise_at": row["promise_at"].isoformat() if row["promise_at"] else None,
                "recorded_at": row["recorded_at"].isoformat(),
            }
            for row in rows
        ],
    }
