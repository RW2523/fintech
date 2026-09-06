"""T-031 — scoring a case from the saved artifacts (docs/07 §2.3)."""

from __future__ import annotations

from typing import Any

import pytest

from ml.credit_risk.calibration import grade_of
from ml.credit_risk.explain import reason_map
from ml.credit_risk.predict import CONDUCT_TERMS, CreditRiskModel, conduct_score

pytestmark = pytest.mark.filterwarnings("ignore")


CLEAN: dict[str, Any] = {
    "ontime_rate_24m": 1.0,
    "arrears_events_12m": 0.0,
    "months_since_last_arrears": None,
    "restructures_36m": 0.0,
    "facilities_open": 1.0,
    "facilities_new_6m": 0.0,
    "utilisation": 0.2,
    "tenure_months": 120.0,
    "savings_balance": 8000.0,
    "savings_slope_180d": 9.0,
    "savings_paused_months": 0.0,
    "share_capital_units": 400.0,
    "share_capital_ratio": 0.08,
    "income_verified_monthly": 5200.0,
    "income_source_variance": 0.02,
    "dsr_proposed": 0.22,
    "commitments_monthly": 400.0,
    "employer_sector": "PUBLIC_ADMIN",
    "employer_tenure_months": 90.0,
    "application_count_12m": 1.0,
    "contact_change_days": 400.0,
    "doc_min_conf": 0.95,
    "findings_max_severity": 0.0,
}

TROUBLED: dict[str, Any] = {
    **CLEAN,
    "ontime_rate_24m": 0.4,
    "arrears_events_12m": 4.0,
    "months_since_last_arrears": 1.0,
    "restructures_36m": 1.0,
    "facilities_open": 3.0,
    "facilities_new_6m": 2.0,
    "utilisation": 0.95,
    "savings_balance": 50.0,
    "savings_slope_180d": -30.0,
    "savings_paused_months": 6.0,
    "dsr_proposed": 0.62,
    "commitments_monthly": 2600.0,
    "employer_sector": "RETAIL",
}


@pytest.fixture(scope="module")
def model(artifacts) -> CreditRiskModel:
    return CreditRiskModel(artifacts)


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def test_a_troubled_case_scores_worse_than_a_clean_one(model) -> None:
    assert model.predict(TROUBLED).champion.pd_12m > model.predict(CLEAN).champion.pd_12m


def test_the_challenger_agrees_on_the_direction(model) -> None:
    assert model.predict(TROUBLED).challenger.pd_12m > model.predict(CLEAN).challenger.pd_12m


def test_the_grade_follows_the_probability(model) -> None:
    for case in (CLEAN, TROUBLED):
        prediction = model.predict(case)
        assert prediction.champion.grade == grade_of(prediction.champion.pd_12m)


def test_the_same_inputs_give_the_same_run_id(model) -> None:
    """T-032 acceptance: a score must be reconstructable from what produced it."""
    first, second = model.predict(CLEAN), model.predict(CLEAN)
    assert first.model_run_id == second.model_run_id
    assert first.champion.pd_12m == second.champion.pd_12m
    assert first.model_run_id.startswith("mr_")


def test_different_inputs_give_a_different_run_id(model) -> None:
    assert model.predict(CLEAN).model_run_id != model.predict(TROUBLED).model_run_id


def test_a_missing_feature_does_not_stop_a_score(model) -> None:
    """A first-time applicant is missing most of a conduct record."""
    sparse = {"employer_sector": "TRANSPORT", "tenure_months": 6.0}
    prediction = model.predict(sparse)
    assert 0.0 <= prediction.champion.pd_12m <= 1.0
    assert prediction.champion.grade in set("ABCDE")


def test_an_unfamiliar_case_raises_the_out_of_distribution_score(model) -> None:
    strange = {
        **CLEAN,
        "savings_balance": 5e7,
        "income_verified_monthly": 4e6,
        "commitments_monthly": 3e6,
        "tenure_months": 900.0,
    }
    assert model.predict(strange).champion.ood_score > model.predict(CLEAN).champion.ood_score


def test_an_unseen_sector_does_not_become_the_worst_case(model) -> None:
    """An empty bin carries no weight, so a new sector is neutral, not damning."""
    unseen = model.predict({**CLEAN, "employer_sector": "SPACE_TOURISM"})
    assert unseen.champion.pd_12m < 0.12


# ---------------------------------------------------------------------------
# explanation
# ---------------------------------------------------------------------------
def test_every_reason_code_returned_is_approved(model) -> None:
    approved = {code for entry in reason_map().values() for code in entry.values()}
    for case in (CLEAN, TROUBLED):
        assert set(model.predict(case).reason_codes) <= approved


def test_drivers_are_ordered_by_how_much_they_moved_the_score(model) -> None:
    drivers = model.predict(TROUBLED).drivers
    shares = [d.share for d in drivers]
    assert shares == sorted(shares, reverse=True)
    assert len(drivers) <= 5


def test_a_troubled_case_reports_adverse_drivers(model) -> None:
    drivers = model.predict(TROUBLED).drivers
    assert any(d.direction == "ADVERSE" for d in drivers)


def test_a_clean_case_reports_favourable_drivers(model) -> None:
    drivers = model.predict(CLEAN).drivers
    assert any(d.direction == "FAVOURABLE" for d in drivers)


def test_every_driver_names_a_characteristic_the_model_uses(model) -> None:
    used = set(model.champion.features)
    assert {d.feature for d in model.predict(TROUBLED).drivers} <= used


# ---------------------------------------------------------------------------
# the conduct calculation
# ---------------------------------------------------------------------------
def test_conduct_is_neutral_without_a_repayment_record() -> None:
    """A first-time borrower has not earned a perfect record, nor a bad one."""
    calculation = conduct_score(
        {"arrears_events_12m": 0.0, "restructures_36m": 0.0, "facilities_new_6m": 0.0}
    )
    assert calculation.value == 0.5
    assert len(calculation.missing) == len(CONDUCT_TERMS)
    assert "no repayment record" in calculation.formula


def test_a_spotless_record_scores_at_the_top() -> None:
    assert conduct_score(CLEAN).value == pytest.approx(1.0)


def test_a_poor_record_scores_low() -> None:
    assert conduct_score(TROUBLED).value < 0.45


def test_conduct_stays_inside_its_range() -> None:
    for case in (CLEAN, TROUBLED, {"ontime_rate_24m": 0.0, "arrears_events_12m": 99.0}):
        assert 0.0 <= conduct_score(case).value <= 1.0


def test_never_having_been_in_arrears_counts_as_good() -> None:
    """No date of last arrears because there were none is the best record."""
    with_date = conduct_score({**CLEAN, "months_since_last_arrears": 0.0})
    without = conduct_score(CLEAN)
    assert without.value > with_date.value
    assert "months_since_last_arrears" not in without.missing


def test_the_same_conduct_inputs_give_the_same_calc_id() -> None:
    assert conduct_score(CLEAN).calc_id == conduct_score(CLEAN).calc_id
    assert conduct_score(CLEAN).calc_id != conduct_score(TROUBLED).calc_id
    assert conduct_score(CLEAN).calc_id.startswith("calc_")


def test_the_conduct_calculation_shows_its_working() -> None:
    calculation = conduct_score(TROUBLED)
    assert calculation.formula
    assert set(calculation.inputs) <= {name for name, _, _ in CONDUCT_TERMS}


# ---------------------------------------------------------------------------
# the serialised shape docs/07 §2.3 promises
# ---------------------------------------------------------------------------
def test_the_response_carries_everything_the_contract_names(model) -> None:
    body = model.predict(TROUBLED).as_dict()
    assert set(body) >= {
        "model_run_id",
        "version",
        "champion",
        "challenger",
        "conduct_score",
        "conduct_calc_id",
        "reason_codes",
        "drivers",
    }
    assert set(body["champion"]) >= {"model", "version", "pd_12m", "grade", "calibration", "ood_score"}


def test_the_response_is_json_serialisable(model) -> None:
    import json

    json.dumps(model.predict(TROUBLED).as_dict())
