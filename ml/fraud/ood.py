"""The isolation forest behind the advisory anomaly score (docs/07 §3)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

__all__ = ["AnomalyDetector"]


@dataclass
class AnomalyDetector:
    """Isolation forest on the standardised case vector, scored into [0, 1]."""

    features: list[str]
    imputer: SimpleImputer
    scaler: StandardScaler
    forest: IsolationForest
    low: float
    high: float
    #: Columns that never varied while training. Kept for the record rather
    #: than silently forgotten: a constant column teaches the model nothing,
    #: and a reader of the card should be able to see which of the declared
    #: inputs were never actually populated.
    dropped: tuple[str, ...] = ()

    @classmethod
    def fit(cls, frame: pd.DataFrame, features: list[str], *, seed: int = 20260906) -> AnomalyDetector:
        numeric = frame[features].apply(pd.to_numeric, errors="coerce")
        varying = [name for name in features if numeric[name].nunique(dropna=True) > 1]
        dropped = tuple(name for name in features if name not in varying)
        features = varying
        raw = numeric[features].to_numpy(dtype=float)
        imputer = SimpleImputer(strategy="median", keep_empty_features=True).fit(raw)
        scaler = StandardScaler().fit(imputer.transform(raw))
        x = scaler.transform(imputer.transform(raw))
        forest = IsolationForest(n_estimators=400, contamination="auto", random_state=seed).fit(x)
        # `score_samples` is unbounded and its units mean nothing on their own,
        # so the scale is anchored to the training spread: 0 is an ordinary
        # case, 1 is stranger than all but the strangest the model was fitted
        # on. Anchoring at the 99th rather than the maximum keeps one outlier
        # from compressing every other score toward zero.
        values = -forest.score_samples(x)
        return cls(
            features=features,
            imputer=imputer,
            scaler=scaler,
            forest=forest,
            low=float(np.percentile(values, 50)),
            high=float(np.percentile(values, 99)),
            dropped=dropped,
        )

    def score(self, frame: pd.DataFrame) -> np.ndarray:
        raw = frame[self.features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        x = self.scaler.transform(self.imputer.transform(raw))
        values = -self.forest.score_samples(x)
        spread = max(self.high - self.low, 1e-9)
        return np.clip((values - self.low) / spread, 0.0, 1.0)

    def score_one(self, row: dict[str, float]) -> float:
        return float(self.score(pd.DataFrame([row]))[0])
