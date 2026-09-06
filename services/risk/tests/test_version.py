"""T-032 — `GET /version` (docs/03 §1, docs/07 §2.3).

The CaseSnapshot freeze stamps whatever this returns, so a decision can be
reconstructed against the model that made it.
"""

from __future__ import annotations

from typing import Any

from httpx import ASGITransport, AsyncClient


async def test_version_reports_the_model_not_the_service_build(client: AsyncClient) -> None:
    body = (await client.get("/version")).json()
    assert body["service"] == "risk"
    assert body["available"] is True
    assert body["version"] != body["service_build"]
    assert body["version"].count(".") == 2


async def test_version_names_both_models(client: AsyncClient) -> None:
    body = (await client.get("/version")).json()
    assert body["champion"] == "woe_scorecard"
    assert body["challenger"] == "lightgbm_monotone"
    assert body["characteristics"]


async def test_version_carries_the_hold_out_result(client: AsyncClient) -> None:
    """An operator should not need the card to see what was accepted."""
    holdout = (await client.get("/version")).json()["holdout"]
    assert 0.5 < holdout["auc"] <= 1.0
    assert holdout["events"] > 0
    assert holdout["calibration_slope"] > 0


async def test_version_states_how_the_probability_was_calibrated(client: AsyncClient) -> None:
    calibration = (await client.get("/version")).json()["calibration"]
    assert calibration["method"] in {"platt", "isotonic"}
    assert calibration["fitted_on"]["events"] >= 0


async def test_a_missing_model_is_reported_not_guessed(db: Any, monkeypatch: Any) -> None:
    """Fail safe: a case frozen without a model must look different."""
    from app import scoring
    from app.main import app
    from ml.common.registry import ArtifactError

    def explode(*_: object, **__: object) -> None:
        raise ArtifactError("credit_risk has no trained artifacts; run its train.py")

    monkeypatch.setattr(scoring, "model", explode)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://risk") as http:
        body = (await http.get("/version")).json()

    assert body["version"] == "0.0.0-unavailable"
    assert body["available"] is False
    assert "train.py" in body["detail"]


async def test_health_still_answers_without_touching_the_model(client: AsyncClient) -> None:
    body = (await client.get("/health")).json()
    assert body["status"] == "ok"
    assert body["service"] == "risk"
