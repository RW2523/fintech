"""Scoring one case from a feature snapshot (docs/07 §2.3).

The risk service is a thin wrapper over this: load the artifacts once, hand it
a snapshot's values, get back a probability, a grade, the drivers behind them
and an approved reason code for each. Keeping the scoring here rather than in
the service means the training run and the serving path share one
implementation, and a model card describes what actually runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
from cio_dff.factors import FactorScore, conduct_score

from cio_common.hashing import canonical_json, sha256
from cio_common.ids import derived_id
from ml.common import registry
from ml.common.registry import Artifacts
from ml.credit_risk.calibration import grade_of
from ml.credit_risk.explain import Driver, drivers_for, reason_codes

FAMILY = "credit_risk"

#: How many payment events the on-time rate was computed from. The Decision
#: Factor Framework parks CONDUCT at neutral below six months of history, and
#: the feature registry carries no months-of-history feature, so the count the
#: feature snapshot records as provenance is what answers the question.
HISTORY_PROVENANCE = ("ontime_rate_24m", "due_events")


@dataclass(frozen=True, slots=True)
class ModelOutput:
    """One model's view of one case."""

    model: str
    version: str
    pd_12m: float
    grade: str
    calibration: dict[str, Any]
    ood_score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        body = {
            "model": self.model,
            "version": self.version,
            "pd_12m": round(self.pd_12m, 6),
            "grade": self.grade,
            "calibration": self.calibration,
        }
        if self.ood_score is not None:
            body["ood_score"] = round(self.ood_score, 4)
        return body


@dataclass(frozen=True, slots=True)
class Prediction:
    """Everything `POST /risk/score` returns, before evidence is attached."""

    model_run_id: str
    version: str
    champion: ModelOutput
    challenger: ModelOutput
    conduct: FactorScore
    drivers: list[Driver]
    reason_codes: list[str]
    inputs_digest: str
    features_used: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_run_id": self.model_run_id,
            "version": self.version,
            "champion": self.champion.as_dict(),
            "challenger": self.challenger.as_dict(),
            "conduct_score": self.conduct.score,
            "conduct_calc_id": self.conduct.calc_id,
            "conduct_factor": self.conduct.as_contract(),
            "reason_codes": list(self.reason_codes),
            "drivers": [d.as_dict() for d in self.drivers],
            "inputs_digest": self.inputs_digest,
        }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(number) else number


def history_months(provenance: Mapping[str, Any] | None) -> int:
    """Months of repayment record behind the on-time rate."""
    if not provenance:
        return 0
    feature, key = HISTORY_PROVENANCE
    entry = provenance.get(feature) or {}
    try:
        return int(entry.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def conduct_of(
    values: Mapping[str, Any],
    *,
    grade: str,
    provenance: Mapping[str, Any] | None = None,
) -> FactorScore:
    """The CONDUCT factor score, from the Decision Factor Framework formula.

    The arithmetic lives in `cio_dff` because the policy engine consumes this
    number and the risk service produces it. Two implementations of one
    formula would be two chances for a decision to be irreproducible, so there
    is one, and it is asked for a derived calculation id: re-scoring the same
    frozen snapshot must reproduce the reference as well as the value.

    A count of zero arrears from a member who has never borrowed is an absence
    of evidence, not a clean record, and the framework's thin-file branch is
    what stops it being read as one.
    """
    arrears = _as_float(values.get("arrears_events_12m")) or 0.0
    since = _as_float(values.get("months_since_last_arrears"))
    return conduct_score(
        ontime_rate_24m=_as_float(values.get("ontime_rate_24m")) or 0.0,
        any_arrears=arrears > 0 or since is not None,
        months_since_last_arrears=since if since is not None else 0.0,
        restructures_36m=int(_as_float(values.get("restructures_36m")) or 0),
        grade=grade,
        history_months=history_months(provenance),
        derived=True,
    )


class CreditRiskModel:
    """The trained artifacts, loaded once and reused."""

    def __init__(self, artifacts: Artifacts) -> None:
        self.artifacts = artifacts
        self.version = artifacts.version
        self.features: list[str] = artifacts["features"]
        self.champion = artifacts["champion"]
        self.challenger = artifacts["challenger"]
        self.challenger_features: list[str] = artifacts["challenger_features"]
        self.calibrator = artifacts["calibrator"]
        self.challenger_calibrator = artifacts["challenger_calibrator"]
        self.ood = artifacts["ood"]

    @classmethod
    def load(cls, version: str | None = None) -> CreditRiskModel:
        return cls(registry.load(FAMILY, version))

    def _frame(self, values: Mapping[str, Any]) -> pd.DataFrame:
        row = {name: values.get(name) for name in self.features}
        frame = pd.DataFrame([row])
        for name in self.features:
            if name not in self.champion.binning.characteristics:
                continue
            if self.champion.binning.characteristics[name].kind == "numeric":
                frame[name] = pd.to_numeric(frame[name], errors="coerce")
        return frame

    def predict(
        self,
        values: Mapping[str, Any],
        *,
        provenance: Mapping[str, Any] | None = None,
    ) -> Prediction:
        frame = self._frame(values)
        digest = sha256(canonical_json({k: values.get(k) for k in sorted(self.features)}))[:16]

        champion_pd = float(self.calibrator.transform(self.champion.score(frame))[0])
        ood = float(self.ood.score(frame)[0])

        raw = np.clip(
            self.challenger.predict(frame[self.challenger_features].apply(pd.to_numeric, errors="coerce")),
            1e-6,
            1 - 1e-6,
        )
        challenger_pd = float(self.challenger_calibrator.transform(np.log(raw / (1 - raw)))[0])

        contributions = self.champion.contributions(frame).iloc[0]
        drivers = drivers_for(contributions, frame.iloc[0])

        return Prediction(
            model_run_id=derived_id("mr", FAMILY, self.version, digest),
            version=self.version,
            champion=ModelOutput(
                model="woe_scorecard",
                version=self.version,
                pd_12m=champion_pd,
                grade=grade_of(champion_pd),
                calibration=self.calibrator.parameters,
                ood_score=ood,
            ),
            challenger=ModelOutput(
                model="lightgbm_monotone",
                version=self.version,
                pd_12m=challenger_pd,
                grade=grade_of(challenger_pd),
                calibration=self.challenger_calibrator.parameters,
            ),
            conduct=conduct_of(values, grade=grade_of(champion_pd), provenance=provenance),
            drivers=drivers,
            reason_codes=reason_codes(drivers),
            inputs_digest=digest,
            features_used=list(self.champion.features),
        )


@lru_cache(maxsize=4)
def loaded(version: str | None = None) -> CreditRiskModel:
    """The process-wide model, loaded on first use."""
    return CreditRiskModel.load(version)
