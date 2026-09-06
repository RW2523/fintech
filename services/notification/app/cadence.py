"""Scheduling reminders around a due date (docs/07 §5).

The cadence comes from the policy pack, not from here. An institution that
decides three reminders is too many changes a list in a file, not a service.

Two rules do the real work.

A reminder is cancelled when the money arrives. A member who paid on the due
date and receives an overdue notice the next morning has been told the platform
is not paying attention, and everything else it says is worth less afterwards.

And a schedule is built once per due event and is idempotent. Rebuilding it
must not double the reminders, because two identical messages a day apart is
how a member learns to ignore all of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.templates import CADENCE_TEMPLATES
from cio_common.ids import derived_id

__all__ = ["DEFAULT_CADENCE", "Reminder", "cancel_reason", "schedule_for"]

#: docs/07 §5 — offsets in days from the due date. Overridden by the pack.
DEFAULT_CADENCE = (-14, -7, -3, 0, 1)


@dataclass
class Reminder:
    """One scheduled message, before it is anything else."""

    message_id: str
    member_id: str
    account_id: str
    template_id: str
    offset_days: int
    due_date: date
    scheduled_at: date
    language: str = "en"
    channel: str = "SMS"
    variables: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "member_id": self.member_id,
            "account_id": self.account_id,
            "template_id": self.template_id,
            "offset_days": self.offset_days,
            "due_date": self.due_date.isoformat(),
            "scheduled_at": self.scheduled_at.isoformat(),
            "language": self.language,
            "channel": self.channel,
            "variables": self.variables,
        }


def schedule_for(
    *,
    member_id: str,
    account_id: str,
    due_date: date,
    cadence: tuple[int, ...] | list[int] = DEFAULT_CADENCE,
    language: str = "en",
    channel: str = "SMS",
    variables: dict[str, Any] | None = None,
    as_of: date | None = None,
) -> list[Reminder]:
    """Every reminder for one instalment, oldest first.

    Offsets already in the past are skipped rather than sent late. A member who
    is told on the due date that their instalment is due in a fortnight has
    been sent noise, and the platform has spent credibility it will want later.
    """
    today = as_of or date.today()
    body = dict(variables or {})
    body.setdefault("due_date", due_date.isoformat())

    out: list[Reminder] = []
    for offset in sorted(cadence):
        template_id = CADENCE_TEMPLATES.get(offset)
        if template_id is None:
            continue
        when = due_date + timedelta(days=offset)
        if when < today:
            continue
        out.append(
            Reminder(
                # Derived from what it is about, so rebuilding the schedule
                # lands on the same rows rather than doubling them.
                message_id=derived_id("msg", member_id, account_id, due_date.isoformat(), str(offset)),
                member_id=member_id,
                account_id=account_id,
                template_id=template_id,
                offset_days=offset,
                due_date=due_date,
                scheduled_at=when,
                language=language,
                channel=channel,
                variables=body,
            )
        )
    return out


def cancel_reason(event: str) -> str:
    """Why the rest of a schedule was called off.

    Recorded rather than deleted: a member asking why they stopped hearing from
    the cooperative deserves an answer, and so does an officer wondering
    whether a reminder went out.
    """
    return {
        "PAYMENT_RECEIVED": "the instalment was paid",
        "ARRANGEMENT_AGREED": "an arrangement covers this instalment",
        "ACCOUNT_CLOSED": "the account was closed",
    }.get(event, f"cancelled on {event}")
