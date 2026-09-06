"""The Decision Factor formulas, shared by the services that use them (docs/05 §4).

The policy engine consumes these scores and the risk and fraud services
produce them. The formulas live here so there is one implementation: two would
be two chances for a decision to be irreproducible.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from cio_dff.factors import (
    FAMILIES,
    FAMILY_TOOLS,
    SEVERITY_POINTS,
    FactorScore,
    commitment_score,
    conditions_score,
    conduct_score,
    integrity_score,
    score_family,
)


def clean(
    *,
    ontime_rate_24m: float = 1.0,
    any_arrears: bool = False,
    months_since_last_arrears: float = 0.0,
    restructures_36m: int = 0,
    grade: str = "A",
    history_months: int = 24,
    derived: bool = False,
) -> FactorScore:
    """A spotless conduct record, with anything the test cares about changed."""
    return conduct_score(
        ontime_rate_24m=ontime_rate_24m,
        any_arrears=any_arrears,
        months_since_last_arrears=months_since_last_arrears,
        restructures_36m=restructures_36m,
        grade=grade,
        history_months=history_months,
        derived=derived,
    )


# ---------------------------------------------------------------------------
# CONDUCT
# ---------------------------------------------------------------------------
def test_a_spotless_record_with_the_best_grade_scores_at_the_top() -> None:
    """40 for on-time, 25 for no arrears, 20 for grade A."""
    assert clean().score == 85


def test_arrears_cost_the_recency_credit() -> None:
    fresh = clean(any_arrears=True, months_since_last_arrears=0.0)
    old = clean(any_arrears=True, months_since_last_arrears=24.0)
    assert fresh.score < old.score
    assert old.score == 85


def test_each_restructure_costs_fifteen() -> None:
    one = clean(restructures_36m=1)
    two = clean(restructures_36m=2)
    assert clean().score - one.score == 15
    assert one.score - two.score == 15


def test_a_thin_file_is_parked_at_neutral() -> None:
    """Too little history to judge is not the same as a poor record."""
    for months in (0, 1, 5):
        assert clean(history_months=months).score == 55


def test_a_thin_file_is_parked_even_when_the_record_looks_bad() -> None:
    parked = clean(history_months=2, ontime_rate_24m=0.0, restructures_36m=3, grade="E")
    assert parked.score == 55


def test_the_score_never_leaves_zero_to_one_hundred() -> None:
    worst = clean(ontime_rate_24m=0.0, any_arrears=True, restructures_36m=9, grade="E")
    assert worst.score == 0
    assert 0 <= clean().score <= 100


def test_a_better_grade_never_lowers_the_score() -> None:
    scores = [clean(grade=g).score for g in "EDCBA"]
    assert scores == sorted(scores)


def test_conduct_names_the_tool_that_owns_it() -> None:
    factor = clean()
    assert factor.family == "CONDUCT"
    assert factor.tool == FAMILY_TOOLS["CONDUCT"] == "risk.score"


# ---------------------------------------------------------------------------
# identity and reproducibility
# ---------------------------------------------------------------------------
def test_each_calculation_is_its_own_event_by_default() -> None:
    """Computing a score twice is two events, so two ids."""
    assert clean().calc_id != clean().calc_id


def test_a_derived_id_is_reproducible_from_the_inputs() -> None:
    """A frozen snapshot must reproduce its references, not just its numbers."""
    first = clean(derived=True)
    second = clean(derived=True)
    other = clean(grade="E", derived=True)
    assert first.calc_id == second.calc_id
    assert first.calc_id != other.calc_id
    assert first.calc_id.startswith("calc_")


def test_the_digest_covers_the_inputs_not_the_result() -> None:
    same = clean()
    again = clean()
    assert same.inputs_digest == again.inputs_digest
    assert clean(grade="B").inputs_digest != same.inputs_digest


# ---------------------------------------------------------------------------
# the other families
# ---------------------------------------------------------------------------
def test_integrity_deducts_by_severity() -> None:
    clean = integrity_score(open_findings=[])
    low = integrity_score(open_findings=[{"severity": "LOW"}])
    high = integrity_score(open_findings=[{"severity": "HIGH"}])
    assert clean.score - low.score == SEVERITY_POINTS["LOW"]
    assert clean.score - high.score == SEVERITY_POINTS["HIGH"]


def test_a_critical_finding_is_a_gate_and_not_a_deduction() -> None:
    """docs/05 §4 — a CRITICAL finding stops the case; it does not price it."""
    critical = integrity_score(open_findings=[{"severity": "CRITICAL"}])
    assert critical.score == 100
    assert critical.level == "CRITICAL"


def test_integrity_reports_the_worst_open_finding() -> None:
    mixed = integrity_score(open_findings=[{"severity": "LOW"}, {"severity": "HIGH"}, {"severity": "MEDIUM"}])
    assert mixed.level == "HIGH"


def _commitment(*, tenure_years: float, savings: str, shares: int, paused: int = 0) -> FactorScore:
    return commitment_score(
        tenure_years=tenure_years,
        savings_balance=Decimal(savings),
        proposed_instalment=Decimal("500"),
        share_capital_units=shares,
        product_min_share_units=100,
        savings_paused_months=paused,
    )


def test_commitment_rewards_tenure_and_savings() -> None:
    thin = _commitment(tenure_years=0.5, savings="0", shares=0)
    deep = _commitment(tenure_years=10.0, savings="20000", shares=500)
    assert deep.score > thin.score


def test_paused_savings_cost_commitment() -> None:
    saving = _commitment(tenure_years=5.0, savings="6000", shares=200, paused=0)
    paused = _commitment(tenure_years=5.0, savings="6000", shares=200, paused=6)
    assert paused.score < saving.score


def test_every_family_is_scoreable_by_name() -> None:
    factor = score_family(
        "CONDUCT",
        {
            "ontime_rate_24m": 1.0,
            "any_arrears": False,
            "months_since_last_arrears": 0.0,
            "restructures_36m": 0,
            "grade": "A",
            "history_months": 24,
        },
    )
    assert factor.family == "CONDUCT"
    assert set(FAMILIES) == {"CAPACITY", "CONDUCT", "COMMITMENT", "CONDITIONS", "INTEGRITY"}


def test_an_unknown_family_is_refused() -> None:
    with pytest.raises(KeyError):
        score_family("WIZARDRY", {})


def test_conditions_scores_from_portfolio_inputs() -> None:
    calm = conditions_score(employer_share_of_portfolio=0.02, sector_stress_flag=False)
    concentrated = conditions_score(employer_share_of_portfolio=0.12, sector_stress_flag=False)
    stressed = conditions_score(employer_share_of_portfolio=0.02, sector_stress_flag=True)
    assert calm.score == 100
    assert concentrated.score == 80
    assert stressed.score == 85
    assert calm.family == "CONDITIONS"


def test_the_contract_shape_is_what_a_factor_must_publish() -> None:
    from cio_contracts import validate

    factor = clean()
    validate(factor.as_contract(), "FactorScore")
