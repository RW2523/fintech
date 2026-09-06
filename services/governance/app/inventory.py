"""The model inventory, read from the cards (docs/07 §6, docs/12 §5).

Every trained model writes a card beside its artifacts. The inventory is that
set of cards, listed: what is trained, which version serving points at, when
it was fitted and what it reported. It is read from disk rather than kept in a
table, because a table can disagree with the artifact and the artifact is what
runs.
"""

from __future__ import annotations

from typing import Any

from cio_common.models import add_model_path

add_model_path()

from ml.common import registry  # noqa: E402

__all__ = ["FAMILIES", "inventory", "model_card"]

#: The families the platform trains. Each has a `train.py` and an artifact
#: directory; a family with neither is listed as untrained rather than hidden.
FAMILIES = ("credit_risk", "fraud", "lmi")


def _headline(metrics: dict[str, Any]) -> dict[str, Any]:
    """The few numbers an operator wants without opening the card."""
    champion = metrics.get("champion") or {}
    held = (champion.get("performance") or {}).get("test") or {}
    if held:
        return {
            "holdout_auc": held.get("auc"),
            "holdout_events": held.get("events"),
            "calibration_slope": held.get("calibration_slope"),
        }
    if "flag_rate" in metrics:
        return {
            "flag_rate": metrics.get("flag_rate"),
            "threshold": metrics.get("threshold"),
            "flagged": metrics.get("flagged"),
        }
    return {}


def inventory() -> list[dict[str, Any]]:
    """One row per family, newest version first within each."""
    rows: list[dict[str, Any]] = []
    for family in FAMILIES:
        versions = registry.versions(family)
        if not versions:
            rows.append(
                {
                    "family": family,
                    "trained": False,
                    "versions": [],
                    "serving": None,
                    "detail": "no artifacts; run its train.py",
                }
            )
            continue
        try:
            serving = registry.latest_version(family)
        except registry.ArtifactError as exc:
            serving = None
            detail: str | None = str(exc)
        else:
            detail = None

        entries: list[dict[str, Any]] = []
        for version in reversed(versions):
            artifacts = registry.load(family, version)
            metrics = artifacts.metrics
            entries.append(
                {
                    "version": version,
                    "serving": version == serving,
                    "trained_at": metrics.get("trained_at"),
                    "card": f"/governance/models/{family}/{version}/card",
                    "headline": _headline(metrics),
                    "features": len(metrics.get("features_used") or metrics.get("features") or []),
                }
            )
        rows.append(
            {"family": family, "trained": True, "serving": serving, "versions": entries, "detail": detail}
        )
    return rows


def model_card(family: str, version: str | None = None) -> dict[str, Any]:
    """One card, as written by the training run."""
    artifacts = registry.load(family, version)
    return {
        "family": family,
        "version": artifacts.version,
        "card": (artifacts.path / "card.md").read_text(),
        "metrics": artifacts.metrics,
    }
