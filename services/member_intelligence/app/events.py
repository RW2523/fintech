"""Turning core records into member events (docs/03 §9, docs/07 §4.1).

The core system holds rows; the intelligence layer needs a timeline. This is
where one becomes the other. Every event carries its provenance, a data-quality
assessment and the purposes it may be used for, because a signal with no
provenance cannot be cited to a member (CLAUDE.md §2.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from cio_common.hashing import sha256

__all__ = ["EVENT_TYPES", "MemberEvent", "days_late", "derive_events"]

#: The subset of docs/03 §9 the core stub can produce today. The rest arrive
#: with the services that generate them (notifications, LMI, decisions).
EVENT_TYPES = (
    "PAYMENT_DUE",
    "PAYMENT_RECEIVED",
    "PAYMENT_LATE",
    "PAYMENT_PARTIAL",
    "DEDUCTION_RECEIVED",
    "DEDUCTION_MISSED",
    "SAVINGS_BALANCE",
    "SHARE_CAPITAL",
    "ARRANGEMENT_ACTIVE",
    "OUTAGE_WINDOW",
    "APPLICATION",
)

#: Beyond this a payment is late rather than merely slow (docs/10 §5).
LATE_THRESHOLD_DAYS = 7
PARTIAL_TOLERANCE = 0.99

#: Every event is usable for servicing and collections; underwriting and fraud
#: are added where the event bears on them (docs/03 §2 permitted uses).
_BASE_USES = ("SERVICING", "COLLECTIONS")
_CREDIT_USES = ("UNDERWRITING", "SERVICING", "COLLECTIONS", "ANALYTICS")


@dataclass(frozen=True, slots=True)
class MemberEvent:
    """One thing that happened to a member (docs/03 §9)."""

    event_id: str
    member_id: str
    account_id: str | None
    occurred_at: datetime
    event_type: str
    source_system: str
    source_record_id: str
    payload: dict[str, Any]
    data_quality: dict[str, Any]
    permitted_uses: tuple[str, ...]
    evidence_refs: tuple[str, ...] = field(default=())

    def as_row(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "member_id": self.member_id,
            "account_id": self.account_id,
            "occurred_at": self.occurred_at,
            "event_type": self.event_type,
            "source_system": self.source_system,
            "source_record_id": self.source_record_id,
            "payload": self.payload,
            "data_quality": self.data_quality,
            "permitted_uses": list(self.permitted_uses),
            "evidence_refs": list(self.evidence_refs),
        }


def _event_id(*parts: str) -> str:
    """Deterministic, so re-importing the same row does not duplicate it."""
    return "evt_" + sha256("|".join(parts))[:26].upper()


def _quality(confidence: float = 1.0, validation: str = "PASS", freshness_s: float = 0.0) -> dict[str, Any]:
    return {"freshness_s": freshness_s, "confidence": confidence, "validation": validation}


def _at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), UTC)
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def days_late(due: Any, paid_at: Any) -> int | None:
    if paid_at is None:
        return None
    return (_at(paid_at).date() - _at(due).date()).days


def derive_events(
    *,
    member_id: str,
    schedules: list[dict[str, Any]] | None = None,
    payments: list[dict[str, Any]] | None = None,
    deductions: list[dict[str, Any]] | None = None,
    savings: list[dict[str, Any]] | None = None,
    shares: list[dict[str, Any]] | None = None,
    arrangements: list[dict[str, Any]] | None = None,
    account_of_schedule: dict[str, str] | None = None,
) -> list[MemberEvent]:
    """Every event derivable from one member's core records."""
    events: list[MemberEvent] = []
    owner = account_of_schedule or {}
    paid_by_schedule = {p["schedule_id"]: p for p in (payments or [])}

    for row in schedules or []:
        account_id = row.get("account_id") or owner.get(row["schedule_id"])
        due = _at(row["due_date"])
        amount_due = str(row["amount_due"])

        events.append(
            MemberEvent(
                event_id=_event_id("due", row["schedule_id"]),
                member_id=member_id,
                account_id=account_id,
                occurred_at=due,
                event_type="PAYMENT_DUE",
                source_system="core.schedule",
                source_record_id=row["schedule_id"],
                payload={
                    "schedule_id": row["schedule_id"],
                    "due_date": due.date().isoformat(),
                    "amount_due": amount_due,
                },
                data_quality=_quality(),
                permitted_uses=_CREDIT_USES,
            )
        )

        payment = paid_by_schedule.get(row["schedule_id"])
        if payment is None:
            continue

        late = days_late(due, payment["paid_at"]) or 0
        paid = float(str(payment["amount_paid"]))
        expected = float(amount_due)
        partial = paid < expected * PARTIAL_TOLERANCE

        event_type = (
            "PAYMENT_PARTIAL"
            if partial
            else "PAYMENT_LATE"
            if late > LATE_THRESHOLD_DAYS
            else "PAYMENT_RECEIVED"
        )
        events.append(
            MemberEvent(
                event_id=_event_id("pay", payment["payment_id"]),
                member_id=member_id,
                account_id=account_id,
                occurred_at=_at(payment["paid_at"]),
                event_type=event_type,
                source_system="core.payment",
                source_record_id=payment["payment_id"],
                payload={
                    "schedule_id": row["schedule_id"],
                    "due_date": due.date().isoformat(),
                    "amount_due": amount_due,
                    "amount_paid": str(payment["amount_paid"]),
                    "paid_at": _at(payment["paid_at"]).isoformat(),
                    "days_late": late,
                },
                data_quality=_quality(),
                permitted_uses=_CREDIT_USES,
            )
        )

    for row in deductions or []:
        received = row.get("received_amount") is not None
        when = _at(row["received_at"]) if received else _at(f"{row['cycle']}-28")
        events.append(
            MemberEvent(
                event_id=_event_id("ded", row["deduction_id"]),
                member_id=member_id,
                account_id=None,
                occurred_at=when,
                event_type="DEDUCTION_RECEIVED" if received else "DEDUCTION_MISSED",
                source_system="core.deduction",
                source_record_id=row["deduction_id"],
                payload={
                    "employer_id": row["employer_id"],
                    "cycle": row["cycle"],
                    "expected_amount": str(row["expected_amount"]),
                    **({"received_amount": str(row["received_amount"])} if received else {}),
                    **({"net_salary": str(row["net_salary"])} if row.get("net_salary") is not None else {}),
                },
                # a missed cycle is asserted from the absence of a record, so it is
                # marked as such rather than presented as an observation
                data_quality=_quality(1.0 if received else 0.9, "PASS" if received else "WARN"),
                permitted_uses=_CREDIT_USES,
            )
        )

    for row in savings or []:
        events.append(
            MemberEvent(
                event_id=_event_id("sav", member_id, str(row["as_of"])),
                member_id=member_id,
                account_id=None,
                occurred_at=_at(row["as_of"]),
                event_type="SAVINGS_BALANCE",
                source_system="core.savings",
                source_record_id=f"{member_id}:{row['as_of']}",
                payload={"balance": str(row["balance"])},
                data_quality=_quality(),
                permitted_uses=_CREDIT_USES,
            )
        )

    for row in shares or []:
        events.append(
            MemberEvent(
                event_id=_event_id("shr", member_id, str(row["as_of"])),
                member_id=member_id,
                account_id=None,
                occurred_at=_at(row["as_of"]),
                event_type="SHARE_CAPITAL",
                source_system="core.share_capital",
                source_record_id=f"{member_id}:{row['as_of']}",
                payload={"units": row["units"], "value": str(row["value"])},
                data_quality=_quality(),
                permitted_uses=_CREDIT_USES,
            )
        )

    for row in arrangements or []:
        events.append(
            MemberEvent(
                event_id=_event_id("arr", row["arrangement_id"]),
                member_id=member_id,
                account_id=row["account_id"],
                occurred_at=_at(row["from_date"]),
                event_type="ARRANGEMENT_ACTIVE",
                source_system="core.arrangement",
                source_record_id=row["arrangement_id"],
                payload={
                    "arrangement_id": row["arrangement_id"],
                    "type": row["type"],
                    "from": str(row["from_date"]),
                    "to": str(row.get("to_date") or ""),
                },
                data_quality=_quality(),
                permitted_uses=_BASE_USES,
            )
        )

    # A payment made on its due date happened after the amount fell due, so at
    # an identical timestamp the due event comes first.
    order = {"PAYMENT_DUE": 0}
    return sorted(
        events,
        key=lambda e: (e.occurred_at, order.get(e.event_type, 1), e.event_id),
    )
