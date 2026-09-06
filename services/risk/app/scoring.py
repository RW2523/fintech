"""Producing a risk score from a frozen snapshot (docs/07 §2.3).

The service is deliberately thin. The arithmetic lives in `ml.credit_risk`,
which is also what the training run used, so what was validated is what runs.
This module fetches the snapshot, calls the model, mints evidence for what it
cited, and records the run so a decision can point at it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.snapshots import FeatureSnapshot, SnapshotSource
from cio_common.errors import NotFound
from cio_common.ids import derived_id
from cio_common.models import add_model_path

add_model_path()

from ml.common.registry import ArtifactError  # noqa: E402
from ml.credit_risk.predict import CreditRiskModel, Prediction  # noqa: E402

__all__ = ["ScoreResult", "Scorer", "model", "model_version", "version_detail"]


@lru_cache(maxsize=2)
def model(version: str | None = None) -> CreditRiskModel:
    """The trained artifacts, loaded once per process."""
    return CreditRiskModel.load(version)


def model_version() -> str:
    """What `GET /version` reports, or why it cannot."""
    try:
        return model().version
    except (ArtifactError, FileNotFoundError):
        # Fail safe: an unloadable model is reported, never guessed at. The
        # snapshot freeze stamps this string, so a case scored without a model
        # stays visibly distinguishable from one scored with it.
        return "0.0.0-unavailable"


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """A prediction, its evidence, and how long it took."""

    prediction: Prediction
    snapshot: FeatureSnapshot
    evidence_refs: list[dict[str, Any]]
    latency_ms: float

    @property
    def evidence_ids(self) -> list[str]:
        return [ref["evidence_id"] for ref in self.evidence_refs]

    def as_contract(self) -> dict[str, Any]:
        body = self.prediction.as_dict()
        body["snapshot_id"] = self.snapshot.snapshot_id
        body["member_id"] = self.snapshot.member_id
        body["evidence_refs"] = self.evidence_refs
        # The CONDUCT factor cites the same evidence the score rested on, so a
        # reader of the factor alone can still reach the values behind it.
        body["conduct_factor"]["evidence_refs"] = self.evidence_ids
        body["latency_ms"] = round(self.latency_ms, 2)
        return body


#: A feature value is derived from the timeline and the core record rather
#: than read off a document, so it is an analytic result (docs/03 §2).
EVIDENCE_TYPE = "ANALYTIC_RESULT"

#: A feature snapshot is a deterministic computation over stored records, so
#: the evidence is certain about what it says: the uncertainty a decision has
#: to weigh lives in the model's probability, not in this reference.
EVIDENCE_CONFIDENCE = 1.0


def _evidence(snapshot: FeatureSnapshot, prediction: Prediction) -> list[dict[str, Any]]:
    """One EvidenceRef per feature the score actually rested on.

    Only the characteristics the champion uses are cited. Listing every value
    in the snapshot would make the evidence unreadable and would claim the
    model consulted things it never looked at.

    The ids are derived from the snapshot and the feature, so re-scoring the
    same snapshot cites the same evidence rather than minting a parallel set
    that says the same thing under new names.
    """
    refs: list[dict[str, Any]] = []
    for name in prediction.features_used:
        provenance = snapshot.provenance.get(name) or {}
        refs.append(
            {
                "schema": "evidence_ref/1.0",
                "evidence_id": derived_id("ev", snapshot.snapshot_id, name),
                "type": EVIDENCE_TYPE,
                "source_system": str(provenance.get("source") or "app_feature.feature_value"),
                "source_record_id": snapshot.snapshot_id,
                "locator": {"field_path": name},
                "value": snapshot.values.get(name),
                "confidence": EVIDENCE_CONFIDENCE,
                "captured_at": snapshot.as_of,
                "version": snapshot.registry_version or "features/1.0",
                "permitted_uses": ["UNDERWRITING"],
            }
        )
    return refs


class Scorer:
    """Fetch, score, and mint evidence."""

    def __init__(self, source: SnapshotSource, *, version: str | None = None) -> None:
        self._source = source
        self._version = version

    async def score(self, snapshot_id: str) -> ScoreResult:
        snapshot = await self._source.fetch(snapshot_id)
        try:
            loaded = model(self._version)
        except (ArtifactError, FileNotFoundError) as exc:
            # A version that was never trained is a bad request, not a crash.
            raise NotFound(f"no credit-risk model {self._version}") from exc
        started = time.perf_counter()
        prediction = loaded.predict(snapshot.values, provenance=snapshot.provenance)
        elapsed = (time.perf_counter() - started) * 1000.0
        return ScoreResult(
            prediction=prediction,
            snapshot=snapshot,
            evidence_refs=_evidence(snapshot, prediction),
            latency_ms=elapsed,
        )


def version_detail() -> dict[str, Any]:
    """What `GET /version` adds about the model this service serves.

    The CaseSnapshot freeze stamps `version` from here, so it names the model
    artifacts and not the service build: a decision has to be reconstructable
    against the model that made it (docs/03 §1).
    """
    loaded = model()
    metrics = loaded.artifacts.metrics
    held = metrics.get("champion", {}).get("performance", {}).get("test", {})
    return {
        "version": loaded.version,
        "service_build": "0.1.0",
        "available": True,
        "champion": "woe_scorecard",
        "challenger": "lightgbm_monotone",
        "characteristics": list(loaded.champion.features),
        "calibration": loaded.calibrator.parameters,
        "holdout": {
            "auc": held.get("auc"),
            "events": held.get("events"),
            "calibration_slope": held.get("calibration_slope"),
        },
        "trained_at": metrics.get("trained_at"),
    }
