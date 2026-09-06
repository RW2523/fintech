"""T-033 — the fraud rules (docs/07 §3)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from app.findings import Finding, worst_severity
from app.graph import EntityGraph
from app.rules import (
    ANOMALY_THRESHOLD,
    CONTACT_CHANGE_DAYS,
    VELOCITY_COUNT,
    VELOCITY_DAYS,
    CaseFacts,
    evaluate_rules,
)

APPLIED = date(2026, 7, 27)
MEMBER = "M-000042"
EMPTY = EntityGraph()


def facts(**overrides: Any) -> CaseFacts:
    base: dict[str, Any] = {
        "case_id": "case_1",
        "member_id": MEMBER,
        "applied_at": APPLIED,
        "employer_id": "E-019",
        "branch_id": "BR-01",
    }
    return CaseFacts(**{**base, **overrides})


def peers(
    count: int, *, days_ago: int = 1, employer: str = "E-019", branch: str | None = "BR-01"
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "member_id": f"M-0001{i:02d}",
            "employer_id": employer,
            "branch_id": branch,
            "applied_at": APPLIED - timedelta(days=days_ago),
        }
        for i in range(count)
    )


# ---------------------------------------------------------------------------
# velocity
# ---------------------------------------------------------------------------
def test_a_burst_of_applications_from_one_employer_is_flagged() -> None:
    found = evaluate_rules(facts(recent_applications=peers(VELOCITY_COUNT)), EMPTY)
    assert [f.rule for f in found] == ["velocity"]
    assert found[0].severity == "MEDIUM"
    assert found[0].code == "INT-04"


def test_a_branch_that_is_always_busy_is_not_flagged() -> None:
    """Three a week is not a burst where three a week is normal.

    Measured on this population, the absolute threshold alone raised velocity
    findings on 14 of 598 clean cases, which by itself breaks the 3% ceiling.
    """
    busy = facts(recent_applications=peers(VELOCITY_COUNT), employer_baseline=3.0)
    assert evaluate_rules(busy, EMPTY) == []


def test_a_quiet_branch_suddenly_busy_is_flagged() -> None:
    sudden = facts(recent_applications=peers(VELOCITY_COUNT + 1), employer_baseline=0.4)
    found = evaluate_rules(sudden, EMPTY)
    assert [f.rule for f in found] == ["velocity"]
    assert found[0].detail["usual_per_window"] == 0.4


def test_a_few_applications_are_not_a_burst() -> None:
    assert evaluate_rules(facts(recent_applications=peers(VELOCITY_COUNT - 1)), EMPTY) == []


def test_applications_outside_the_window_do_not_count() -> None:
    old = peers(VELOCITY_COUNT + 2, days_ago=VELOCITY_DAYS + 1)
    assert evaluate_rules(facts(recent_applications=old), EMPTY) == []


def test_applications_from_another_employer_do_not_count() -> None:
    other = peers(VELOCITY_COUNT + 2, employer="E-999")
    assert evaluate_rules(facts(recent_applications=other), EMPTY) == []


def test_applications_from_another_branch_do_not_count() -> None:
    """A big employer across branches is not a coordinated group."""
    other = peers(VELOCITY_COUNT + 2, branch="BR-99")
    assert evaluate_rules(facts(recent_applications=other), EMPTY) == []


def test_velocity_needs_an_application_date() -> None:
    assert evaluate_rules(facts(applied_at=None, recent_applications=peers(9)), EMPTY) == []


# ---------------------------------------------------------------------------
# contact change
# ---------------------------------------------------------------------------
def test_a_contact_changed_just_before_applying_is_noted() -> None:
    found = evaluate_rules(facts(contact_updated_at=APPLIED - timedelta(days=3)), EMPTY)
    assert [f.code for f in found] == ["INT-06"]
    assert found[0].severity == "LOW"
    assert found[0].detail["days_before_application"] == 3


def test_an_old_contact_change_is_not_noted() -> None:
    old = APPLIED - timedelta(days=CONTACT_CHANGE_DAYS + 1)
    assert evaluate_rules(facts(contact_updated_at=old), EMPTY) == []


def test_a_contact_changed_after_applying_is_not_noted() -> None:
    later = APPLIED + timedelta(days=2)
    assert evaluate_rules(facts(contact_updated_at=later), EMPTY) == []


# ---------------------------------------------------------------------------
# duplicate applicant
# ---------------------------------------------------------------------------
def test_a_duplicate_applicant_is_high() -> None:
    """T-033 acceptance: the duplicate applicant is found."""
    found = evaluate_rules(facts(duplicate_identities=("M-000099",)), EMPTY)
    assert [f.rule for f in found] == ["duplicate_applicant"]
    assert found[0].severity == "HIGH"
    assert found[0].detail["also_used_by"] == ["M-000099"]
    assert set(found[0].members) == {MEMBER, "M-000099"}


def test_a_member_matching_only_themselves_is_not_a_duplicate() -> None:
    assert evaluate_rules(facts(duplicate_identities=(MEMBER,)), EMPTY) == []


# ---------------------------------------------------------------------------
# document findings
# ---------------------------------------------------------------------------
def test_an_identity_mismatch_is_critical() -> None:
    found = evaluate_rules(
        facts(document_findings=({"code": "INT-08", "severity": "HIGH", "document_id": "DOC-1"},)), EMPTY
    )
    assert found[0].severity == "CRITICAL"
    assert found[0].documents == ("DOC-1",)


def test_a_confirmed_reused_image_is_high() -> None:
    found = evaluate_rules(facts(document_findings=({"code": "INT-02", "severity": "HIGH"},)), EMPTY)
    assert found[0].severity == "HIGH"


def test_an_unconfirmed_image_match_stays_a_lead() -> None:
    """T-022 measured that a page hash alone cannot separate documents."""
    found = evaluate_rules(facts(document_findings=({"code": "INT-02", "severity": "LOW"},)), EMPTY)
    assert found[0].severity == "LOW"


def test_a_document_finding_the_fraud_service_does_not_escalate_is_ignored() -> None:
    """DOC-04 asks for a clearer copy. It is not an accusation."""
    assert evaluate_rules(facts(document_findings=({"code": "DOC-04", "severity": "LOW"},)), EMPTY) == []


# ---------------------------------------------------------------------------
# guarantor findings
# ---------------------------------------------------------------------------
def ring_with(member: str, size: int = 7) -> tuple[EntityGraph, tuple[str, ...]]:
    members = (member, *(f"M-0009{i:02d}" for i in range(1, size)))
    graph = EntityGraph()
    for index, name in enumerate(members):
        graph.add_guarantee(name, members[(index + 1) % size])
    return graph, members


def test_a_ring_with_concurrent_applications_is_high() -> None:
    graph, members = ring_with(MEMBER)
    applications = {m: APPLIED - timedelta(days=i * 10) for i, m in enumerate(members[:4])}
    found = evaluate_rules(facts(applications_by_member=applications), graph)
    cycle = [f for f in found if f.rule == "guarantor_cycle"]
    assert cycle and cycle[0].severity == "HIGH"
    assert cycle[0].detail["cycle_length"] == 7


def test_a_ring_with_a_little_concurrent_borrowing_is_medium() -> None:
    """Two members borrowing through the loop at once, but not the three
    docs/07 §3 needs for HIGH."""
    graph, members = ring_with(MEMBER)
    applications = {members[0]: APPLIED, members[3]: APPLIED - timedelta(days=30)}
    found = [
        f
        for f in evaluate_rules(facts(applications_by_member=applications), graph)
        if f.rule == "guarantor_cycle"
    ]
    assert found and found[0].severity == "MEDIUM"


def test_borrowing_spread_across_a_year_is_not_a_ring() -> None:
    """Neighbours who borrow are not a ring.

    docs/07 §3 uses the ninety-day window to separate HIGH from MEDIUM.
    Measured on this population, without that window as the bar for raising
    anything at all, incidental cycles put clean cases over the 3% ceiling.
    """
    graph, members = ring_with(MEMBER)
    applications = {
        members[0]: APPLIED,
        members[3]: APPLIED - timedelta(days=200),
        members[5]: APPLIED - timedelta(days=400),
    }
    assert [
        f
        for f in evaluate_rules(facts(applications_by_member=applications), graph)
        if f.rule == "guarantor_cycle"
    ] == []


def test_a_quiet_ring_raises_nothing() -> None:
    """Members standing behind each other is how a cooperative works.

    Measured on this population, 4.4% of members sit in some guarantee cycle,
    so raising a finding on every one would bury the real ones.
    """
    graph, _ = ring_with(MEMBER)
    assert [f for f in evaluate_rules(facts(), graph) if f.rule == "guarantor_cycle"] == []


def test_a_cycle_this_member_is_not_in_is_not_their_finding() -> None:
    graph = EntityGraph()
    others = tuple(f"M-0009{i:02d}" for i in range(4))
    for index, name in enumerate(others):
        graph.add_guarantee(name, others[(index + 1) % len(others)])
    applications = dict.fromkeys(others, APPLIED)
    assert [
        f
        for f in evaluate_rules(facts(applications_by_member=applications), graph)
        if f.rule == "guarantor_cycle"
    ] == []


def test_a_serial_guarantor_is_medium() -> None:
    graph = EntityGraph()
    for index in range(6):
        graph.add_guarantee(MEMBER, f"M-0007{index:02d}")
    found = [f for f in evaluate_rules(facts(), graph) if f.rule == "serial_guarantor"]
    assert found and found[0].severity == "MEDIUM"
    assert found[0].detail["guarantees"] == 6


def test_branch_concentration_is_advisory() -> None:
    """A portfolio observation, not a finding against anyone."""
    guarantors = ["M-000001"] * 6 + ["M-000002"] * 4 + ["M-000003"] * 3 + [f"M-0001{i:02d}" for i in range(6)]
    found = [
        f
        for f in evaluate_rules(facts(branch_guarantees={"BR-01": guarantors}), EMPTY)
        if f.rule == "guarantee_concentration"
    ]
    assert found and found[0].advisory
    assert found[0].severity == "LOW"


# ---------------------------------------------------------------------------
# anomaly
# ---------------------------------------------------------------------------
def test_a_high_anomaly_score_is_advisory_only() -> None:
    found = evaluate_rules(facts(anomaly_score=0.95), EMPTY)
    assert [f.rule for f in found] == ["anomaly"]
    assert found[0].advisory
    assert found[0].severity == "LOW"


def test_an_ordinary_case_raises_no_anomaly() -> None:
    assert evaluate_rules(facts(anomaly_score=ANOMALY_THRESHOLD - 0.01), EMPTY) == []


def test_an_anomaly_alone_does_not_raise_the_level() -> None:
    """docs/07 §3 — unusual is a reason to look, not a reason to accuse."""
    found = evaluate_rules(facts(anomaly_score=0.99), EMPTY)
    assert worst_severity(found) == "LOW"
    assert worst_severity([]) == "LOW"


def test_an_anomaly_does_not_dilute_a_real_finding() -> None:
    found = evaluate_rules(facts(anomaly_score=0.99, duplicate_identities=("M-000099",)), EMPTY)
    assert worst_severity(found) == "HIGH"


# ---------------------------------------------------------------------------
# the shape of a finding
# ---------------------------------------------------------------------------
def test_the_same_observation_is_reported_once() -> None:
    found = evaluate_rules(
        facts(
            document_findings=({"code": "INT-08", "severity": "HIGH"}, {"code": "INT-08", "severity": "HIGH"})
        ),
        EMPTY,
    )
    assert len(found) == 1


def test_finding_ids_are_derived_from_what_was_found() -> None:
    first = evaluate_rules(facts(duplicate_identities=("M-000099",)), EMPTY)[0]
    second = evaluate_rules(facts(duplicate_identities=("M-000099",)), EMPTY)[0]
    other = evaluate_rules(facts(duplicate_identities=("M-000098",)), EMPTY)[0]
    assert first.finding_id == second.finding_id
    assert first.finding_id != other.finding_id
    assert first.finding_id.startswith("fnd_")


def test_a_finding_cannot_carry_an_unknown_severity() -> None:
    with pytest.raises(ValueError, match="unknown severity"):
        Finding(code="INT-01", severity="APOCALYPTIC", rule="test")


def test_a_clean_case_raises_nothing() -> None:
    assert evaluate_rules(facts(), EMPTY) == []
    assert worst_severity([]) == "LOW"
