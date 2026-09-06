"""T-060 — the corroborating feature families (docs/07 §4.2).

One family moving is noise often enough that acting on it teaches officers to
ignore alerts. Two families moving together is a member whose circumstances
changed. These tests are about telling those apart.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ml.lmi.families import (
    Deduction,
    Interaction,
    SavingsPoint,
    baselines_for,
    capacity_features,
    deduction_features,
    departures,
    interaction_features,
    savings_features,
)

TODAY = date(2026, 9, 6)


def cycles(actuals: list[float], *, expected: float = 250.0) -> list[Deduction]:
    return [
        Deduction(
            cycle=TODAY - timedelta(days=30 * (len(actuals) - index)),
            expected=expected,
            actual=actual,
            employer_id="E-01",
        )
        for index, actual in enumerate(actuals)
    ]


# ---------------------------------------------------------------------------
# deduction integrity
# ---------------------------------------------------------------------------
def test_a_short_deduction_is_not_a_missed_one() -> None:
    """The first is usually a pay change and the second is usually the
    employer's file, and an alert that cannot tell them apart is wrong about
    half the time."""
    assert not Deduction(TODAY, 250.0, 180.0).missed
    assert Deduction(TODAY, 250.0, 180.0).shortfall == 70.0
    assert Deduction(TODAY, 250.0, 0.0).missed


def test_missed_cycles_are_counted_over_ninety_days() -> None:
    features = deduction_features(cycles([250, 250, 0, 0]), as_of=TODAY)
    assert features["deduction_missed_count_90d"] == 2
    assert features["deduction_amount_delta_90d"] == 500.0


def test_a_pay_cut_shows_as_a_level_change() -> None:
    features = deduction_features(cycles([250, 250, 250, 250, 200, 200, 200]), as_of=TODAY)
    assert features["deduction_level_change"] < 0


def test_an_employers_missed_cycle_is_not_the_members_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treating it as one raises an alert on everybody at that employer at
    once, which is the fastest way to make the platform useless."""
    missed = cycles([250, 0])
    cycle = missed[-1].cycle

    theirs = deduction_features(missed, as_of=TODAY, employer_cycles={cycle: 0.55})
    assert theirs["employer_gap_flag"] == 1.0

    ours = deduction_features(missed, as_of=TODAY, employer_cycles={cycle: 0.02})
    assert ours["employer_gap_flag"] == 0.0


# ---------------------------------------------------------------------------
# savings
# ---------------------------------------------------------------------------
def points(balances: list[float], contributions: list[float]) -> list[SavingsPoint]:
    return [
        SavingsPoint(
            balance=balance,
            at=TODAY - timedelta(days=30 * (len(balances) - index)),
            contribution=contribution,
        )
        for index, (balance, contribution) in enumerate(zip(balances, contributions, strict=True))
    ]


def test_a_flattening_balance_is_visible_before_anything_is_late() -> None:
    """A member under pressure protects the instalment and gives up the
    savings, so the balance flattens months before a payment slips."""
    saving = points([1000, 1100, 1200, 1300, 1400], [100] * 5)
    stalled = points([1400, 1400, 1400, 1400, 1400], [0] * 5)

    assert savings_features(saving, as_of=TODAY)["savings_slope_180d"] > 0
    assert savings_features(stalled, as_of=TODAY)["savings_slope_180d"] == 0
    assert savings_features(stalled, as_of=TODAY)["savings_paused_months"] == 5


def test_a_member_who_resumed_saving_is_not_paused() -> None:
    resumed = points([1400, 1400, 1400, 1500], [0, 0, 0, 100])
    assert savings_features(resumed, as_of=TODAY)["savings_paused_months"] == 0


def test_no_savings_history_is_not_a_falling_balance() -> None:
    assert savings_features([], as_of=TODAY)["savings_slope_180d"] == 0.0


# ---------------------------------------------------------------------------
# capacity
# ---------------------------------------------------------------------------
def test_a_rising_commitment_ratio_shows_as_a_trend() -> None:
    history = [(TODAY - timedelta(days=150 - 30 * i), 0.30 + 0.03 * i) for i in range(5)]
    assert capacity_features(dsr_history=history, as_of=TODAY)["dsr_trend_180d"] > 0


def test_new_obligations_are_carried_through() -> None:
    features = capacity_features(dsr_history=[], as_of=TODAY, new_obligations_6m=2)
    assert features["new_obligations_6m"] == 2.0


# ---------------------------------------------------------------------------
# interaction
# ---------------------------------------------------------------------------
def test_a_member_who_stops_answering_shows_a_falling_response_rate() -> None:
    contacts = [
        Interaction(at=TODAY - timedelta(days=200), kind="CALL", responded=True),
        Interaction(at=TODAY - timedelta(days=100), kind="CALL", responded=True),
        Interaction(at=TODAY - timedelta(days=30), kind="CALL", responded=False),
        Interaction(at=TODAY - timedelta(days=10), kind="CALL", responded=False),
    ]
    assert interaction_features(contacts, as_of=TODAY)["contact_response_rate"] == 0.5


def test_promises_are_only_scored_once_they_are_due() -> None:
    """A promise nobody has checked yet is not a broken promise."""
    made = [
        Interaction(at=TODAY, kind="CALL", responded=True, promise_made=True, promise_kept=None),
        Interaction(
            at=TODAY - timedelta(days=40),
            kind="CALL",
            responded=True,
            promise_made=True,
            promise_kept=True,
        ),
    ]
    assert interaction_features(made, as_of=TODAY)["promise_kept_rate"] == 1.0


def test_no_contact_is_not_a_zero_response_rate_by_accident() -> None:
    assert interaction_features([], as_of=TODAY)["contact_response_rate"] == 0.0


# ---------------------------------------------------------------------------
# departures
# ---------------------------------------------------------------------------
def test_only_signals_with_a_habit_are_reported_as_departures() -> None:
    """A feature nobody has a history for is a number, not a departure, and
    giving it a z of zero would read as "normal for them" when nothing is
    known at all."""
    baselines = baselines_for(
        {"days_to_pay_median_30d": [0, 0, 1, 0, 0, 0, 0], "savings_slope_180d": [1.0, 2.0]}
    )
    found = departures({"days_to_pay_median_30d": 6.0, "savings_slope_180d": -5.0, "unknown": 3.0}, baselines)
    assert "days_to_pay_median_30d_robust_z" in found
    assert "savings_slope_180d_robust_z" not in found, "two points is not a habit"
    assert "unknown_robust_z" not in found
