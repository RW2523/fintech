"""T-031 — properties the trained artifacts must hold (docs/07 §2.2).

These read the version serving is pointed at, so they check the model that
would actually make decisions, not a model fitted inside the test.
"""

from __future__ import annotations

import pytest

from ml.credit_risk.calibration import ISOTONIC_MIN_EVENTS
from ml.credit_risk.explain import monotone_table
from ml.credit_risk.validate import CALIBRATION_BAND, MIN_HOLDOUT_AUC, REQUIRED_CARD_SECTIONS

pytestmark = pytest.mark.filterwarnings("ignore")


def test_the_challenger_meets_the_discrimination_target(artifacts) -> None:
    """docs/00 T-031 acceptance: hold-out AUC at or above 0.72."""
    assert artifacts.metrics["challenger"]["performance"]["test"]["auc"] >= MIN_HOLDOUT_AUC


def test_the_champion_shortfall_is_recorded_rather_than_hidden(artifacts) -> None:
    """The champion sits fractionally under the target on this population.

    Pinned rather than relaxed, so the shortfall stays visible: if a change
    lifts the champion over the line, this fails and the card must be
    corrected to stop claiming a shortfall that no longer exists.
    """
    held = artifacts.metrics["champion"]["performance"]["test"]
    card = (artifacts.path / "card.md").read_text()
    assert (held["auc"] >= MIN_HOLDOUT_AUC) is ("falls fractionally short" not in card)


def test_the_champion_is_close_to_the_target(artifacts) -> None:
    """Close enough that the gap is noise at this many events, not a broken model."""
    held = artifacts.metrics["champion"]["performance"]["test"]
    assert held["auc"] > MIN_HOLDOUT_AUC - 0.02
    assert held["auc_ci_high"] > MIN_HOLDOUT_AUC


def test_the_calibration_miss_is_recorded_rather_than_hidden(artifacts) -> None:
    """The slope target is not met on this population, and the card says so.

    Guarding it this way keeps the failure visible: if a later change brings
    the slope into band, this test fails and the card must be corrected.
    """
    held = artifacts.metrics["champion"]["performance"]["test"]
    inside = CALIBRATION_BAND[0] <= held["calibration_slope"] <= CALIBRATION_BAND[1]
    card = (artifacts.path / "card.md").read_text()
    assert inside is ("Calibration decays out of time" not in card)


def test_the_calibration_miss_is_spread_and_not_level(artifacts) -> None:
    """Correcting the level alone must not fix the slope, or the diagnosis is wrong."""
    diagnostic = artifacts.metrics["champion"]["calibration_diagnostic"]["test"]
    assert diagnostic["level_corrected_slope"] == pytest.approx(diagnostic["served_slope"], abs=0.05)


def test_the_model_ranks_better_than_chance_on_every_split(artifacts) -> None:
    for split, performance in artifacts.metrics["champion"]["performance"].items():
        assert performance["auc"] > 0.5, split


def test_no_characteristic_inverts_its_policy_direction(artifacts) -> None:
    directions = monotone_table()
    for entry in artifacts.metrics["champion"]["table"]["characteristics"]:
        assert entry["weight"] >= 0, entry["feature"]
        assert entry["monotone"] == directions[entry["feature"]]


def test_weights_of_evidence_run_the_declared_way(artifacts) -> None:
    """The binning, not the sample, decides which direction is worse."""
    for entry in artifacts.metrics["champion"]["table"]["characteristics"]:
        if entry["monotone"] == 0:
            continue
        weights = [b["woe"] for b in entry["bins"][1:]]
        expected = sorted(weights, reverse=entry["monotone"] < 0)
        assert weights == expected, entry["feature"]


def test_no_protected_characteristic_reached_the_model(artifacts) -> None:
    assert artifacts.metrics["fairness"]["protected_features_present"] == []


def test_application_only_features_were_left_out(artifacts) -> None:
    """Columns that are constant in the history teach nothing and hide their absence."""
    dropped = set(artifacts.metrics["features_dropped"])
    assert {
        "doc_min_conf",
        "contact_change_days",
        "application_count_12m",
        "findings_max_severity",
    } <= dropped
    assert not dropped & set(artifacts["features"])


def test_the_splits_never_overlap_in_time(artifacts) -> None:
    splits = artifacts.metrics["dataset"]["splits"]
    assert max(splits["train"]["cohorts"]) < min(splits["valid"]["cohorts"])
    assert max(splits["valid"]["cohorts"]) < min(splits["test"]["cohorts"])


def test_censored_accounts_were_excluded(artifacts) -> None:
    """An account without a finished performance window is not a good account."""
    assert artifacts.metrics["dataset"]["censored_excluded"] > 0


def test_the_calibrator_matches_the_event_count_rule(artifacts) -> None:
    calibration = artifacts.metrics["champion"]["calibration"]
    events = calibration["fitted_on"]["events"]
    assert calibration["method"] == ("isotonic" if events >= ISOTONIC_MIN_EVENTS else "platt")


def test_grades_get_worse_as_the_observed_rate_rises(artifacts) -> None:
    rows = [
        r
        for r in artifacts.metrics["champion"]["grades"]["test"]
        if r["observed_rate"] is not None and r["n"] >= 40
    ]
    assert rows[0]["observed_rate"] < rows[-1]["observed_rate"]


def test_the_card_carries_every_required_section(artifacts) -> None:
    card = (artifacts.path / "card.md").read_text()
    for section in REQUIRED_CARD_SECTIONS:
        assert f"## {section}" in card, section


def test_the_card_states_the_hold_out_numbers(artifacts) -> None:
    card = (artifacts.path / "card.md").read_text()
    held = artifacts.metrics["champion"]["performance"]["test"]
    assert f"{held['auc']:.4f}" in card
    assert f"{held['calibration_slope']:.3f}" in card


def test_the_card_names_the_rollback_path(artifacts) -> None:
    card = (artifacts.path / "card.md").read_text()
    assert "latest.txt" in card
    assert artifacts.version in card


def test_every_artifact_the_spec_names_is_present(artifacts) -> None:
    for name in ("champion", "challenger", "calibrator", "ood"):
        assert name in artifacts.objects, name
    assert (artifacts.path / "card.md").is_file()
    assert (artifacts.path / "metrics.json").is_file()
