"""T-031 — turning a score into a probability and a grade."""

from __future__ import annotations

import numpy as np
import pytest

from ml.credit_risk.calibration import (
    GRADE_BANDS,
    ISOTONIC_MIN_EVENTS,
    Calibrator,
    grade_of,
    grades_of,
)


def test_grade_bands_follow_the_policy() -> None:
    """docs/07 §2.2: A < 1.5%, B < 3%, C < 6%, D < 12%, E above."""
    assert grade_of(0.0) == "A"
    assert grade_of(0.0149) == "A"
    assert grade_of(0.015) == "B"
    assert grade_of(0.0299) == "B"
    assert grade_of(0.03) == "C"
    assert grade_of(0.0599) == "C"
    assert grade_of(0.06) == "D"
    assert grade_of(0.1199) == "D"
    assert grade_of(0.12) == "E"
    assert grade_of(1.0) == "E"


def test_grades_never_improve_as_risk_rises() -> None:
    order = "ABCDE"
    previous = 0
    for value in np.linspace(0.0, 0.5, 200):
        position = order.index(grade_of(float(value)))
        assert position >= previous
        previous = position


def test_the_bands_are_contiguous_and_cover_everything() -> None:
    ceilings = [c for _, c in GRADE_BANDS]
    assert ceilings == sorted(ceilings)
    assert ceilings[-1] == float("inf")


def test_a_small_calibration_sample_uses_platt() -> None:
    rng = np.random.default_rng(3)
    scores = rng.normal(0, 1, 800)
    y = rng.binomial(1, 1 / (1 + np.exp(-(scores - 3))))
    calibrator = Calibrator.fit(scores, y)
    assert y.sum() < ISOTONIC_MIN_EVENTS
    assert calibrator.method == "platt"
    assert set(calibrator.parameters) >= {"slope", "intercept", "fitted_on"}


def test_a_large_calibration_sample_uses_isotonic() -> None:
    rng = np.random.default_rng(4)
    scores = rng.normal(0, 1, 20000)
    y = rng.binomial(1, 1 / (1 + np.exp(-scores)))
    calibrator = Calibrator.fit(scores, y)
    assert y.sum() >= ISOTONIC_MIN_EVENTS
    assert calibrator.method == "isotonic"


def test_calibration_makes_the_average_prediction_match_the_rate() -> None:
    rng = np.random.default_rng(5)
    scores = rng.normal(0, 1.5, 4000)
    y = rng.binomial(1, 1 / (1 + np.exp(-(0.5 * scores - 2.5))))
    calibrator = Calibrator.fit(scores, y)
    assert calibrator.transform(scores).mean() == pytest.approx(y.mean(), abs=0.01)


def test_calibration_preserves_the_ranking() -> None:
    """Calibration changes the size of a probability, never its order."""
    rng = np.random.default_rng(6)
    scores = rng.normal(0, 1, 2000)
    y = rng.binomial(1, 1 / (1 + np.exp(-(scores - 2))))
    calibrated = Calibrator.fit(scores, y).transform(scores)
    order_before = np.argsort(scores)
    assert np.all(np.diff(calibrated[order_before]) >= -1e-12)


def test_probabilities_stay_inside_the_unit_interval() -> None:
    rng = np.random.default_rng(8)
    scores = rng.normal(0, 1, 3000)
    y = rng.binomial(1, 0.05, 3000)
    out = Calibrator.fit(scores, y).transform(np.array([-50.0, 0.0, 50.0]))
    assert ((out >= 0.0) & (out <= 1.0)).all()


def test_grades_of_handles_an_array() -> None:
    assert list(grades_of(np.array([0.001, 0.02, 0.2]))) == ["A", "B", "E"]
