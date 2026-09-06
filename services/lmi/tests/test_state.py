"""T-063 — the member state machine (docs/07 §4.5-4.6).

A platform that escalates freely and de-escalates reluctantly ends with
everybody in ELEVATED and nobody reading it. These tests are as much about
what does not happen as about what does.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise
from typing import Any

from app.state import HYSTERESIS_DAYS, Evidence, corroborate, next_state

TODAY = date(2026, 9, 6)


def evidence(**overrides: Any) -> Evidence:
    body: dict[str, Any] = {
        "member_id": "M-000042",
        "as_of": TODAY,
        "features": {
            "days_to_pay_median_30d_robust_z": 0.2,
            "days_to_pay_median_90d_robust_z": 0.1,
            "deduction_missed_count_90d": 0.0,
            "savings_paused_months": 0.0,
            "contact_response_rate": 0.8,
            "contacts_12m": 4.0,
        },
    }
    features = overrides.pop("features", None)
    if features:
        body["features"].update(features)
    body.update(overrides)
    return Evidence(**body)


def confirmed(at: str = "2026-07-15") -> list[dict[str, Any]]:
    return [{"cp_date": at, "signal": "days_to_pay", "stats": {"confirmed": True}}]


# ---------------------------------------------------------------------------
# going up
# ---------------------------------------------------------------------------
def test_a_steady_member_stays_stable() -> None:
    assert next_state("STABLE", evidence()).to_state == "STABLE"


def test_two_departures_reach_watch() -> None:
    move = next_state("STABLE", evidence(departure_events=2))
    assert move.to_state == "WATCH"
    assert move.rule == "DEPARTED_TWICE"


def test_one_departure_is_not_enough() -> None:
    """One reading outside a member's habit is a late payment, not a change."""
    assert next_state("STABLE", evidence(departure_events=1)).to_state == "STABLE"


def test_a_missed_deduction_reaches_watch_on_its_own() -> None:
    """The employer stopped paying, which the member usually learns about when
    the platform does."""
    move = next_state("STABLE", evidence(features={"deduction_missed_count_90d": 1.0}))
    assert move.to_state == "WATCH"
    assert move.rule == "DEDUCTION_MISSED"


def test_watch_becomes_elevated_only_with_a_confirmed_change() -> None:
    """An unconfirmed alarm is worth watching and not worth an officer's
    morning."""
    unconfirmed = next_state("WATCH", evidence(p30=0.4))
    assert unconfirmed.to_state == "WATCH"

    move = next_state("WATCH", evidence(p30=0.4, change_points=confirmed()))
    assert move.to_state == "ELEVATED"
    assert "2026-07-15" in move.reason


def test_a_confirmed_change_without_corroboration_does_not_escalate() -> None:
    """One family moving is noise often enough that acting on it teaches
    officers to ignore alerts."""
    move = next_state("WATCH", evidence(p30=0.05, change_points=confirmed()))
    assert move.to_state == "WATCH"


def test_a_second_family_corroborates_without_a_probability() -> None:
    move = next_state(
        "WATCH",
        evidence(change_points=confirmed(), features={"deduction_missed_count_90d": 2.0}),
    )
    assert move.to_state == "ELEVATED"
    assert "DEDUCTION_IRREGULAR" in move.corroboration.confirms


def test_thirty_days_late_is_critical_from_anywhere() -> None:
    move = next_state("STABLE", evidence(max_days_late=45))
    assert move.to_state == "CRITICAL"
    assert "45 days late" in move.reason


def test_a_high_integrity_finding_is_critical() -> None:
    assert next_state("WATCH", evidence(fraud_level="HIGH")).to_state == "CRITICAL"


def test_a_high_probability_needs_two_families_for_critical() -> None:
    one = next_state("ELEVATED", evidence(p30=0.8, features={"deduction_missed_count_90d": 1.0}))
    assert one.to_state != "CRITICAL"

    two = next_state(
        "ELEVATED",
        evidence(
            p30=0.8,
            features={"deduction_missed_count_90d": 1.0, "savings_paused_months": 4.0},
        ),
    )
    assert two.to_state == "CRITICAL"


# ---------------------------------------------------------------------------
# coming back
# ---------------------------------------------------------------------------
def test_two_on_time_events_and_a_closing_gap_reach_recovery() -> None:
    move = next_state(
        "ELEVATED",
        evidence(consecutive_on_time=2, distance_to_baseline=[3.0, 1.2]),
    )
    assert move.to_state == "RECOVERY"


def test_paying_on_time_without_closing_the_gap_is_not_recovery() -> None:
    """Two payments after a long slip is a pause, not a return."""
    move = next_state(
        "ELEVATED",
        evidence(consecutive_on_time=2, distance_to_baseline=[1.2, 3.0]),
    )
    assert move.to_state != "RECOVERY"


def test_recovery_returns_to_stable_after_three_on_time_events() -> None:
    move = next_state("RECOVERY", evidence(consecutive_on_time=3))
    assert move.to_state == "STABLE"


def test_recovery_holds_while_the_departure_persists() -> None:
    move = next_state(
        "RECOVERY",
        evidence(consecutive_on_time=3, features={"days_to_pay_median_90d_robust_z": 2.5}),
    )
    assert move.to_state == "RECOVERY"


def test_watch_returns_to_stable_when_the_member_settles() -> None:
    move = next_state("WATCH", evidence(consecutive_on_time=2))
    assert move.to_state == "STABLE"


# ---------------------------------------------------------------------------
# being wrong
# ---------------------------------------------------------------------------
def test_an_outage_that_explains_the_lateness_de_escalates() -> None:
    """The platform being wrong is normal. The platform refusing to notice it
    is not."""
    move = next_state("ELEVATED", evidence(outage_covers_deviations=True))
    assert move.to_state == "WATCH"
    assert "OUTAGE_WINDOW" in move.corroboration.explains


def test_an_active_arrangement_de_escalates() -> None:
    move = next_state("CRITICAL", evidence(arrangement_active=True))
    assert move.to_state == "WATCH"


def test_an_employer_gap_explains_rather_than_confirms() -> None:
    """An employer that missed a whole cycle is not a member who stopped
    paying, and the alert belongs to whoever calls the employer."""
    found = corroborate(evidence(employer_gap=True))
    assert "EMPLOYER_GAP" in found.explains
    assert "EMPLOYER_GAP" not in found.confirms


def test_an_explained_deviation_cannot_carry_a_member_up() -> None:
    move = next_state(
        "WATCH",
        evidence(p30=0.9, change_points=confirmed(), outage_covers_deviations=True),
    )
    assert move.to_state == "WATCH"


def test_a_real_deterioration_alongside_an_outage_is_still_visible() -> None:
    """Both are reported. Resolving it for the officer would hide a case they
    have to see."""
    found = corroborate(evidence(outage_covers_deviations=True, features={"deduction_missed_count_90d": 2.0}))
    assert found.explains and found.confirms


# ---------------------------------------------------------------------------
# hysteresis
# ---------------------------------------------------------------------------
def test_a_state_does_not_change_twice_in_a_fortnight() -> None:
    """Without this a member on the edge of a threshold oscillates weekly, and
    an officer learns the state means nothing."""
    recent = TODAY - timedelta(days=3)
    move = next_state("STABLE", evidence(departure_events=2), since=recent)

    assert move.to_state == "STABLE"
    assert move.held_by_hysteresis
    assert str(HYSTERESIS_DAYS) in move.reason


def test_the_fortnight_passes_and_the_change_lands() -> None:
    old = TODAY - timedelta(days=HYSTERESIS_DAYS + 1)
    move = next_state("STABLE", evidence(departure_events=2), since=old)
    assert move.to_state == "WATCH"
    assert not move.held_by_hysteresis


def test_critical_is_never_held() -> None:
    """A member thirty days late does not wait a fortnight for the platform to
    be allowed to say so."""
    recent = TODAY - timedelta(days=1)
    move = next_state("WATCH", evidence(max_days_late=60), since=recent)
    assert move.to_state == "CRITICAL"
    assert not move.held_by_hysteresis


def test_a_noisy_member_flips_at_most_once_per_fortnight() -> None:
    """The oscillation test. A member alternating either side of a threshold
    every week must not alternate state with them."""
    state = "STABLE"
    since: date | None = None
    changes: list[date] = []

    for week in range(20):
        day = TODAY + timedelta(days=7 * week)
        move = next_state(
            state,
            evidence(
                as_of=day,
                departure_events=2 if week % 2 == 0 else 0,
                consecutive_on_time=0 if week % 2 == 0 else 2,
            ),
            since=since,
        )
        if move.changed:
            changes.append(day)
            state, since = move.to_state, day

    gaps = [(b - a).days for a, b in pairwise(changes)]
    assert all(gap >= HYSTERESIS_DAYS for gap in gaps), f"flipped after {gaps}"


# ---------------------------------------------------------------------------
# what a transition records
# ---------------------------------------------------------------------------
def test_every_transition_names_its_rule_and_its_reason() -> None:
    """A state change nobody can explain is a state change nobody can
    challenge."""
    move = next_state("STABLE", evidence(departure_events=2))
    body = move.as_dict()
    assert body["rule"]
    assert body["reason"]
    assert body["from_state"] == "STABLE"
    assert body["to_state"] == "WATCH"


def test_the_evidence_ids_travel_with_the_transition() -> None:
    move = next_state("STABLE", evidence(departure_events=2, evidence_ids=["ev_01ARZ3NDEKTSV4RRFFQ69G5FAW"]))
    assert move.evidence_ids == ["ev_01ARZ3NDEKTSV4RRFFQ69G5FAW"]
