"""T-063 — alert hygiene (docs/07 §4.7).

An early-warning engine that raises everything it notices is an engine nobody
reads. Every rule here exists to protect an officer's attention.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.alerts import (
    DEFAULT_CAP_PER_OFFICER,
    Alert,
    close_on_recovery,
    raise_alert,
    rank_value,
    shortlist,
    signal_set,
    why_now,
)

TODAY = date(2026, 9, 6)


def transition(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "member_id": "M-000042",
        "from_state": "WATCH",
        "to_state": "ELEVATED",
        "changed": True,
        "rule": "CONFIRMED_CHANGE_CORROBORATED",
        "reason": "a confirmed change-point and the deduction agrees",
        "corroboration": {"confirms": ["DEDUCTION_IRREGULAR"], "explains": [], "silent": []},
    }
    body.update(overrides)
    return body


FEATURES = {
    "baseline_median": 1.0,
    "days_to_pay_median_30d": 9.0,
    "deduction_missed_count_90d": 2.0,
    "savings_paused_months": 0.0,
    "contact_response_rate": 0.8,
}


# ---------------------------------------------------------------------------
# what is worth raising
# ---------------------------------------------------------------------------
def test_an_escalation_raises_an_alert() -> None:
    alert = raise_alert(transition(), features=FEATURES, exposure=8000.0, p90=0.4, account_id="A-1")
    assert alert is not None
    assert alert.state == "ELEVATED"


def test_watch_does_not_raise_an_alert() -> None:
    """WATCH is the platform paying attention, not asking somebody else to."""
    assert raise_alert(transition(to_state="WATCH"), features=FEATURES, exposure=8000.0, p90=0.4) is None


def test_a_state_that_did_not_change_raises_nothing() -> None:
    assert raise_alert(transition(changed=False), features=FEATURES, exposure=8000.0, p90=0.4) is None


# ---------------------------------------------------------------------------
# deduplication
# ---------------------------------------------------------------------------
def test_the_same_concern_twice_is_one_alert() -> None:
    first = raise_alert(transition(), features=FEATURES, exposure=8000.0, p90=0.4, account_id="A-1")
    second = raise_alert(transition(), features=FEATURES, exposure=8000.0, p90=0.4, account_id="A-1")
    assert first and second
    assert first.alert_id == second.alert_id


def test_a_new_reason_is_a_new_alert() -> None:
    """The new reason is what an officer has not seen."""
    first = raise_alert(transition(), features=FEATURES, exposure=8000.0, p90=0.4, account_id="A-1")
    later = raise_alert(
        transition(),
        features={**FEATURES, "savings_paused_months": 5.0},
        exposure=8000.0,
        p90=0.4,
        account_id="A-1",
    )
    assert first and later
    assert first.alert_id != later.alert_id


def test_one_alert_per_account() -> None:
    one = raise_alert(transition(), features=FEATURES, exposure=8000.0, p90=0.4, account_id="A-1")
    other = raise_alert(transition(), features=FEATURES, exposure=8000.0, p90=0.4, account_id="A-2")
    assert one and other
    assert one.alert_id != other.alert_id


def test_the_signal_set_is_stable_whatever_the_order() -> None:
    assert signal_set(transition(), FEATURES) == signal_set(transition(), FEATURES)


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------
def test_a_bigger_exposure_ranks_higher() -> None:
    small = rank_value(p90=0.4, exposure=1000.0, contact_response_rate=0.2)
    large = rank_value(p90=0.4, exposure=20000.0, contact_response_rate=0.2)
    assert large > small


def test_a_member_who_answers_the_phone_ranks_higher() -> None:
    """Not because they are more at risk: because the intervention can work."""
    unreachable = rank_value(p90=0.4, exposure=8000.0, contact_response_rate=0.1)
    reachable = rank_value(p90=0.4, exposure=8000.0, contact_response_rate=0.9)
    assert reachable > unreachable


def test_a_member_with_no_probability_ranks_last_rather_than_vanishing() -> None:
    """They still appear, at the bottom, where somebody may still look."""
    assert rank_value(p90=None, exposure=50000.0, contact_response_rate=0.9) == 0.0


# ---------------------------------------------------------------------------
# the cap
# ---------------------------------------------------------------------------
def alerts(n: int) -> list[Alert]:
    return [
        Alert(
            alert_id=f"alert_{index:03d}",
            member_id=f"M-{index:06d}",
            account_id=f"A-{index}",
            state="ELEVATED",
            signals=("X",),
            rank_value=float(index),
            why_now="",
            opened_at=TODAY,
        )
        for index in range(n)
    ]


def test_a_day_holds_what_one_officer_can_work_through() -> None:
    listed = shortlist(alerts(100))
    assert len(listed) == DEFAULT_CAP_PER_OFFICER


def test_the_cap_scales_with_the_officers_on_duty() -> None:
    assert len(shortlist(alerts(100), cap=10, officers=3)) == 30


def test_the_highest_value_reaches_the_top() -> None:
    listed = shortlist(alerts(100), cap=3)
    assert [alert.rank_value for alert in listed] == [99.0, 98.0, 97.0]


def test_a_capped_alert_is_deferred_not_dropped() -> None:
    """It stays open and is considered again tomorrow."""
    everything = alerts(100)
    shortlist(everything, cap=3)
    assert all(alert.open for alert in everything)


def test_a_closed_alert_never_reaches_the_queue() -> None:
    everything = alerts(10)
    everything[9].closed_at = TODAY
    assert everything[9] not in shortlist(everything, cap=5)


# ---------------------------------------------------------------------------
# why now
# ---------------------------------------------------------------------------
def test_the_line_says_what_moved_and_by_how_much() -> None:
    """An alert an officer cannot act on without reconstructing the case is an
    alert they leave until tomorrow."""
    line = why_now(
        signal="days to pay",
        baseline=1.0,
        current=9.0,
        days=30,
        change_point="2026-07-15",
        corroboration={"confirms": ["DEDUCTION_IRREGULAR"], "explains": []},
    )
    assert "days to pay moved from 1 to 9 over 30 days" in line
    assert "2026-07-15" in line
    assert "deduction irregular agree" in line


def test_the_line_says_when_nothing_else_moved() -> None:
    line = why_now(
        signal="days to pay",
        baseline=1.0,
        current=9.0,
        days=30,
        change_point=None,
        corroboration={},
    )
    assert "no other signal moved with it" in line


def test_an_explanation_is_named_even_on_an_alert() -> None:
    """An officer needs to know somebody already has a reason."""
    line = why_now(
        signal="days to pay",
        baseline=1.0,
        current=9.0,
        days=30,
        change_point=None,
        corroboration={"explains": ["OUTAGE_WINDOW"]},
    )
    assert "may explain it" in line


# ---------------------------------------------------------------------------
# closing
# ---------------------------------------------------------------------------
def test_returning_to_stable_closes_the_alerts() -> None:
    """A platform that raises concerns and never withdraws them teaches people
    to ignore it."""
    open_alerts = alerts(3)
    for alert in open_alerts:
        alert.member_id = "M-000042"

    closed = close_on_recovery(
        open_alerts,
        transition(from_state="RECOVERY", to_state="STABLE"),
        at=TODAY,
    )
    assert len(closed) == 3
    assert all(not alert.open for alert in open_alerts)


def test_any_other_transition_closes_nothing() -> None:
    open_alerts = alerts(2)
    for alert in open_alerts:
        alert.member_id = "M-000042"
    assert close_on_recovery(open_alerts, transition(), at=TODAY) == []
