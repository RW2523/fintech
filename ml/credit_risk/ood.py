"""Is this application like the ones the model was trained on?

A probability of default is only meaningful for a member who resembles the
training population. An isolation forest gives a cheap, honest answer to "have
we seen anything like this before", and a high score is a reason to send the
case to a person rather than to distrust the applicant.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler


@dataclass
class OutOfDistribution:
    """Isolation forest on standardised features, scored into [0, 1]."""

    features: list[str]
    imputer: SimpleImputer
    scaler: StandardScaler
    forest: IsolationForest
    low: float
    high: float

    @classmethod
    def fit(cls, frame: pd.DataFrame, features: list[str], *, seed: int = 20260906) -> OutOfDistribution:
        raw = frame[features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        imputer = SimpleImputer(strategy="median", keep_empty_features=True).fit(raw)
        scaler = StandardScaler().fit(imputer.transform(raw))
        x = scaler.transform(imputer.transform(raw))
        forest = IsolationForest(n_estimators=300, contamination="auto", random_state=seed).fit(x)
        # `score_samples` is unbounded and its scale means nothing on its own,
        # so it is anchored to the training range: 0 is typical, 1 is stranger
        # than anything the model was fitted on.
        raw_scores = -forest.score_samples(x)
        return cls(
            features=features,
            imputer=imputer,
            scaler=scaler,
            forest=forest,
            low=float(np.percentile(raw_scores, 5)),
            high=float(np.percentile(raw_scores, 99)),
        )

    def score(self, frame: pd.DataFrame) -> np.ndarray:
        raw = frame[self.features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        x = self.scaler.transform(self.imputer.transform(raw))
        values = -self.forest.score_samples(x)
        spread = max(self.high - self.low, 1e-9)
        return np.clip((values - self.low) / spread, 0.0, 1.0)
