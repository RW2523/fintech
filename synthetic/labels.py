"""Outcome labels per account-month (docs/10 §5).

These are what the credit-risk and early-warning models are trained against, so
their definitions matter more than almost anything else here. They are derived
from the payment history alone, never from the archetype that generated it:
a label the generator handed out would teach a model nothing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

__all__ = [
    "CHARGE_OFF_DAYS",
    "DPD_THRESHOLDS",
    "AccountOutcome",
    "MonthLabel",
    "days_late",
    "label_accounts",
    "summarise_labels",
]

#: Days past due at which each label turns on.
DPD_THRESHOLDS = {"late7": 7, "late30": 30, "late60": 60, "late90": 90}
#: Past this, the balance is written off rather than chased (docs/10 §5).
CHARGE_OFF_DAYS = 120

#: An unpaid due event is treated as this many days late, so it labels as a
#: charge-off rather than silently disappearing from the training data.
_UNPAID_DPD = 999


@dataclass(frozen=True, slots=True)
class MonthLabel:
    """One row of `core.outcome`."""

    account_id: str
    month: date
    late7: bool = False
    late30: bool = False
    late60: bool = False
    late90: bool = False
    cure: bool = False
    restructure: bool = False
    charge_off: bool = False

    def as_row(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "month": self.month.isoformat(),
            "late7": self.late7,
            "late30": self.late30,
            "late60": self.late60,
            "late90": self.late90,
            "cure": self.cure,
            "restructure": self.restructure,
            "charge_off": self.charge_off,
        }


@dataclass(frozen=True, slots=True)
class AccountOutcome:
    account_id: str
    months: tuple[MonthLabel, ...]
    first_late_date: date | None


def days_late(due_date: date, paid_at: str | datetime | None) -> int:
    """Days between the due date and payment. Unpaid counts as far past due."""
    if paid_at is None:
        return _UNPAID_DPD
    if isinstance(paid_at, str):
        paid = datetime.fromisoformat(paid_at.replace("Z", "+00:00")).date()
    else:
        paid = paid_at.date()
    return (paid - due_date).days


def _month_of(value: date) -> date:
    return date(value.year, value.month, 1)


def label_accounts(
    schedules: list[dict[str, Any]],
    payments: list[dict[str, Any]],
    arrangements: list[dict[str, Any]] | None = None,
) -> list[AccountOutcome]:
    """Derive every account's monthly labels from its payment history."""
    paid_by_schedule = {p["schedule_id"]: p.get("paid_at") for p in payments}

    by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in schedules:
        by_account[row["account_id"]].append(row)

    restructure_months: dict[str, set[date]] = defaultdict(set)
    for row in arrangements or []:
        restructure_months[row["account_id"]].add(_month_of(date.fromisoformat(row["from_date"])))

    outcomes: list[AccountOutcome] = []

    for account_id, rows in by_account.items():
        rows = sorted(rows, key=lambda r: r["due_date"])

        # worst days-late seen for each due event, grouped by its month
        by_month: dict[date, list[int]] = defaultdict(list)
        for row in rows:
            due = date.fromisoformat(row["due_date"])
            by_month[_month_of(due)].append(days_late(due, paid_by_schedule.get(row["schedule_id"])))

        months: list[MonthLabel] = []
        first_late: date | None = None
        has_been_late = False

        for month in sorted(by_month):
            lateness = by_month[month]
            worst = max(lateness)

            late7 = worst >= DPD_THRESHOLDS["late7"]
            if late7 and first_late is None:
                first_late = month

            # A cure is a month where an account that had been late pays every
            # due event on time again.
            cured = has_been_late and all(d <= 0 for d in lateness)

            months.append(
                MonthLabel(
                    account_id=account_id,
                    month=month,
                    late7=late7,
                    late30=worst >= DPD_THRESHOLDS["late30"],
                    late60=worst >= DPD_THRESHOLDS["late60"],
                    late90=worst >= DPD_THRESHOLDS["late90"],
                    cure=cured,
                    restructure=month in restructure_months.get(account_id, set()),
                    charge_off=worst >= CHARGE_OFF_DAYS,
                )
            )

            if late7:
                has_been_late = True

        outcomes.append(
            AccountOutcome(account_id=account_id, months=tuple(months), first_late_date=first_late)
        )

    return sorted(outcomes, key=lambda o: o.account_id)


def summarise_labels(outcomes: list[AccountOutcome]) -> dict[str, Any]:
    """Counts an operator can eyeball before training anything on them."""
    total = sum(len(o.months) for o in outcomes)
    if total == 0:
        return {"account_months": 0, "accounts": len(outcomes), "rates": {}}

    counts: dict[str, int] = defaultdict(int)
    for outcome in outcomes:
        for month in outcome.months:
            for label in ("late7", "late30", "late60", "late90", "cure", "restructure", "charge_off"):
                counts[label] += int(getattr(month, label))

    return {
        "accounts": len(outcomes),
        "account_months": total,
        "accounts_ever_late": sum(1 for o in outcomes if o.first_late_date is not None),
        "counts": dict(sorted(counts.items())),
        "rates": {k: round(v / total, 4) for k, v in sorted(counts.items())},
    }
