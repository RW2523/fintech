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

from cio_common.hashing import canonical_json, sha256
from cio_common.ids import derived_id
from ml.common import registry
from ml.common.registry import Artifacts
from ml.credit_risk.calibration import grade_of
from ml.credit_risk.explain import Driver, drivers_for, reason_codes

FAMILY = "credit_risk"

#: docs/06 §CONDUCT — the deterministic conduct score, computed here from
#: feature values so that the number and the formula that produced it are
#: recorded together. Weights sum to 1.
CONDUCT_TERMS: tuple[tuple[str, float, str], ...] = (
    ("ontime_rate_24m", 0.45, "higher_is_better"),
    ("arrears_events_12m", 0.25, "count_penalty"),
    ("months_since_last_arrears", 0.15, "recency_credit"),
    ("restructures_36m", 0.10, "count_penalty"),
    ("facilities_new_6m", 0.05, "count_penalty"),
)

CONDUCT_FORMULA_VERSION = "conduct/1.0"

#: A member with no prior facility has no conduct record. Scoring them as
#: perfect would reward the absence of evidence, and scoring them as zero would
#: punish a first-time borrower for being one, so the neutral midpoint is used
#: and the missing inputs are named in the calculation.
CONDUCT_NEUTRAL = 0.5

#: Presence of this feature is what says a repayment record exists at all. The
#: count terms all read zero for a member who has never borrowed, which looks
#: identical to a member who borrowed and never missed. Without this gate a
#: first-time applicant scores a perfect 1.0 for conduct they have never had
#: the chance to demonstrate, and that number would then feed the weighted
#: score as though it were earned.
CONDUCT_EVIDENCE = "ontime_rate_24m"


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
class Calculation:
    """A deterministic number and the arithmetic behind it."""

    calc_id: str
    name: str
    value: float
    formula: str
    inputs: dict[str, Any]
    missing: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "calc_id": self.calc_id,
            "name": self.name,
            "value": round(self.value, 6),
            "formula": self.formula,
            "inputs": self.inputs,
            "missing_inputs": list(self.missing),
        }


@dataclass(frozen=True, slots=True)
class Prediction:
    """Everything `POST /risk/score` returns, before evidence is attached."""

    model_run_id: str
    version: str
    champion: ModelOutput
    challenger: ModelOutput
    conduct: Calculation
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
            "conduct_score": round(self.conduct.value, 6),
            "conduct_calc_id": self.conduct.calc_id,
            "conduct_calculation": self.conduct.as_dict(),
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


def conduct_score(values: Mapping[str, Any]) -> Calculation:
    """The CONDUCT factor's deterministic score, in [0, 1]."""
    used: dict[str, Any] = {}
    missing: list[str] = []
    total = 0.0
    weight_seen = 0.0

    if _as_float(values.get(CONDUCT_EVIDENCE)) is None:
        calc_id = derived_id("calc", CONDUCT_FORMULA_VERSION, b"no-repayment-history")
        return Calculation(
            calc_id=calc_id,
            name="conduct_score",
            value=CONDUCT_NEUTRAL,
            formula="neutral: no repayment record to score",
            inputs={},
            missing=tuple(name for name, _, _ in CONDUCT_TERMS),
        )

    never_in_arrears = _as_float(values.get("arrears_events_12m")) == 0.0

    for name, weight, kind in CONDUCT_TERMS:
        raw = _as_float(values.get(name))
        if raw is None and kind == "recency_credit" and never_in_arrears:
            # No date of last arrears because there were none. That is the best
            # possible record, not an absent one, and dropping the term would
            # quietly hand its weight to the others.
            used[name] = "no arrears on record"
            total += weight
            weight_seen += weight
            continue
        if raw is None:
            missing.append(name)
            continue
        if kind == "higher_is_better":
            term = min(max(raw, 0.0), 1.0)
        elif kind == "recency_credit":
            # Twenty-four clean months is treated as a clean record; the credit
            # accrues in proportion up to that point.
            term = min(max(raw, 0.0), 24.0) / 24.0
        else:
            # Each event costs a third of the term, so three wipe it out.
            term = max(0.0, 1.0 - raw / 3.0)
        used[name] = raw
        total += weight * term
        weight_seen += weight

    value = total / weight_seen if weight_seen else CONDUCT_NEUTRAL
    formula = " + ".join(f"{w:g}·f({n})" for n, w, _ in CONDUCT_TERMS)
    calc_id = derived_id("calc", CONDUCT_FORMULA_VERSION, canonical_json(used))
    return Calculation(
        calc_id=calc_id,
        name="conduct_score",
        value=float(value),
        formula=f"({formula}) / Σw over available terms",
        inputs=used,
        missing=tuple(missing),
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

    def predict(self, values: Mapping[str, Any]) -> Prediction:
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
            conduct=conduct_score(values),
            drivers=drivers,
            reason_codes=reason_codes(drivers),
            inputs_digest=digest,
            features_used=list(self.champion.features),
        )


@lru_cache(maxsize=4)
def loaded(version: str | None = None) -> CreditRiskModel:
    """The process-wide model, loaded on first use."""
    return CreditRiskModel.load(version)
