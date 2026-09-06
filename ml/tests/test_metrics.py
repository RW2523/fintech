"""T-031 — the metrics a model card reports (docs/07 §2.2)."""

from __future__ import annotations

import numpy as np
import pytest

from ml.common.metrics import (
    auc_confidence_interval,
    calibration_slope_intercept,
    decile_lift,
    evaluate,
    ks_statistic,
)


@pytest.fixture
def graded() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(11)
    n = 4000
    risk = rng.uniform(0, 1, n)
    p = np.clip(0.01 + 0.3 * risk, 1e-4, 0.99)
    return rng.binomial(1, p), p


def test_a_perfect_ranking_gives_a_perfect_ks(graded) -> None:
    y, _ = graded
    assert ks_statistic(y, y.astype(float)) == pytest.approx(1.0)


def test_ks_is_undefined_without_both_outcomes() -> None:
    y = np.zeros(100, dtype=int)
    assert np.isnan(ks_statistic(y, np.linspace(0, 1, 100)))


def test_a_well_calibrated_model_has_a_slope_near_one(graded) -> None:
    y, p = graded
    slope, intercept = calibration_slope_intercept(y, p)
    assert slope == pytest.approx(1.0, abs=0.25)
    assert intercept == pytest.approx(0.0, abs=0.25)


def test_an_overconfident_model_has_a_slope_below_one(graded) -> None:
    """Predictions spread wider than the data supports pull the slope down."""
    y, p = graded
    log_odds = np.log(p / (1 - p))
    spread = 1 / (1 + np.exp(-(log_odds * 2.5)))
    slope, _ = calibration_slope_intercept(y, spread)
    assert slope < 0.8


def test_deciles_are_ordered_worst_first(graded) -> None:
    y, p = graded
    rows = decile_lift(y, p)
    assert len(rows) == 10
    assert rows[0]["mean_pd"] > rows[-1]["mean_pd"]
    assert sum(r["n"] for r in rows) == len(y)
    assert sum(r["events"] for r in rows) == int(y.sum())


def test_the_confidence_interval_brackets_the_estimate(graded) -> None:
    from sklearn.metrics import roc_auc_score

    y, p = graded
    low, high = auc_confidence_interval(y, p, draws=400)
    assert low < roc_auc_score(y, p) < high


def test_a_smaller_sample_gives_a_wider_interval() -> None:
    rng = np.random.default_rng(12)
    y = rng.binomial(1, 0.1, 4000)
    p = np.clip(0.1 + 0.3 * y + rng.normal(0, 0.15, 4000), 1e-3, 0.99)
    wide = auc_confidence_interval(y[:200], p[:200], draws=400)
    narrow = auc_confidence_interval(y, p, draws=400)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_evaluate_reports_every_field_a_card_needs(graded) -> None:
    y, p = graded
    body = evaluate(y, p).as_dict()
    assert set(body) == {
        "n",
        "events",
        "event_rate",
        "auc",
        "auc_ci_low",
        "auc_ci_high",
        "pr_auc",
        "ks",
        "brier",
        "calibration_slope",
        "calibration_intercept",
        "deciles",
    }


def test_evaluate_survives_a_split_with_one_outcome() -> None:
    y = np.zeros(500, dtype=int)
    body = evaluate(y, np.full(500, 0.02))
    assert np.isnan(body.auc)
    assert body.brier == pytest.approx(0.0004)


def test_the_result_is_json_serialisable(graded) -> None:
    import json

    y, p = graded
    json.dumps(evaluate(y, p).as_dict())
