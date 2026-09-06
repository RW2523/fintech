"""T-025 — deriving events from core records (docs/03 §9, docs/07 §4.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.events import EVENT_TYPES, LATE_THRESHOLD_DAYS, days_late, derive_events

SCHEDULE = {"schedule_id": "S-1", "account_id": "A-1", "due_date": "2026-04-05", "amount_due": "500.00"}


def paid(days: int, amount: str = "500.00") -> dict:
    due = datetime(2026, 4, 5, 9, tzinfo=UTC)
    return {
        "payment_id": "P-1",
        "schedule_id": "S-1",
        "paid_at": due + timedelta(days=days),
        "amount_paid": amount,
    }


def types_of(**kwargs: object) -> list[str]:
    return [e.event_type for e in derive_events(member_id="M-000042", **kwargs)]


def test_a_due_event_is_always_produced() -> None:
    assert types_of(schedules=[SCHEDULE]) == ["PAYMENT_DUE"]


def test_an_on_time_payment_is_received() -> None:
    assert types_of(schedules=[SCHEDULE], payments=[paid(0)]) == ["PAYMENT_DUE", "PAYMENT_RECEIVED"]


def test_a_slightly_slow_payment_is_still_received() -> None:
    assert "PAYMENT_LATE" not in types_of(schedules=[SCHEDULE], payments=[paid(LATE_THRESHOLD_DAYS)])


def test_a_late_payment_is_marked_late() -> None:
    assert "PAYMENT_LATE" in types_of(schedules=[SCHEDULE], payments=[paid(LATE_THRESHOLD_DAYS + 1)])


def test_a_short_payment_is_partial_whatever_its_timing() -> None:
    assert "PAYMENT_PARTIAL" in types_of(schedules=[SCHEDULE], payments=[paid(0, "300.00")])
    assert "PAYMENT_PARTIAL" in types_of(schedules=[SCHEDULE], payments=[paid(20, "300.00")])


def test_a_missed_deduction_is_marked_and_flagged_as_inferred() -> None:
    """It is asserted from an absence, so it is not presented as an observation."""
    events = derive_events(
        member_id="M-000042",
        deductions=[
            {
                "deduction_id": "D-1",
                "employer_id": "E-1",
                "cycle": "2026-04",
                "expected_amount": "125.00",
                "received_amount": None,
                "received_at": None,
            }
        ],
    )
    assert events[0].event_type == "DEDUCTION_MISSED"
    assert events[0].data_quality["validation"] == "WARN"
    assert events[0].data_quality["confidence"] < 1.0


def test_a_received_deduction_carries_the_reported_net() -> None:
    events = derive_events(
        member_id="M-000042",
        deductions=[
            {
                "deduction_id": "D-1",
                "employer_id": "E-1",
                "cycle": "2026-04",
                "expected_amount": "125.00",
                "received_amount": "125.00",
                "received_at": datetime(2026, 4, 28, tzinfo=UTC),
                "net_salary": "2400.00",
            }
        ],
    )
    assert events[0].event_type == "DEDUCTION_RECEIVED"
    assert events[0].payload["net_salary"] == "2400.00"


def test_events_are_returned_in_time_order() -> None:
    events = derive_events(
        member_id="M-000042",
        schedules=[SCHEDULE],
        payments=[paid(20)],
        savings=[{"member_id": "M-000042", "as_of": "2026-03-31", "balance": "900.00"}],
    )
    assert [e.occurred_at for e in events] == sorted(e.occurred_at for e in events)


def test_event_ids_are_deterministic() -> None:
    """A replayed import must update rather than duplicate."""
    first = derive_events(member_id="M-000042", schedules=[SCHEDULE])
    second = derive_events(member_id="M-000042", schedules=[SCHEDULE])
    assert [e.event_id for e in first] == [e.event_id for e in second]


def test_every_event_declares_its_permitted_uses() -> None:
    """CLAUDE.md §2.5 — a tool masks by purpose, so events must carry one."""
    events = derive_events(
        member_id="M-000042",
        schedules=[SCHEDULE],
        payments=[paid(0)],
        savings=[{"member_id": "M-000042", "as_of": "2026-03-31", "balance": "900.00"}],
        shares=[{"member_id": "M-000042", "as_of": "2026-03-31", "units": 150, "value": "1500.00"}],
    )
    assert all(e.permitted_uses for e in events)
    assert all(
        set(e.permitted_uses) <= {"UNDERWRITING", "SERVICING", "COLLECTIONS", "FRAUD", "ANALYTICS"}
        for e in events
    )


def test_every_event_names_its_source_record() -> None:
    events = derive_events(member_id="M-000042", schedules=[SCHEDULE], payments=[paid(0)])
    assert all(e.source_system and e.source_record_id for e in events)


def test_event_types_stay_inside_the_contract() -> None:
    import cio_contracts

    allowed = set(cio_contracts.bundle()["$defs"]["MemberEvent"]["properties"]["event_type"]["enum"])
    assert set(EVENT_TYPES) <= allowed


@pytest.mark.parametrize(
    "due,paid_at,expected",
    [
        ("2026-04-05", "2026-04-05T09:00:00Z", 0),
        ("2026-04-05", "2026-04-01T09:00:00Z", -4),
        ("2026-04-05", "2026-04-20T09:00:00Z", 15),
        ("2026-04-05", None, None),
    ],
)
def test_days_late_measures_from_the_due_date(due: str, paid_at: str | None, expected: int | None) -> None:
    assert days_late(due, paid_at) == expected
