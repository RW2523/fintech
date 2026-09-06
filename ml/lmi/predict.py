"""Serving the early-warning models (docs/07 §4.4).

Loads one version of the artifacts and scores a member's feature vector at
every horizon, with an interval and the drivers behind it.

The drivers matter as much as the number. A member is going to be telephoned
about this, and "your probability is 0.32" is not a conversation. "Your salary
deduction missed two cycles and your savings stopped" is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ml.common import registry
from ml.lmi.conformal import Intervals
from ml.lmi.dataset import HORIZONS

__all__ = ["FAMILY", "EarlyWarningModel", "Score"]

FAMILY = "lmi_early_warning"

#: How many drivers are reported. More than a handful is a list nobody reads,
#: and a driver ranked eighth is not what the score turned on.
TOP_DRIVERS = 5


@dataclass
class Score:
    """One member, at one horizon."""

    horizon: int
    probability: float
    lower: float
    upper: float
    nominal: float
    band: str
    #: How many calibration members sat in this band. A band nobody was in
    #: gives an interval of nothing.
    band_n: int
    drivers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def outside_interval(self) -> bool:
        """Whether the model's estimate falls outside the measured rate.

        The probability is the model's estimate; the interval is the rate
        actually observed among members it scored alike. When the first sits
        outside the second the model is miscalibrated in that band, and saying
        so is more use to a reader than quietly moving one to fit the other.
        """
        return not (self.lower <= self.probability <= self.upper)

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "horizon_days": self.horizon,
            "probability": round(self.probability, 4),
            "interval": {
                "lower": round(self.lower, 4),
                "upper": round(self.upper, 4),
                "nominal": self.nominal,
                "band": self.band,
                "band_n": self.band_n,
            },
            "drivers": self.drivers,
        }
        if self.band_n and self.outside_interval:
            body["calibration_warning"] = (
                f"the model says {self.probability:.3f}; members it scored in this band "
                f"went late between {self.lower:.3f} and {self.upper:.3f} of the time"
            )
        return body


@dataclass
class EarlyWarningModel:
    """One trained version, loaded and ready to score."""

    version: str
    features: list[str]
    models: dict[int, Any] = field(default_factory=dict)
    calibrators: dict[int, Any] = field(default_factory=dict)
    intervals: dict[int, Intervals] = field(default_factory=dict)
    survival: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    _explainer: Any = None

    @classmethod
    def load(cls, version: str | None = None) -> EarlyWarningModel:
        artifacts = registry.load(FAMILY, version)
        metrics = artifacts.metrics
        panel = metrics.get("panel") or {}

        model = cls(
            version=metrics.get("version", version or "unknown"),
            features=list(panel.get("features") or []),
            survival=dict(metrics.get("survival") or {}),
            metrics=metrics,
        )
        for horizon in HORIZONS:
            if (obj := artifacts.get(f"horizon_{horizon}")) is not None:
                model.models[horizon] = obj
            if (obj := artifacts.get(f"calibrator_{horizon}")) is not None:
                model.calibrators[horizon] = obj
            if (obj := artifacts.get(f"intervals_{horizon}")) is not None:
                model.intervals[horizon] = obj
        return model

    @property
    def horizons(self) -> list[int]:
        return sorted(self.models)

    def _vector(self, features: dict[str, float]) -> np.ndarray:
        """The feature vector, in the order the model was trained on.

        A feature the caller did not supply is zero, and the score says how
        many were missing: a vector assembled from half the features is a
        different member from the one the model was trained to recognise.
        """
        return np.asarray([[float(features.get(name, 0.0)) for name in self.features]], dtype=float)

    def missing(self, features: dict[str, float]) -> list[str]:
        return [name for name in self.features if name not in features]

    def score(self, features: dict[str, float], *, horizons: list[int] | None = None) -> list[Score]:
        """Every horizon, or the ones asked for."""
        vector = self._vector(features)
        wanted = horizons or self.horizons
        drivers = self._drivers(vector, features)

        out: list[Score] = []
        for horizon in wanted:
            model = self.models.get(horizon)
            if model is None:
                continue
            raw = float(model.predict_proba(vector)[0, 1])
            calibrator = self.calibrators.get(horizon)
            probability = float(calibrator.predict([raw])[0]) if calibrator else raw

            intervals = self.intervals.get(horizon)
            if intervals is None:
                out.append(
                    Score(
                        horizon=horizon,
                        probability=probability,
                        lower=0.0,
                        upper=1.0,
                        nominal=0.0,
                        band="unfitted",
                        band_n=0,
                        drivers=drivers,
                    )
                )
                continue

            interval = intervals.for_probability(probability)
            out.append(
                Score(
                    horizon=horizon,
                    probability=probability,
                    lower=interval.lower,
                    upper=interval.upper,
                    nominal=interval.nominal,
                    band=interval.band,
                    band_n=interval.n,
                    drivers=drivers,
                )
            )
        return out

    def _drivers(self, vector: np.ndarray, features: dict[str, float]) -> list[dict[str, Any]]:
        """What moved this member's score, from SHAP over the 30-day model.

        One horizon's drivers rather than four sets: the features that push a
        member towards trouble are the same at every distance, and four lists
        that mostly agree is four times the reading for no more information.

        Falls back to the model's global importances when SHAP is absent,
        labelled as such: a global importance is not a reason about this
        member, and presenting it as one would be a lie in the officer's hand.
        """
        model = self.models.get(30) or (self.models.get(self.horizons[0]) if self.horizons else None)
        if model is None:
            return []

        try:
            import shap

            if self._explainer is None:
                self._explainer = shap.TreeExplainer(model)
            values = np.asarray(self._explainer.shap_values(vector))
            if values.ndim == 3:
                values = values[..., 1]
            contributions = values.reshape(-1)
            kind = "shap"
        except Exception:
            contributions = np.asarray(model.feature_importances_, dtype=float)
            kind = "global_importance"

        ranked = sorted(
            zip(self.features, contributions, strict=False),
            key=lambda pair: -abs(float(pair[1])),
        )
        return [
            {
                "feature": name,
                "value": round(float(features.get(name, 0.0)), 4),
                "contribution": round(float(weight), 5),
                "direction": "raises" if float(weight) > 0 else "lowers",
                "kind": kind,
            }
            for name, weight in ranked[:TOP_DRIVERS]
            if abs(float(weight)) > 0
        ]
