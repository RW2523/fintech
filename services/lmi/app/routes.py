"""lmi-service endpoints (docs/07 §4).

Serves the temporal features and runs the materialisation. Nothing here judges
a member: it reports what their behaviour looked like on a day, and says when
it has too little history to say anything.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.anomaly import score_book
from app.config import load_config
from app.db import session
from app.detect import latest_features, run_detection
from app.materialise import materialise
from cio_common.errors import NotFound, ValidationFailed

router = APIRouter(tags=["lmi"])


class MaterialiseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: str | None = None
    member_ids: list[str] | None = Field(default=None, max_length=10_000)


def _today() -> date:
    return datetime.now(UTC).date()


@router.post("/lmi/materialise", summary="Compute a day's temporal features")
async def run_materialisation(body: MaterialiseRequest) -> dict[str, Any]:
    """docs/07 §4.2 — the nightly job, and the event-triggered one.

    Takes an explicit member list when something happened to those members, and
    the whole book when it is the nightly run. Both write the same rows, so a
    member refreshed at noon is not a different kind of row from one refreshed
    at midnight.
    """
    as_of = date.fromisoformat(body.as_of) if body.as_of else _today()
    async with session() as db:
        run = await materialise(db, as_of=as_of, member_ids=body.member_ids)
        await db.commit()
    return run.as_dict()


@router.get("/lmi/features/{member_id}", summary="One member's temporal features")
async def features(member_id: str, as_of: str | None = None) -> dict[str, Any]:
    """The most recent feature set at or before `as_of`.

    At or before, not exactly on: a member whose features were last computed on
    Tuesday has features on Wednesday, and refusing to answer would make every
    reader implement this fallback themselves.
    """
    cutoff = date.fromisoformat(as_of) if as_of else _today()
    async with session() as db:
        row = (
            (
                await db.execute(
                    text("""
            SELECT * FROM app_lmi.temporal_features
             WHERE member_id = :member_id AND as_of <= :cutoff
             ORDER BY as_of DESC LIMIT 1
        """),
                    {"member_id": member_id, "cutoff": cutoff},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise NotFound(
            f"no temporal features for {member_id!r} at or before {cutoff.isoformat()}",
            member_id=member_id,
        )

    body = dict(row)
    for field in ("features", "baselines", "seasonal"):
        if isinstance(body.get(field), str):
            body[field] = json.loads(body[field])
    body["as_of"] = body["as_of"].isoformat()
    body["computed_at"] = body["computed_at"].isoformat()
    # Said rather than left to be worked out from a date: a reader asking about
    # today and getting Tuesday's numbers should know that is what happened.
    body["stale_days"] = (cutoff - row["as_of"]).days
    return body


@router.get("/lmi/runs", summary="What the materialisation has done")
async def runs(limit: int = Query(default=30, ge=1, le=365)) -> dict[str, Any]:
    """A run that covered half the book is visible as such rather than as a
    quiet night."""
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("SELECT * FROM app_lmi.materialisation ORDER BY started_at DESC LIMIT :limit"),
                    {"limit": limit},
                )
            )
            .mappings()
            .all()
        )
    return {"count": len(rows), "runs": [dict(row) for row in rows]}


class DetectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: str | None = None
    member_ids: list[str] | None = Field(default=None, max_length=10_000)
    #: Only announce changes that began on or after this day, so a nightly run
    #: does not re-raise a drift that started last year and was dealt with.
    since: str | None = None


@router.post("/lmi/detect", summary="Find where members' behaviour changed")
async def run_change_detection(body: DetectRequest) -> dict[str, Any]:
    """docs/07 §4.3 — CUSUM, confirmed by PELT, emitted as events.

    Nothing here decides what to do about a change. Keeping detection and
    action apart is what lets a detection be reviewed without arguing about the
    intervention it triggered.
    """
    as_of = date.fromisoformat(body.as_of) if body.as_of else _today()
    since = date.fromisoformat(body.since) if body.since else None
    async with session() as db:
        run = await run_detection(db, as_of=as_of, member_ids=body.member_ids, since=since)
        await db.commit()
    return run.as_dict()


@router.get("/lmi/anomaly", summary="How unlike the book each member looks")
async def anomaly(
    as_of: str | None = None, limit: int = Query(default=5000, ge=1, le=20000)
) -> dict[str, Any]:
    """Advisory only. It never raises a state change on its own, because nobody
    can say what it objected to."""
    cutoff = date.fromisoformat(as_of) if as_of else _today()
    settings = load_config().anomaly
    async with session() as db:
        rows = await latest_features(db, as_of=cutoff, limit=limit)

    scored = score_book(
        rows,
        contamination=float(settings.get("contamination", 0.02)),
        min_members=int(settings.get("min_members", 200)),
    )
    ranked = sorted(scored.scores.items(), key=lambda item: -item[1])[:50]
    return {
        "as_of": cutoff.isoformat(),
        **scored.as_dict(),
        "advisory": True,
        "most_unusual": [{"member_id": member, "anomaly_score": value} for member, value in ranked],
    }


@router.get("/lmi/config", summary="The parameters in force and what they were measured to do")
async def configuration() -> dict[str, Any]:
    """Served so an operator can see the thresholds without reading the image.

    `measured` is what the parameters achieved on a held-out half of the
    population, so a later run can be compared with a number rather than with
    somebody's memory.
    """
    settings = load_config()
    return {
        "version": settings.version,
        "signals": {
            name: {
                "drift": settings.signal(name).drift,
                "threshold": settings.signal(name).threshold,
                "direction": settings.signal(name).direction,
                "min_scale": settings.min_scale(name),
            }
            for name in settings.signals
        },
        "confirmation": {
            "window_days": settings.confirmation_window_days,
            "observations": settings.confirmation_observations,
        },
        "anomaly": settings.anomaly,
        "measured": settings.measured,
    }


# ---------------------------------------------------------------------------
# scoring (docs/07 §4.4)
# ---------------------------------------------------------------------------
_model: Any = None


def early_warning_model() -> Any:
    """The loaded model.

    Loaded once and kept: four boosted models plus their calibrators is tens of
    megabytes, and reloading per request would put that on the read path for no
    benefit.
    """
    global _model
    if _model is None:
        from ml.lmi.predict import EarlyWarningModel

        _model = EarlyWarningModel.load()
    return _model


def set_early_warning_model(replacement: Any) -> None:
    global _model
    _model = replacement


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: str = Field(min_length=1, max_length=64)
    as_of: str | None = None
    horizons: list[int] | None = None
    #: Supplied by a caller that has already computed them. Otherwise the
    #: materialised set for this member is used.
    features: dict[str, float] | None = None


async def stored_features(member_id: str, *, cutoff: date) -> tuple[dict[str, float], int]:
    """The materialised features at or before a day, and how stale they are."""
    async with session() as db:
        row = (
            (
                await db.execute(
                    text("""
            SELECT as_of, features FROM app_lmi.temporal_features
             WHERE member_id = :member_id AND as_of <= :cutoff
             ORDER BY as_of DESC LIMIT 1
        """),
                    {"member_id": member_id, "cutoff": cutoff},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise NotFound(
            f"no temporal features for {member_id!r} at or before {cutoff.isoformat()}; "
            "run /lmi/materialise first",
            member_id=member_id,
        )
    body = row["features"]
    return (
        json.loads(body) if isinstance(body, str) else dict(body),
        (cutoff - row["as_of"]).days,
    )


@router.post("/lmi/score", summary="A member's probability of going late, by horizon")
async def score_member(body: ScoreRequest) -> dict[str, Any]:
    """docs/07 §4.4 — the number, its interval and what moved it.

    The drivers are not decoration. A member is going to be telephoned about
    this, and "your probability is 0.32" is not a conversation.
    """
    cutoff = date.fromisoformat(body.as_of) if body.as_of else _today()

    if body.features is not None:
        features, stale_days = dict(body.features), 0
    else:
        features, stale_days = await stored_features(body.member_id, cutoff=cutoff)

    model = early_warning_model()
    if body.horizons:
        unknown = sorted(set(body.horizons) - set(model.horizons))
        if unknown:
            raise ValidationFailed(f"no model for horizon(s) {unknown}", horizons=model.horizons)

    scores = model.score(features, horizons=body.horizons)
    missing = model.missing(features)

    return {
        "member_id": body.member_id,
        "as_of": cutoff.isoformat(),
        "model_version": model.version,
        # Stated rather than left implicit: a score computed from a feature set
        # that is a fortnight old is a score about a fortnight ago, and the
        # reader has to know which.
        "stale_days": stale_days,
        # A vector assembled from half the features is a different member from
        # the one the model was trained to recognise, so the gap is named.
        "features_missing": missing,
        "scores": [score.as_dict() for score in scores],
    }


@router.get("/lmi/model", summary="Which early-warning model is serving")
async def model_version() -> dict[str, Any]:
    """What is loaded and what it was measured to do."""
    model = early_warning_model()
    metrics = model.metrics or {}
    horizons = metrics.get("horizons") or {}
    return {
        "family": "lmi_early_warning",
        "version": model.version,
        "horizons": model.horizons,
        "features": model.features,
        "measured": {
            horizon: {
                "auc": (body.get("test") or {}).get("auc"),
                "calibration_slope": (body.get("test") or {}).get("calibration_slope"),
                "interval_coverage": body.get("interval_coverage"),
                # The number that matters: discrimination among members who are
                # not already late, which is what early warning means.
                "auc_not_currently_late": (body.get("clean_only") or {}).get("auc"),
            }
            for horizon, body in horizons.items()
        },
        "survival": {
            name: {
                "concordance": (model.survival.get(name) or {}).get("concordance"),
                "events": (model.survival.get(name) or {}).get("events"),
                "hazard_ratios": (model.survival.get(name) or {}).get("hazard_ratios"),
            }
            for name in ("time_to_first_late", "time_to_cure")
            if model.survival.get(name)
        },
    }
