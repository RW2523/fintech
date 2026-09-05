"""T-011 — the affordability calculation and its invariants (docs/05 §2, §4)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from app.affordability import AffordabilityInputs, compute, instalment_for

STRESS = (
    {"case": "income-10%", "income_factor": 0.90},
    {"case": "rate+2%", "profit_rate_delta": 0.02},
    {"case": "commit+10%", "commitments_factor": 1.10},
)

PROPERTY = settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def inputs(**overrides: object) -> AffordabilityInputs:
    base = {
        "income_verified_monthly": Decimal("4200"),
        "commitments_monthly": Decimal("600"),
        "requested_amount": Decimal("8000"),
        "tenor_months": 24,
        "profit_rate": Decimal("0.065"),
        "dsr_limit": Decimal("0.60"),
        "residual_income_min": Decimal("800"),
        "stress_cases": STRESS,
    }
    base.update(overrides)
    return AffordabilityInputs(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# the formula
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "amount,tenor,rate,expected",
    [
        (Decimal("24000"), 24, Decimal("0.065"), Decimal("1130.00")),
        (Decimal("12000"), 12, Decimal("0.065"), Decimal("1065.00")),
        (Decimal("8000"), 24, Decimal("0.065"), Decimal("376.67")),
    ],
)
def test_instalment_follows_the_product_formula(
    amount: Decimal, tenor: int, rate: Decimal, expected: Decimal
) -> None:
    """`amount * (1 + rate * tenor / 12) / tenor` (docs/05 §2)."""
    assert instalment_for(amount, tenor, rate) == expected


def test_a_zero_tenor_is_refused() -> None:
    with pytest.raises(ValueError, match="tenor"):
        instalment_for(Decimal("1000"), 0, Decimal("0.065"))


def test_money_stays_decimal_and_never_becomes_float() -> None:
    """CLAUDE.md §7."""
    result = compute(inputs())
    assert isinstance(result.instalment, Decimal)
    assert isinstance(result.residual, Decimal)
    assert result.as_contract()["instalment"] == f"{result.instalment:.2f}"


# ---------------------------------------------------------------------------
# invariants
# ---------------------------------------------------------------------------
@PROPERTY
@given(
    commitments_a=st.decimals(min_value=0, max_value=5000, places=2),
    commitments_b=st.decimals(min_value=0, max_value=5000, places=2),
)
def test_dsr_is_monotonic_in_commitments(commitments_a: Decimal, commitments_b: Decimal) -> None:
    """More commitments can never mean a lower debt-service ratio."""
    assume(commitments_a <= commitments_b)
    lower = compute(inputs(commitments_monthly=commitments_a)).dsr
    higher = compute(inputs(commitments_monthly=commitments_b)).dsr
    assert lower <= higher


@PROPERTY
@given(
    income_a=st.decimals(min_value=1, max_value=50000, places=2),
    income_b=st.decimals(min_value=1, max_value=50000, places=2),
)
def test_dsr_is_antitonic_in_income(income_a: Decimal, income_b: Decimal) -> None:
    """More income can never mean a higher debt-service ratio."""
    assume(income_a <= income_b)
    assert (
        compute(inputs(income_verified_monthly=income_a)).dsr
        >= compute(inputs(income_verified_monthly=income_b)).dsr
    )


@PROPERTY
@given(
    amount_a=st.decimals(min_value=1000, max_value=150000, places=2),
    amount_b=st.decimals(min_value=1000, max_value=150000, places=2),
)
def test_dsr_is_monotonic_in_requested_amount(amount_a: Decimal, amount_b: Decimal) -> None:
    assume(amount_a <= amount_b)
    assert compute(inputs(requested_amount=amount_a)).dsr <= compute(inputs(requested_amount=amount_b)).dsr


@PROPERTY
@given(commitments=st.decimals(min_value=0, max_value=5000, places=2))
def test_capacity_score_is_antitonic_in_commitments(commitments: Decimal) -> None:
    """A worse ratio can never produce a better capacity score."""
    baseline = compute(inputs(commitments_monthly=Decimal("0"))).capacity_score
    assert compute(inputs(commitments_monthly=commitments)).capacity_score <= baseline


@PROPERTY
@given(
    income=st.decimals(min_value=1, max_value=50000, places=2),
    commitments=st.decimals(min_value=0, max_value=50000, places=2),
)
def test_headroom_is_always_the_limit_minus_the_ratio(income: Decimal, commitments: Decimal) -> None:
    result = compute(inputs(income_verified_monthly=income, commitments_monthly=commitments))
    if result.dsr != float("inf"):
        assert result.headroom == pytest.approx(result.dsr_limit - result.dsr, abs=1e-4)


@PROPERTY
@given(
    income=st.decimals(min_value=1, max_value=50000, places=2),
    commitments=st.decimals(min_value=0, max_value=50000, places=2),
)
def test_capacity_score_stays_inside_its_range(income: Decimal, commitments: Decimal) -> None:
    score = compute(inputs(income_verified_monthly=income, commitments_monthly=commitments)).capacity_score
    assert 0 <= score <= 100


def test_the_same_inputs_produce_the_same_digest_and_numbers() -> None:
    """A calc must be reproducible from its recorded inputs (docs/05 §3.7)."""
    first, second = compute(inputs()), compute(inputs())
    assert first.inputs_digest == second.inputs_digest
    assert (first.dsr, first.capacity_score, first.instalment) == (
        second.dsr,
        second.capacity_score,
        second.instalment,
    )
    assert first.calc_id != second.calc_id, "each run is separately identifiable"


def test_different_inputs_produce_different_digests() -> None:
    assert (
        compute(inputs()).inputs_digest != compute(inputs(commitments_monthly=Decimal("601"))).inputs_digest
    )


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------
def test_no_verified_income_yields_no_capacity_rather_than_an_error() -> None:
    """CLAUDE.md §2.7 — fail safe. The DOC/AFF gates catch it downstream."""
    result = compute(inputs(income_verified_monthly=Decimal("0")))
    assert result.dsr == float("inf")
    assert result.capacity_score == 0
    assert result.headroom == 0.0


def test_stress_cases_are_each_evaluated() -> None:
    result = compute(inputs())
    assert [s.case for s in result.stress] == ["income-10%", "rate+2%", "commit+10%"]


def test_a_tighter_case_fails_more_stress_scenarios() -> None:
    easy = compute(inputs())
    tight = compute(inputs(income_verified_monthly=Decimal("700"), commitments_monthly=Decimal("50")))
    assert tight.stress_failures >= easy.stress_failures
    assert easy.stress_failures == 0


def test_an_instalment_override_is_used_verbatim() -> None:
    """The Challenger may ask for a substituted instalment (docs/06 §5.3)."""
    result = compute(inputs(instalment_override=Decimal("999.99")))
    assert result.instalment == Decimal("999.99")
