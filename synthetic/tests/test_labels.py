"""T-021 — outcome labels, on hand-built payment sequences (docs/10 §5).

These labels are what the credit-risk and early-warning models learn from, so
each one is pinned to an explicit sequence rather than inferred from a large
population where a mistake would hide in the aggregate.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from synthetic.labels import (
    CHARGE_OFF_DAYS,
    DPD_THRESHOLDS,
    days_late,
    label_accounts,
    summarise_labels,
)

ACCOUNT = "A-000001"
FIRST_DUE = date(2025, 1, 15)


def sequence(*lateness: int | None, account_id: str = ACCOUNT) -> tuple[list, list]:
    """Build one due event per month, each paid ``lateness`` days after its date.

    ``None`` means the event was never paid.
    """
    schedules: list[dict[str, Any]] = []
    payments: list[dict[str, Any]] = []

    for index, days in enumerate(lateness):
        month = FIRST_DUE.month - 1 + index
        due = date(FIRST_DUE.year + month // 12, month % 12 + 1, FIRST_DUE.day)
        schedule_id = f"S-{account_id}-{index + 1:03d}"
        schedules.append(
            {
                "schedule_id": schedule_id,
                "account_id": account_id,
                "seq": index + 1,
                "due_date": due.isoformat(),
                "amount_due": "500.00",
            }
        )
        if days is not None:
            paid = due + timedelta(days=days)
            payments.append(
                {
                    "payment_id": f"P-{index + 1:03d}",
                    "schedule_id": schedule_id,
                    "paid_at": f"{paid.isoformat()}T09:00:00Z",
                    "amount_paid": "500.00",
                }
            )

    return schedules, payments


def labels_for(*lateness: int | None, arrangements: list | None = None) -> list:
    schedules, payments = sequence(*lateness)
    outcomes = label_accounts(schedules, payments, arrangements)
    assert len(outcomes) == 1
    return list(outcomes[0].months)


# ---------------------------------------------------------------------------
# days_late
# ---------------------------------------------------------------------------
def test_an_early_payment_is_negative_days_late() -> None:
    assert days_late(date(2025, 1, 15), "2025-01-13T09:00:00Z") == -2


def test_an_on_time_payment_is_zero() -> None:
    assert days_late(date(2025, 1, 15), "2025-01-15T09:00:00Z") == 0


def test_an_unpaid_event_counts_as_far_past_due() -> None:
    """It must label as a charge-off, not vanish from the training data."""
    assert days_late(date(2025, 1, 15), None) > CHARGE_OFF_DAYS


# ---------------------------------------------------------------------------
# the DPD ladder
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "days,expected",
    [
        (-3, set()),
        (0, set()),
        (6, set()),
        (7, {"late7"}),
        (29, {"late7"}),
        (30, {"late7", "late30"}),
        (59, {"late7", "late30"}),
        (60, {"late7", "late30", "late60"}),
        (89, {"late7", "late30", "late60"}),
        (90, {"late7", "late30", "late60", "late90"}),
        (119, {"late7", "late30", "late60", "late90"}),
        (120, {"late7", "late30", "late60", "late90", "charge_off"}),
    ],
)
def test_each_threshold_turns_on_at_its_boundary(days: int, expected: set[str]) -> None:
    month = labels_for(days)[0]
    on = {name for name in ("late7", "late30", "late60", "late90", "charge_off") if getattr(month, name)}
    assert on == expected


def test_the_ladder_is_monotonic() -> None:
    """A worse label can never be on while a milder one is off."""
    for days in range(-5, 130):
        month = labels_for(days)[0]
        assert not (month.late30 and not month.late7)
        assert not (month.late60 and not month.late30)
        assert not (month.late90 and not month.late60)
        assert not (month.charge_off and not month.late90)


def test_the_worst_event_in_a_month_decides_it() -> None:
    """Two due events in one month: the worse one sets the label."""
    schedules, payments = sequence(0)
    schedules.append(
        {
            "schedule_id": "S-extra",
            "account_id": ACCOUNT,
            "seq": 99,
            "due_date": FIRST_DUE.isoformat(),
            "amount_due": "500.00",
        }
    )
    payments.append(
        {
            "payment_id": "P-extra",
            "schedule_id": "S-extra",
            "paid_at": "2025-03-01T09:00:00Z",
            "amount_paid": "500.00",
        }
    )

    month = label_accounts(schedules, payments)[0].months[0]
    assert month.late30 is True


# ---------------------------------------------------------------------------
# cure
# ---------------------------------------------------------------------------
def test_a_cure_needs_a_prior_late_month() -> None:
    """An account that was never late cannot cure."""
    assert all(not m.cure for m in labels_for(0, 0, 0))


def test_paying_on_time_after_a_late_month_is_a_cure() -> None:
    months = labels_for(0, 20, 0, 0)
    assert [m.cure for m in months] == [False, False, True, True]


def test_a_still_late_month_is_not_a_cure() -> None:
    months = labels_for(20, 15, 0)
    assert [m.cure for m in months] == [False, False, True]


def test_paying_early_still_counts_as_a_cure() -> None:
    months = labels_for(20, -3)
    assert months[1].cure is True


def test_a_month_that_is_only_slightly_late_does_not_cure() -> None:
    """Six days late is not late enough to be an event, nor good enough to cure."""
    months = labels_for(20, 6)
    assert months[1].late7 is False
    assert months[1].cure is False, "still past due, so not a return to zero DPD"


# ---------------------------------------------------------------------------
# restructure
# ---------------------------------------------------------------------------
def test_a_restructure_labels_the_month_it_starts() -> None:
    arrangements = [
        {
            "arrangement_id": "AR-1",
            "account_id": ACCOUNT,
            "type": "DEFERMENT_1M",
            "from_date": "2025-02-01",
            "to_date": "2025-05-01",
        }
    ]
    months = labels_for(0, 0, 0, arrangements=arrangements)
    assert [m.restructure for m in months] == [False, True, False]


def test_an_arrangement_on_another_account_is_ignored() -> None:
    arrangements = [
        {
            "arrangement_id": "AR-1",
            "account_id": "A-999999",
            "type": "DEFERMENT_1M",
            "from_date": "2025-02-01",
            "to_date": "2025-05-01",
        }
    ]
    assert all(not m.restructure for m in labels_for(0, 0, 0, arrangements=arrangements))


# ---------------------------------------------------------------------------
# first late date and shape
# ---------------------------------------------------------------------------
def test_the_first_late_date_is_the_first_month_past_a_week() -> None:
    schedules, payments = sequence(0, 3, 9, 40)
    outcome = label_accounts(schedules, payments)[0]
    assert outcome.first_late_date == date(2025, 3, 1)


def test_an_account_that_never_goes_late_has_no_first_late_date() -> None:
    schedules, payments = sequence(0, -1, 2, 5)
    assert label_accounts(schedules, payments)[0].first_late_date is None


def test_one_row_per_account_month() -> None:
    schedules, payments = sequence(*([0] * 12))
    months = label_accounts(schedules, payments)[0].months
    assert len(months) == 12
    assert len({m.month for m in months}) == 12


def test_months_are_returned_in_order() -> None:
    schedules, payments = sequence(0, 0, 0, 0, 0)
    months = label_accounts(schedules, payments)[0].months
    assert list(months) == sorted(months, key=lambda m: m.month)


def test_accounts_are_labelled_independently() -> None:
    first_s, first_p = sequence(0, 0, account_id="A-000001")
    second_s, second_p = sequence(45, 45, account_id="A-000002")
    outcomes = label_accounts(first_s + second_s, first_p + second_p)

    assert [o.account_id for o in outcomes] == ["A-000001", "A-000002"]
    assert not any(m.late30 for m in outcomes[0].months)
    assert all(m.late30 for m in outcomes[1].months)


def test_a_row_serialises_for_the_core_outcome_table() -> None:
    row = labels_for(40)[0].as_row()
    assert set(row) == {
        "account_id",
        "month",
        "late7",
        "late30",
        "late60",
        "late90",
        "cure",
        "restructure",
        "charge_off",
    }
    assert row["month"] == "2025-01-01"


# ---------------------------------------------------------------------------
# the summary an operator reads before training on these
# ---------------------------------------------------------------------------
def test_the_summary_counts_every_label() -> None:
    schedules, payments = sequence(0, 20, 0, 45, 0)
    summary = summarise_labels(label_accounts(schedules, payments))

    assert summary["accounts"] == 1
    assert summary["account_months"] == 5
    assert summary["accounts_ever_late"] == 1
    assert set(summary["counts"]) == {
        "late7",
        "late30",
        "late60",
        "late90",
        "cure",
        "restructure",
        "charge_off",
    }
    assert summary["counts"]["late7"] == 2
    assert summary["counts"]["late30"] == 1
    assert summary["rates"]["late7"] == 0.4


def test_the_summary_of_nothing_is_empty_rather_than_an_error() -> None:
    assert summarise_labels([])["account_months"] == 0


def test_the_thresholds_match_the_specification() -> None:
    assert DPD_THRESHOLDS == {"late7": 7, "late30": 30, "late60": 60, "late90": 90}
    assert CHARGE_OFF_DAYS == 120
