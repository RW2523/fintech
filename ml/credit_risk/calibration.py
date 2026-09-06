"""Turning a score into a probability, and a probability into a grade.

A model that ranks well can still be wrong about how likely default is, and
the policy engine acts on the probability, not the rank. So calibration is a
separate, fitted step on data the model was not trained on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

#: docs/07 §2.2 — isotonic needs enough events to place its steps; below this
#: it fits the calibration sample instead of the relationship, so Platt's two
#: parameters are the safer choice.
ISOTONIC_MIN_EVENTS = 300

#: docs/07 §2.2 grade bands on the calibrated probability of default.
GRADE_BANDS: tuple[tuple[str, float], ...] = (
    ("A", 0.015),
    ("B", 0.030),
    ("C", 0.060),
    ("D", 0.120),
    ("E", float("inf")),
)


def grade_of(pd_12m: float) -> str:
    for grade, ceiling in GRADE_BANDS:
        if pd_12m < ceiling:
            return grade
    return GRADE_BANDS[-1][0]


def grades_of(values: np.ndarray) -> np.ndarray:
    return np.array([grade_of(float(v)) for v in np.asarray(values, dtype=float)])


@dataclass
class Calibrator:
    """Maps a model's log-odds to a calibrated probability."""

    method: str
    events: int
    n: int
    _platt: LogisticRegression | None = None
    _isotonic: IsotonicRegression | None = None

    @classmethod
    def fit(cls, scores: np.ndarray, y: np.ndarray) -> Calibrator:
        scores = np.asarray(scores, dtype=float).reshape(-1, 1)
        y = np.asarray(y, dtype=int)
        events = int(y.sum())
        if events >= ISOTONIC_MIN_EVENTS:
            fitted = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            fitted.fit(scores.ravel(), y)
            return cls(method="isotonic", events=events, n=len(y), _isotonic=fitted)
        # `C` large enough to leave the two parameters essentially unpenalised:
        # Platt is already only a slope and an intercept.
        platt = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000).fit(scores, y)
        return cls(method="platt", events=events, n=len(y), _platt=platt)

    def transform(self, scores: np.ndarray) -> np.ndarray:
        scores = np.asarray(scores, dtype=float).reshape(-1, 1)
        if self._isotonic is not None:
            return np.clip(self._isotonic.predict(scores.ravel()), 1e-6, 1.0 - 1e-6)
        assert self._platt is not None
        return self._platt.predict_proba(scores)[:, 1]

    @property
    def parameters(self) -> dict[str, Any]:
        if self._platt is not None:
            return {
                "method": "platt",
                "slope": round(float(self._platt.coef_[0][0]), 5),
                "intercept": round(float(self._platt.intercept_[0]), 5),
                "fitted_on": {"n": self.n, "events": self.events},
            }
        return {"method": "isotonic", "fitted_on": {"n": self.n, "events": self.events}}
