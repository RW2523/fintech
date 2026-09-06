"""T-060 — temporal features and personal baselines (docs/07 §4.2).

Every test here builds a member's behaviour by hand, so the arithmetic can be
checked against a series somebody can read. The question these features answer
is not "does this member look risky" but "does this member look like
themselves", and the tests are written to that.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ml.lmi.temporal import (
    Baseline,
    DueEvent,
    baseline_of,
    days_to_pay,
    late_streak,
    recovery_features,
    robust_z,
    slope,
    window_features,
)

TODAY = date(2026, 9, 6)


def monthly(timings: list[int | None], *, start: date = date(2025, 9, 1)) -> list[DueEvent]:
    """One instalment a month, paid `timings[i]` days after it fell due.

    None means it was never paid, which is deliberately different from a large
    positive number.
    """
    events = []
    for index, timing in enumerate(timings):
        due = start + timedelta(days=30 * index)
        events.append(
            DueEvent(
                due_date=due,
                amount_due=250.0,
                paid_at=None if timing is None else due + timedelta(days=timing),
                amount_paid=0.0 if timing is None else 250.0,
            )
        )
    return events


# ---------------------------------------------------------------------------
# one due event
# ---------------------------------------------------------------------------
def test_early_is_negative_and_late_is_positive() -> None:
    due = date(2026, 9, 1)
    assert DueEvent(due, 250.0, due - timedelta(days=2), 250.0).days_to_pay == -2
    assert DueEvent(due, 250.0, due + timedelta(days=5), 250.0).days_to_pay == 5


def test_unpaid_is_not_very_late() -> None:
    """A due event with no payment against it is a different fact from one paid
    sixty days on, and averaging the two lets an unpaid instalment look like
    slowness."""
    unpaid = DueEvent(date(2026, 9, 1), 250.0)
    assert unpaid.days_to_pay is None
    assert days_to_pay([unpaid]) == []


def test_a_shortfall_is_what_was_not_paid() -> None:
    event = DueEvent(date(2026, 9, 1), 250.0, date(2026, 9, 1), 100.0)
    assert event.shortfall == 150.0
    assert DueEvent(date(2026, 9, 1), 250.0, date(2026, 9, 1), 300.0).shortfall == 0.0


# ---------------------------------------------------------------------------
# the personal baseline
# ---------------------------------------------------------------------------
def test_a_baseline_is_the_members_own_habit() -> None:
    baseline = baseline_of("days_to_pay", [3, 3, 4, 3, 2, 3, 3])
    assert baseline.median == 3
    assert baseline.usable


def test_a_short_history_is_not_a_habit() -> None:
    """Fewer than six observations and the median is an opinion. Reporting a
    robust z off three payments would flag every new member on their fourth."""
    baseline = baseline_of("days_to_pay", [0, 0, 12])
    assert not baseline.usable
    assert robust_z(30.0, baseline) == 0.0


def test_a_member_who_always_pays_three_days_late_has_not_changed() -> None:
    """The absolute value is rarely the signal. The departure is."""
    habitual = baseline_of("days_to_pay", [3, 3, 3, 4, 3, 2, 3, 3])
    assert abs(robust_z(3.0, habitual)) < 1.0


def test_a_member_who_always_pays_on_the_day_registers_a_move_at_two_days() -> None:
    """One deviation, not several. Payment timing is recorded in whole days, so
    a member whose habit varies by a day has no resolution below that: without
    a floor on the scale, a two-day swing reads as a large departure and the
    detector spends its time measuring rounding."""
    punctual = baseline_of("days_to_pay", [0, 0, 0, 1, 0, 0, 0, 0])
    assert robust_z(2.0, punctual) == 1.0
    assert robust_z(8.0, punctual) == 4.0


def test_the_floor_only_ever_makes_a_departure_smaller() -> None:
    """A member whose habit varies more than the floor keeps their own scale."""
    varied = baseline_of("days_to_pay", [0, 6, 0, 7, 0, 6, 1, 7])
    assert varied.scale > 2.0
    assert (
        abs(robust_z(9.0, varied))
        < abs(robust_z(9.0, baseline_of("days_to_pay", [0, 6, 0, 7, 0, 6, 1, 7], min_scale=0.0)))
        or varied.scale == baseline_of("days_to_pay", [0, 6, 0, 7, 0, 6, 1, 7], min_scale=0.0).scale
    )


def test_a_member_with_no_variation_does_not_divide_by_zero() -> None:
    """A member who paid on exactly the same day for a year has a MAD of zero,
    and the first day they are late would otherwise be infinitely surprising."""
    unvarying = baseline_of("days_to_pay", [0] * 12)
    value = robust_z(1.0, unvarying)
    assert value > 0
    assert value < float("inf")


# ---------------------------------------------------------------------------
# windows
# ---------------------------------------------------------------------------
def test_a_window_with_no_due_event_reports_nothing() -> None:
    """A member with no instalment due this week has not paid on time this
    week, and recording that as perfect punctuality is how a quiet month looks
    like an improvement."""
    features = window_features(monthly([0, 0, 0]), as_of=TODAY)
    assert "days_to_pay_median_7d" not in features
    assert "days_to_pay_median_365d" in features


def test_the_median_and_the_tail_are_both_reported() -> None:
    """A member who is usually on time and occasionally thirty days late is a
    different case from one who is always five days late, and a median alone
    cannot tell them apart."""
    features = window_features(monthly([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 30]), as_of=TODAY)
    assert features["days_to_pay_median_365d"] == 0
    assert features["days_late_p95_365d"] > 10


def test_an_unpaid_event_is_counted_separately() -> None:
    features = window_features(monthly([0, 0, None, 0]), as_of=TODAY)
    assert features["unpaid_365d"] == 1.0


def test_the_streak_is_what_is_happening_not_what_happened() -> None:
    """A member who was late four times last year and has paid on time since
    has a streak of zero."""
    assert late_streak(monthly([5, 5, 5, 5, 0, 0, 0])) == 0
    assert late_streak(monthly([0, 0, 0, 5, 5])) == 2


def test_an_unpaid_event_continues_the_streak() -> None:
    assert late_streak(monthly([0, 0, 3, None])) == 2


def test_the_trend_runs_over_due_events_not_calendar_days() -> None:
    """A member with monthly instalments has twelve points a year, and a
    regression on the date axis would be dominated by the gaps."""
    drifting = monthly([0, 1, 2, 3, 4, 5], start=TODAY - timedelta(days=170))
    features = window_features(drifting, as_of=TODAY)
    assert features["due_to_pay_slope_180d"] == pytest.approx(1.0, abs=0.01)


def test_a_steady_payer_has_no_trend() -> None:
    steady = monthly([2, 2, 2, 2, 2, 2], start=TODAY - timedelta(days=170))
    assert window_features(steady, as_of=TODAY)["due_to_pay_slope_180d"] == 0.0


# ---------------------------------------------------------------------------
# slope
# ---------------------------------------------------------------------------
def test_a_slope_over_one_point_is_zero_not_an_error() -> None:
    assert slope([(0.0, 5.0)]) == 0.0
    assert slope([]) == 0.0


def test_a_slope_over_a_vertical_series_is_zero() -> None:
    """Every point at the same x carries no information about a direction."""
    assert slope([(1.0, 3.0), (1.0, 9.0)]) == 0.0


# ---------------------------------------------------------------------------
# recovery
# ---------------------------------------------------------------------------
def test_no_alert_means_nothing_to_recover_from() -> None:
    features = recovery_features(monthly([0, 0]), alert_at=None, baseline=Baseline("x", 0, 1, 12))
    assert features["risk_decay"] == 1.0


def test_paying_on_time_after_an_alert_decays_the_concern() -> None:
    """A platform that raises concerns and never withdraws them teaches people
    to ignore it."""
    events = monthly([5, 5, 0, 0, 0, 0], start=TODAY - timedelta(days=170))
    features = recovery_features(
        events, alert_at=TODAY - timedelta(days=120), baseline=Baseline("x", 0, 1, 12)
    )
    assert features["consecutive_on_time_since_alert"] == 4
    assert features["risk_decay"] < 0.6


def test_slipping_again_resets_the_recovery() -> None:
    events = monthly([0, 0, 0, 6], start=TODAY - timedelta(days=110))
    features = recovery_features(
        events, alert_at=TODAY - timedelta(days=120), baseline=Baseline("x", 0, 1, 12)
    )
    assert features["consecutive_on_time_since_alert"] == 0
    assert features["risk_decay"] == 1.0


def test_a_departure_from_no_variation_is_capped_not_astronomical() -> None:
    """A member whose shortfall has been zero every cycle has a MAD of zero,
    and dividing by the epsilon turns a 450 unit shortfall into billions. The
    number has no meaning at that size; ten robust deviations already says
    "unlike anything this member has done"."""
    from ml.lmi.temporal import MAX_Z

    unvarying = baseline_of("deduction_amount_delta_90d", [0.0] * 24)
    assert robust_z(453.05, unvarying) == MAX_Z
    assert robust_z(-453.05, unvarying) == -MAX_Z


def test_an_ordinary_departure_is_not_capped() -> None:
    """The cap must not flatten the range people actually read."""
    from ml.lmi.temporal import MAX_Z

    ordinary = baseline_of("days_to_pay", [0, 1, 0, 2, 0, 1, 0, 1])
    value = robust_z(4.0, ordinary)
    assert 0 < value < MAX_Z
